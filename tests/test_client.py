import calendar
import socket
import xmlrpc.client
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch
from xml.sax.saxutils import escape as xml_escape

import pytest
import requests
from urllib3.connection import HTTPConnection

from trac_mcp_server.config import Config
from trac_mcp_server.core.client import (
    TCP_KEEPALIVE_IDLE_SECONDS,
    KeepAliveHTTPAdapter,
    TicketCreateTimeout,
    TicketUpdateConflict,
    TracClient,
    WikiPageConflict,
    WikiPageExists,
    WikiPageNotModified,
    WikiPageStoredReadbackFailed,
    _keepalive_socket_options,
)
from trac_mcp_server.validators import (
    validate_content,
    validate_page_name,
)


# TestTracClient tests
def test_rpc_url_construction(mock_config):
    """Test that RPC URL is constructed correctly."""
    client = TracClient(mock_config)
    expected_url = "https://trac.example.com/trac/login/rpc"
    assert client.rpc_url == expected_url


def test_rpc_url_construction_without_trailing_slash():
    """Test RPC URL construction when base URL has no trailing slash."""
    config = Config(
        trac_url="https://trac.example.com",
        username="user",
        password="pass",
        insecure=False,
    )
    client = TracClient(config)
    expected_url = "https://trac.example.com/login/rpc"
    assert client.rpc_url == expected_url


def test_session_creation_secure(mock_config):
    """Test that session is created with correct auth and SSL verification."""
    client = TracClient(mock_config)
    assert client.session.auth == ("testuser", "testpass")
    assert client.session.verify


def test_session_creation_insecure():
    """Test that session is created with SSL verification disabled in insecure mode."""
    config = Config(
        trac_url="https://trac.example.com",
        username="user",
        password="pass",
        insecure=True,
    )
    client = TracClient(config)
    assert not client.session.verify


def test_rpc_url_uses_override_when_set():
    """rpc_url_override wins over the derived .../login/rpc path -- used
    by OIDC per-user auth, whose endpoint is not necessarily reachable
    at the same path as the default Basic/LDAP one (see mcp/oidc.py)."""
    config = Config(
        trac_url="https://trac.example.com/trac",
        username="user",
        password="pass",
        rpc_url_override="https://trac.example.com/trac-api/login/xmlrpc",
    )
    client = TracClient(config)
    assert (
        client.rpc_url
        == "https://trac.example.com/trac-api/login/xmlrpc"
    )


def test_session_uses_bearer_auth_when_token_set():
    """A bearer_token config forwards Authorization: Bearer <token>
    instead of Basic auth -- OIDC per-user auth never sends the empty
    username/password an oidc_only Config carries."""
    config = Config(
        trac_url="https://trac.example.com/trac",
        username="",
        password="",
        bearer_token="the-users-own-token",
        rpc_url_override="https://trac.example.com/trac-api/login/xmlrpc",
        oidc_only=True,
    )
    client = TracClient(config)
    assert client.session.auth is None
    assert (
        client.session.headers["Authorization"]
        == "Bearer the-users-own-token"
    )


def test_session_uses_basic_auth_when_no_bearer_token(mock_config):
    """Regression guard: the default (non-OIDC) path is unaffected --
    still plain Basic auth, no stray Authorization header."""
    client = TracClient(mock_config)
    assert client.session.auth == ("testuser", "testpass")
    assert "Authorization" not in client.session.headers


def test_session_mounts_keepalive_adapter(mock_config):
    """Both schemes get the keepalive adapter, not requests' default one."""
    client = TracClient(mock_config)
    for scheme in ("http://", "https://"):
        assert isinstance(
            client.session.adapters[scheme], KeepAliveHTTPAdapter
        )


def test_keepalive_socket_options_enable_and_tighten_keepalive():
    """SO_KEEPALIVE alone is not enough -- the idle timer must be tightened.

    A bare SO_KEEPALIVE inherits the system idle timer (7200s on Linux),
    long past the point where a NAT mapping has expired, which is the
    failure in #21/#22.
    """
    options = _keepalive_socket_options()

    assert (socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1) in options

    idle_option = getattr(socket, "TCP_KEEPIDLE", None) or getattr(
        socket, "TCP_KEEPALIVE", None
    )
    if idle_option is not None:
        idle_values = [
            value
            for level, option, value in options
            if level == socket.IPPROTO_TCP and option == idle_option
        ]
        assert idle_values == [TCP_KEEPALIVE_IDLE_SECONDS]
        assert TCP_KEEPALIVE_IDLE_SECONDS < 300, (
            "idle timer must stay well below plausible NAT idle timeouts"
        )


def test_keepalive_socket_options_preserve_urllib3_defaults():
    """Extends urllib3's defaults (TCP_NODELAY) rather than replacing them."""
    options = _keepalive_socket_options()
    for default in HTTPConnection.default_socket_options:
        assert default in options


def test_keepalive_adapter_passes_socket_options_to_poolmanager():
    """The options reach urllib3, which is what actually applies them."""
    adapter = KeepAliveHTTPAdapter()
    assert (
        adapter.poolmanager.connection_pool_kw["socket_options"]
        == _keepalive_socket_options()
    )


def test_keepalive_adapter_respects_explicit_socket_options():
    """An explicit caller value wins -- the default is a setdefault, not a clobber."""
    adapter = KeepAliveHTTPAdapter()
    custom = [(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 0)]
    adapter.init_poolmanager(1, 1, socket_options=custom)
    assert (
        adapter.poolmanager.connection_pool_kw["socket_options"]
        == custom
    )


@patch("trac_mcp_server.core.client.requests.Session.post")
def test_search_tickets_success(mock_post, mock_config):
    """Test search_tickets method with successful response."""
    # Create mock response with XML-RPC array of integers
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.content = b"""<?xml version="1.0"?>
<methodResponse>
  <params>
    <param>
      <value>
        <array>
          <data>
            <value><int>1</int></value>
            <value><int>2</int></value>
            <value><int>3</int></value>
          </data>
        </array>
      </value>
    </param>
  </params>
</methodResponse>"""
    mock_post.return_value = mock_response

    client = TracClient(mock_config)
    result = client.search_tickets("status=new&owner=testuser")

    # Verify the result
    assert result == [1, 2, 3]

    # Verify the RPC call was made correctly
    mock_post.assert_called_once()
    call_args = mock_post.call_args

    # Check URL
    assert call_args[0][0] == "https://trac.example.com/trac/login/rpc"

    # Check headers
    assert call_args[1]["headers"]["Content-Type"] == "text/xml"

    # Check that the payload contains the correct method call
    payload = call_args[1]["data"]
    assert "ticket.query" in payload
    assert "status=new&amp;owner=testuser" in payload

    # Default read timeout comes from config.rpc_timeout (60)
    assert call_args[1]["timeout"] == (10, 60)


@patch("trac_mcp_server.core.client.requests.Session.post")
def test_rpc_request_uses_configured_rpc_timeout(mock_post):
    """A custom Config.rpc_timeout is threaded into the request timeout."""
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.content = b"""<?xml version="1.0"?>
<methodResponse>
  <params>
    <param>
      <value><string>1.0</string></value>
    </param>
  </params>
</methodResponse>"""
    mock_post.return_value = mock_response

    config = Config(
        trac_url="https://trac.example.com/trac",
        username="testuser",
        password="testpass",
        rpc_timeout=120,
    )
    client = TracClient(config)
    client.validate_connection()

    assert mock_post.call_args[1]["timeout"] == (10, 120)


@patch("trac_mcp_server.core.client.requests.Session.post")
def test_search_tickets_empty_result(mock_post, mock_config):
    """Test search_tickets with no matching tickets."""
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.content = b"""<?xml version="1.0"?>
<methodResponse>
  <params>
    <param>
      <value>
        <array>
          <data>
          </data>
        </array>
      </value>
    </param>
  </params>
</methodResponse>"""
    mock_post.return_value = mock_response

    client = TracClient(mock_config)
    result = client.search_tickets("status=nonexistent")

    assert result == []


@patch("trac_mcp_server.core.client.requests.Session.post")
def test_search_tickets_fault(mock_post, mock_config):
    """Test search_tickets when server returns a fault."""
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.content = b"""<?xml version="1.0"?>
<methodResponse>
  <fault>
    <value>
      <struct>
        <member>
          <name>faultCode</name>
          <value><int>1</int></value>
        </member>
        <member>
          <name>faultString</name>
          <value><string>Invalid query</string></value>
        </member>
      </struct>
    </value>
  </fault>
</methodResponse>"""
    mock_post.return_value = mock_response

    client = TracClient(mock_config)

    with pytest.raises(Exception) as exc_info:
        client.search_tickets("invalid&query")

    # Verify it's an XML-RPC fault
    assert "Invalid query" in str(exc_info.value)


@patch("trac_mcp_server.core.client.requests.Session.post")
def test_get_ticket(mock_post, mock_config):
    """Test get_ticket method."""
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.content = b"""<?xml version="1.0"?>
<methodResponse>
  <params>
    <param>
      <value>
        <array>
          <data>
            <value><int>123</int></value>
          </data>
        </array>
      </value>
    </param>
  </params>
</methodResponse>"""
    mock_post.return_value = mock_response

    client = TracClient(mock_config)
    client.get_ticket(123)

    # Verify the RPC call
    mock_post.assert_called_once()
    payload = mock_post.call_args[1]["data"]
    assert "ticket.get" in payload


@patch("trac_mcp_server.core.client.requests.Session.post")
def test_validate_connection(mock_post, mock_config):
    """Test validate_connection method."""
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.content = b"""<?xml version="1.0"?>
<methodResponse>
  <params>
    <param>
      <value>
        <array>
          <data>
            <value><int>1</int></value>
            <value><int>3</int></value>
          </data>
        </array>
      </value>
    </param>
  </params>
</methodResponse>"""
    mock_post.return_value = mock_response

    client = TracClient(mock_config)
    result = client.validate_connection()

    # Verify the result is converted to string
    assert isinstance(result, str)

    # Verify the RPC call
    mock_post.assert_called_once()
    payload = mock_post.call_args[1]["data"]
    assert "system.getAPIVersion" in payload


@patch("trac_mcp_server.core.client.requests.Session.post")
def test_get_server_time_reads_http_date_header(mock_post, mock_config):
    """get_server_time returns the HTTP Date header of the RPC
    response, not any Trac resource's lastModified timestamp
    (ticket #33) -- the Date header reflects the server's actual
    clock at response time, while a resource's lastModified only
    reflects when that resource was last edited and goes stale the
    moment nothing on it changes.
    """
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.headers = {"Date": "Mon, 17 Aug 2026 17:06:07 GMT"}
    mock_response.content = b"""<?xml version="1.0"?>
<methodResponse>
  <params>
    <param>
      <value><string>1.0</string></value>
    </param>
  </params>
</methodResponse>"""
    mock_post.return_value = mock_response

    client = TracClient(mock_config)
    result = client.get_server_time()

    assert result == datetime(
        2026, 8, 17, 17, 6, 7, tzinfo=timezone.utc
    )

    # Uses a lightweight RPC call, not a wiki/ticket resource lookup.
    mock_post.assert_called_once()
    payload = mock_post.call_args[1]["data"]
    assert "system.getAPIVersion" in payload


@patch("trac_mcp_server.core.client.requests.Session.post")
def test_get_server_time_missing_date_header_raises(
    mock_post, mock_config
):
    """No Date header on the response surfaces as a clear error
    instead of silently returning a wrong timestamp.
    """
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.headers = {}
    mock_response.content = b"""<?xml version="1.0"?>
<methodResponse>
  <params>
    <param>
      <value><string>1.0</string></value>
    </param>
  </params>
</methodResponse>"""
    mock_post.return_value = mock_response

    client = TracClient(mock_config)
    with pytest.raises(RuntimeError, match="Date header"):
        client.get_server_time()


@patch("trac_mcp_server.core.client.requests.Session.post")
def test_create_ticket_success(mock_post, mock_config):
    """Test create_ticket method with successful response."""
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.content = b"""<?xml version="1.0"?>
<methodResponse>
  <params>
    <param>
      <value><int>42</int></value>
    </param>
  </params>
</methodResponse>"""
    mock_post.return_value = mock_response

    client = TracClient(mock_config)
    result = client.create_ticket("Test ticket", "Test description")

    # Verify returned ticket ID
    assert result == 42

    # Verify the RPC call
    mock_post.assert_called_once()
    payload = mock_post.call_args[1]["data"]
    assert "ticket.create" in payload
    assert "Test ticket" in payload
    assert "Test description" in payload
    assert "defect" in payload  # Default type


@patch("trac_mcp_server.core.client.requests.Session.post")
def test_create_ticket_with_attributes(mock_post, mock_config):
    """Test create_ticket with optional fields."""
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.content = b"""<?xml version="1.0"?>
<methodResponse>
  <params>
    <param>
      <value><int>43</int></value>
    </param>
  </params>
</methodResponse>"""
    mock_post.return_value = mock_response

    client = TracClient(mock_config)
    attributes = {
        "priority": "high",
        "component": "core",
        "milestone": "v1.0",
        "owner": "admin",
    }
    result = client.create_ticket(
        "Test ticket",
        "Test description",
        ticket_type="enhancement",
        attributes=attributes,
    )

    # Verify returned ticket ID
    assert result == 43

    # Verify the RPC call includes all fields
    payload = mock_post.call_args[1]["data"]
    assert "ticket.create" in payload
    assert "enhancement" in payload
    assert "high" in payload
    assert "core" in payload
    assert "v1.0" in payload
    assert "admin" in payload


# Trac returns creation times as compact UTC iso8601 strings; keep the test
# clock and the fixture timestamps derived from one source so they cannot drift.
_NOW_STR = "20260812T12:00:00"
_NOW_EPOCH = calendar.timegm(
    datetime.strptime(_NOW_STR, "%Y%m%dT%H:%M:%S").timetuple()
)


def _query_response(*ids):
    values = "".join(f"<value><int>{i}</int></value>" for i in ids)
    r = Mock()
    r.status_code = 200
    r.content = f"""<?xml version="1.0"?>
<methodResponse><params><param><value><array><data>{values}</data></array></value></param></params></methodResponse>""".encode()
    return r


def _ticket_get_response(
    tid,
    summary,
    created="20260812T12:00:00",
    description="Test description",
):
    summary = xml_escape(summary)
    description = xml_escape(description)
    r = Mock()
    r.status_code = 200
    r.content = f"""<?xml version="1.0"?>
<methodResponse>
  <params>
    <param>
      <value>
        <array>
          <data>
            <value><int>{tid}</int></value>
            <value><string>{created}</string></value>
            <value><string>{created}</string></value>
            <value>
              <struct>
                <member>
                  <name>summary</name>
                  <value><string>{summary}</string></value>
                </member>
                <member>
                  <name>description</name>
                  <value><string>{description}</string></value>
                </member>
              </struct>
            </value>
          </data>
        </array>
      </value>
    </param>
  </params>
</methodResponse>""".encode()
    return r


@patch("trac_mcp_server.core.client.time.time")
@patch("trac_mcp_server.core.client.requests.Session.post")
def test_create_ticket_timeout_reports_not_created(
    mock_post, mock_time, mock_config
):
    """Timeout + no matching ticket afterwards: report that it most likely
    was not created, and do NOT retry -- ticket.create is not idempotent."""
    mock_time.return_value = float(_NOW_EPOCH)
    mock_post.side_effect = [
        requests.exceptions.ReadTimeout("Read timed out."),
        _query_response(),
    ]

    client = TracClient(mock_config)

    with pytest.raises(TicketCreateTimeout) as exc_info:
        client.create_ticket("Test ticket", "Test description")

    assert exc_info.value.ticket_id is None
    assert "most likely was not created" in str(exc_info.value)
    # Crucially: no second ticket.create was issued.
    assert mock_post.call_count == 2
    assert "ticket.create" in mock_post.call_args_list[0][1]["data"]
    assert "ticket.query" in mock_post.call_args_list[1][1]["data"]


@patch("trac_mcp_server.core.client.time.time")
@patch("trac_mcp_server.core.client.requests.Session.post")
def test_create_ticket_timeout_reports_ticket_that_landed(
    mock_post, mock_time, mock_config
):
    """Timeout but the ticket did land: report its id so the caller knows
    not to retry, rather than silently returning it as a normal success."""
    mock_time.return_value = float(_NOW_EPOCH)
    mock_post.side_effect = [
        requests.exceptions.ReadTimeout("Read timed out."),
        _query_response(10),
        _ticket_get_response(10, "Test ticket", "20260812T12:00:05"),
    ]

    client = TracClient(mock_config)

    with pytest.raises(TicketCreateTimeout) as exc_info:
        client.create_ticket("Test ticket", "Test description")

    assert exc_info.value.ticket_id == 10
    assert "created anyway as #10" in str(exc_info.value)
    assert "Do not retry" in str(exc_info.value)
    assert mock_post.call_count == 3


@patch("trac_mcp_server.core.client.time.time")
@patch("trac_mcp_server.core.client.requests.Session.post")
def test_create_ticket_timeout_ignores_older_ticket_with_same_summary(
    mock_post, mock_time, mock_config
):
    """An unrelated older ticket sharing the summary must not be mistaken
    for this create -- otherwise a real create is silently skipped."""
    mock_time.return_value = float(_NOW_EPOCH)
    mock_post.side_effect = [
        requests.exceptions.ReadTimeout("Read timed out."),
        _query_response(7),
        # Same summary, but created a year earlier
        _ticket_get_response(7, "Test ticket", "20250812T12:00:00"),
    ]

    client = TracClient(mock_config)

    with pytest.raises(TicketCreateTimeout) as exc_info:
        client.create_ticket("Test ticket", "Test description")

    assert exc_info.value.ticket_id is None
    assert "most likely was not created" in str(exc_info.value)


_OIDC_CONFIG = dict(
    trac_url="https://trac.example.com/trac",
    username="",
    password="",
    bearer_token="per-user-token",
)


def _timeout_run(
    mock_post,
    mock_time,
    config,
    *,
    summary="Test ticket",
    description="Test description",
    found=(),
    attrs=None,
):
    """Run a create that times out, then answer the recovery query with
    ``found`` (``(id, summary, description, created)`` rows).

    Returns ``(query, error)``: the ticket.query string sent (``None``
    when no query was sent) and the exception create_ticket raised."""
    mock_post.reset_mock()
    mock_time.return_value = float(_NOW_EPOCH)
    responses = [
        requests.exceptions.ReadTimeout("Read timed out."),
        _query_response(*[row[0] for row in found]),
    ]
    responses += [
        _ticket_get_response(tid, summ, created, description)
        for tid, summ, description, created in found
    ]
    mock_post.side_effect = responses
    client = TracClient(config)
    with pytest.raises(
        (TicketCreateTimeout, requests.exceptions.Timeout)
    ) as exc_info:
        client.create_ticket(summary, description, attributes=attrs)
    if mock_post.call_count == 1:
        return None, exc_info.value
    (params,), method = xmlrpc.client.loads(
        mock_post.call_args_list[1][1]["data"]
    )
    assert method == "ticket.query"
    return params, exc_info.value


@patch("trac_mcp_server.core.client.time.time")
@patch("trac_mcp_server.core.client.requests.Session.post")
def test_create_ticket_timeout_query_has_summary_and_known_reporter(
    mock_post, mock_time, mock_config
):
    """Trac /trac_mcp_server #269: the recovery query is by exact summary,
    narrowed by the reporter when it is known (the configured username)."""
    params, _ = _timeout_run(mock_post, mock_time, mock_config)
    assert (
        params
        == "summary=Test ticket&reporter=testuser&order=id&desc=1&max=20"
    )


@patch("trac_mcp_server.core.client.time.time")
@patch("trac_mcp_server.core.client.requests.Session.post")
def test_create_ticket_timeout_query_uses_a_supplied_reporter(
    mock_post, mock_time, mock_config
):
    """tracrpc stores a supplied ``reporter`` as given, so it is the one
    to query, not the configured username."""
    params, _ = _timeout_run(
        mock_post,
        mock_time,
        mock_config,
        attrs={"reporter": "someone"},
    )
    assert (
        params
        == "summary=Test ticket&reporter=someone&order=id&desc=1&max=20"
    )


@pytest.mark.parametrize("attrs", [None, {"reporter": ""}])
@patch("trac_mcp_server.core.client.time.time")
@patch("trac_mcp_server.core.client.requests.Session.post")
def test_create_ticket_timeout_query_without_reporter_for_oidc(
    mock_post, mock_time, attrs
):
    """An OIDC client has username="" while Trac records the token's user
    as reporter, so a ``reporter=`` clause would match nothing and a
    landed create would be reported as lost. The summary clause stays, so
    the query is not "the newest tickets of everyone"."""
    config = Config(**_OIDC_CONFIG)
    params, error = _timeout_run(
        mock_post,
        mock_time,
        config,
        found=[
            (10, "Test ticket", "Test description", "20260812T12:00:05")
        ],
        attrs=attrs,
    )
    assert params == "summary=Test ticket&order=id&desc=1&max=20"
    assert error.ticket_id == 10
    assert "created anyway as #10" in str(error)


@patch("trac_mcp_server.core.client.time.time")
@patch("trac_mcp_server.core.client.requests.Session.post")
def test_create_ticket_timeout_query_escapes_summary_and_reporter(
    mock_post, mock_time, mock_config
):
    """Query.from_string does not URL-decode: it splits clauses on an
    unescaped ``&`` and alternatives on an unescaped ``|`` and removes
    only the ``\\&`` and ``\\|`` escapes."""
    params, _ = _timeout_run(
        mock_post,
        mock_time,
        mock_config,
        summary="a&b|c",
        attrs={"reporter": "x&y|z"},
    )
    assert params == (
        "summary=a\\&b\\|c&reporter=x\\&y\\|z&order=id&desc=1&max=20"
    )


@patch("trac_mcp_server.core.client.time.time")
@patch("trac_mcp_server.core.client.requests.Session.post")
def test_create_ticket_timeout_ignores_same_summary_other_description(
    mock_post, mock_time, mock_config
):
    """Another ticket (another user's, say) with the same summary but a
    different description is not this create: matching on the summary
    alone would say "created anyway, do not retry" for a create that
    never landed."""
    params, error = _timeout_run(
        mock_post,
        mock_time,
        mock_config,
        found=[
            (
                7,
                "Test ticket",
                "A different description",
                "20260812T12:00:05",
            )
        ],
    )
    assert error.ticket_id is None
    assert "most likely was not created" in str(error)


@pytest.mark.parametrize(
    "sent", ["Test ticket ", " Test ticket", "\t Test ticket \n"]
)
@patch("trac_mcp_server.core.client.time.time")
@patch("trac_mcp_server.core.client.requests.Session.post")
def test_create_ticket_timeout_matches_the_summary_as_trac_stores_it(
    mock_post, mock_time, mock_config, sent
):
    """Trac strips a summary on create (``Ticket.__setitem__`` calls
    ``value.strip()`` for every field that is not a textarea; observed
    live on /trac_test with a trailing space). The recovery queries and
    compares the stripped summary, else a create that landed is
    reported as lost and a retry makes a duplicate."""
    params, error = _timeout_run(
        mock_post,
        mock_time,
        mock_config,
        summary=sent,
        found=[
            (10, "Test ticket", "Test description", "20260812T12:00:05")
        ],
    )
    assert (
        params
        == "summary=Test ticket&reporter=testuser&order=id&desc=1&max=20"
    )
    assert error.ticket_id == 10


@patch("trac_mcp_server.core.client.time.time")
@patch("trac_mcp_server.core.client.requests.Session.post")
def test_create_ticket_timeout_strips_a_supplied_reporter_like_trac(
    mock_post, mock_time, mock_config
):
    """The same ``Ticket.__setitem__`` strip applies to ``reporter``."""
    params, _ = _timeout_run(
        mock_post,
        mock_time,
        mock_config,
        attrs={"reporter": " someone "},
    )
    assert (
        params
        == "summary=Test ticket&reporter=someone&order=id&desc=1&max=20"
    )


@patch("trac_mcp_server.core.client.time.time")
@patch("trac_mcp_server.core.client.requests.Session.post")
def test_create_ticket_timeout_whitespace_reporter_has_no_clause(
    mock_post, mock_time, mock_config
):
    """A reporter that strips to nothing is stored empty: unknown."""
    params, _ = _timeout_run(
        mock_post, mock_time, mock_config, attrs={"reporter": "  "}
    )
    assert params == "summary=Test ticket&order=id&desc=1&max=20"


@pytest.mark.parametrize(
    "description", ["body\n", "  body  ", "line one\n\nline two\n"]
)
@patch("trac_mcp_server.core.client.time.time")
@patch("trac_mcp_server.core.client.requests.Session.post")
def test_create_ticket_timeout_compares_the_description_unstripped(
    mock_post, mock_time, mock_config, description
):
    """The description is a textarea: Trac keeps it as sent (no strip),
    so it is compared exactly, whitespace included."""
    _, error = _timeout_run(
        mock_post,
        mock_time,
        mock_config,
        description=description,
        found=[(10, "Test ticket", description, "20260812T12:00:05")],
    )
    assert error.ticket_id == 10
    _, error = _timeout_run(
        mock_post,
        mock_time,
        mock_config,
        description=description,
        found=[
            (
                10,
                "Test ticket",
                description.strip(),
                "20260812T12:00:05",
            )
        ],
    )
    assert error.ticket_id is None


@pytest.mark.parametrize(
    ("sent", "stored"),
    [
        ("one\r\ntwo\r\n", "one\ntwo\n"),
        ("one\rtwo\r", "one\ntwo\n"),
        ("a\r\n\r\nb\rc\nd", "a\n\nb\nc\nd"),
    ],
    ids=["crlf", "lone-cr", "mixed"],
)
@patch("trac_mcp_server.core.client.time.time")
@patch("trac_mcp_server.core.client.requests.Session.post")
def test_create_ticket_timeout_compares_descriptions_with_eol_normalised(
    mock_post, mock_time, mock_config, sent, stored
):
    """The XML-RPC request is XML, whose end-of-line handling turns
    ``\\r\\n`` and a lone ``\\r`` into ``\\n`` before Trac sees the text
    (observed live on /trac_test: a CRLF description is stored with LF).
    A sent CR/CRLF description must match its stored LF form."""
    _, error = _timeout_run(
        mock_post,
        mock_time,
        mock_config,
        description=sent,
        found=[(10, "Test ticket", stored, "20260812T12:00:05")],
    )
    assert error.ticket_id == 10


@patch("trac_mcp_server.core.client.time.time")
@patch("trac_mcp_server.core.client.requests.Session.post")
def test_create_ticket_timeout_eol_normalising_keeps_other_differences(
    mock_post, mock_time, mock_config
):
    """Only line endings are folded: other differences still mismatch."""
    _, error = _timeout_run(
        mock_post,
        mock_time,
        mock_config,
        description="one\r\ntwo\r\n",
        found=[(10, "Test ticket", "one\ntwo", "20260812T12:00:05")],
    )
    assert error.ticket_id is None


@pytest.mark.parametrize("summary", ["a\\b", "cost $USER", "$USER"])
@patch("trac_mcp_server.core.client.time.time")
@patch("trac_mcp_server.core.client.requests.Session.post")
def test_create_ticket_timeout_unqueryable_summary_reraises_timeout(
    mock_post, mock_time, mock_config, summary
):
    """Trac has no escape for a backslash and substitutes a literal
    ``$USER``, so such a summary cannot be queried exactly: the outcome
    is unknown, the original timeout is surfaced, and no query is sent."""
    params, error = _timeout_run(
        mock_post, mock_time, mock_config, summary=summary
    )
    assert params is None
    assert mock_post.call_count == 1
    assert isinstance(error, requests.exceptions.ReadTimeout)
    assert not isinstance(error, TicketCreateTimeout)


@pytest.mark.parametrize("reporter", ["a\\b", "$USER", "me$USER"])
@patch("trac_mcp_server.core.client.time.time")
@patch("trac_mcp_server.core.client.requests.Session.post")
def test_create_ticket_timeout_unqueryable_reporter_is_dropped(
    mock_post, mock_time, mock_config, reporter
):
    """An unescapable reporter only loses its clause; the summary and
    description still decide."""
    params, error = _timeout_run(
        mock_post,
        mock_time,
        mock_config,
        attrs={"reporter": reporter},
        found=[
            (10, "Test ticket", "Test description", "20260812T12:00:05")
        ],
    )
    assert params == "summary=Test ticket&order=id&desc=1&max=20"
    assert error.ticket_id == 10


@pytest.mark.live
class TestFindRecentlyCreatedTicketLive:
    """Requires --run-live and TRAC_URL/USERNAME/PASSWORD. Creates a few
    throwaway tickets on /trac_test and deletes each in a ``finally``.

    Trac /trac_mcp_server #269: the create-timeout recovery rests on how
    Trac parses ``summary=<value>`` (exact match, ``\\&`` and ``\\|``
    escapes) in a ``ticket.query`` string. That was reasoned from Trac's
    source; these rows observe it, with a real create and the real
    finder, called directly with the ``sent_at`` a timed-out create
    would have noted.
    """

    @staticmethod
    def _create(client, summary, description):
        import time

        sent_at = time.time()
        return client.create_ticket(summary, description), sent_at

    def test_finds_a_ticket_with_ampersand_and_pipe_in_the_summary(
        self,
    ):
        import uuid

        from tests.live_helpers import trac_test_client

        client = trac_test_client()
        run = uuid.uuid4().hex[:8]
        summary = f"t269 a & b | c {run}"
        description = f"Ticket 269 live finder row, run {run}."
        tid = None
        try:
            tid, sent_at = self._create(client, summary, description)
            # An OIDC caller's reporter is unknown (""): no reporter
            # clause; and a known one: both clauses.
            assert (
                client._find_recently_created_ticket(
                    summary, description, "", sent_at
                )
                == tid
            )
            assert (
                client._find_recently_created_ticket(
                    summary,
                    description,
                    client.config.username,
                    sent_at,
                )
                == tid
            )
        finally:
            if tid is not None:
                client.delete_ticket(tid)

    def test_a_description_differing_by_one_character_is_no_match(self):
        import uuid

        from tests.live_helpers import trac_test_client

        client = trac_test_client()
        run = uuid.uuid4().hex[:8]
        summary = f"t269 a & b | c {run}"
        description = f"Ticket 269 live finder row, run {run}."
        tid = None
        try:
            tid, sent_at = self._create(client, summary, description)
            for reporter in ("", client.config.username):
                assert (
                    client._find_recently_created_ticket(
                        summary, description + "x", reporter, sent_at
                    )
                    is None
                )
        finally:
            if tid is not None:
                client.delete_ticket(tid)

    @pytest.mark.parametrize(
        "pad", ["trailing", "leading"], ids=["trailing", "leading"]
    )
    def test_a_summary_with_edge_whitespace_is_found_as_trac_stores_it(
        self, pad
    ):
        """Trac strips a summary on create (``Ticket.__setitem__``,
        ``value.strip()``; observed live with a trailing space), so the
        finder must query and compare the stripped summary. The stored
        value is in the failure message."""
        import uuid

        from tests.live_helpers import trac_test_client

        client = trac_test_client()
        run = uuid.uuid4().hex[:8]
        core = f"t269 {pad} space {run}"
        summary = core + " " if pad == "trailing" else " " + core
        description = f"Ticket 269 live finder row, run {run}."
        tid = None
        try:
            tid, sent_at = self._create(client, summary, description)
            stored = client.get_ticket(tid)[3]["summary"]
            found = client._find_recently_created_ticket(
                summary, description, "", sent_at
            )
            assert found == tid, (
                f"Trac stored the summary as {stored!r} (sent "
                f"{summary!r}); the finder returned {found!r}"
            )
        finally:
            if tid is not None:
                client.delete_ticket(tid)

    @pytest.mark.parametrize(
        "ending",
        ["\n", "  ", "\r\n", "\r\nsecond line\r\n"],
        ids=["lf", "spaces", "crlf", "crlf-lines"],
    )
    def test_a_description_is_found_as_trac_stores_it(self, ending):
        """Trac's source keeps a description as sent (a textarea is not
        strip()ped), so the finder compares it exactly. A ``\\r`` in it
        is the open question: Python's XML-RPC client sends it raw, and
        an XML parser on the server may fold ``\\r\\n`` to ``\\n`` before
        Trac sees it. The stored value is in the failure message; the
        row asserts the finder returns the id."""
        import uuid

        from tests.live_helpers import trac_test_client

        client = trac_test_client()
        run = uuid.uuid4().hex[:8]
        summary = f"t269 description ending {run}"
        description = f"Ticket 269 live finder row, run {run}.{ending}"
        tid = None
        try:
            tid, sent_at = self._create(client, summary, description)
            stored = client.get_ticket(tid)[3]["description"]
            found = client._find_recently_created_ticket(
                summary, description, "", sent_at
            )
            assert found == tid, (
                f"Trac stored the description as {stored!r} (sent "
                f"{description!r}); the finder returned {found!r}"
            )
        finally:
            if tid is not None:
                client.delete_ticket(tid)


@patch("trac_mcp_server.core.client.requests.Session.post")
def test_create_ticket_timeout_reraises_when_verification_fails(
    mock_post, mock_config
):
    """If the post-timeout verification search itself fails, don't guess:
    surface the original timeout rather than risk a duplicate create."""
    mock_post.side_effect = [
        requests.exceptions.ReadTimeout("Read timed out."),
        requests.exceptions.ConnectionError("connection reset"),
    ]

    client = TracClient(mock_config)

    with pytest.raises(requests.exceptions.ReadTimeout):
        client.create_ticket("Test ticket", "Test description")

    # No retry create call was made after the failed verification.
    assert mock_post.call_count == 2


def test_create_ticket_empty_summary(mock_config):
    """Test create_ticket validation with empty summary."""
    client = TracClient(mock_config)

    with pytest.raises(ValueError) as exc_info:
        client.create_ticket("", "Test description")

    assert "Summary is required" in str(exc_info.value)


def test_create_ticket_empty_description(mock_config):
    """Test create_ticket validation with empty description."""
    client = TracClient(mock_config)

    with pytest.raises(ValueError) as exc_info:
        client.create_ticket("Test ticket", "")

    assert "Description is required" in str(exc_info.value)


@patch("trac_mcp_server.core.client.requests.Session.post")
def test_update_ticket_success(mock_post, mock_config):
    """Test update_ticket method with successful get + update sequence."""
    # First mock response for ticket.get
    get_response = Mock()
    get_response.status_code = 200
    get_response.content = b"""<?xml version="1.0"?>
<methodResponse>
  <params>
    <param>
      <value>
        <array>
          <data>
            <value><int>42</int></value>
            <value><int>1234567890</int></value>
            <value><int>1234567900</int></value>
            <value>
              <struct>
                <member>
                  <name>_ts</name>
                  <value><string>1234567900</string></value>
                </member>
                <member>
                  <name>status</name>
                  <value><string>new</string></value>
                </member>
              </struct>
            </value>
          </data>
        </array>
      </value>
    </param>
  </params>
</methodResponse>"""

    # Second mock response for ticket.update
    update_response = Mock()
    update_response.status_code = 200
    update_response.content = b"""<?xml version="1.0"?>
<methodResponse>
  <params>
    <param>
      <value>
        <array>
          <data>
            <value><int>42</int></value>
            <value><int>1234567890</int></value>
            <value><int>1234567910</int></value>
            <value>
              <struct>
                <member>
                  <name>_ts</name>
                  <value><string>1234567910</string></value>
                </member>
                <member>
                  <name>status</name>
                  <value><string>accepted</string></value>
                </member>
              </struct>
            </value>
          </data>
        </array>
      </value>
    </param>
  </params>
</methodResponse>"""

    # Set up mock to return different responses in sequence
    mock_post.side_effect = [get_response, update_response]

    client = TracClient(mock_config)
    _ = client.update_ticket(42, attributes={"status": "accepted"})

    # Verify both RPC calls were made
    assert mock_post.call_count == 2

    # Verify first call was ticket.get
    first_call_payload = mock_post.call_args_list[0][1]["data"]
    assert "ticket.get" in first_call_payload

    # Verify second call was ticket.update
    second_call_payload = mock_post.call_args_list[1][1]["data"]
    assert "ticket.update" in second_call_payload
    assert "accepted" in second_call_payload


@patch("trac_mcp_server.core.client.requests.Session.post")
def test_update_ticket_with_comment(mock_post, mock_config):
    """Test update_ticket with comment and field update."""
    # First mock response for ticket.get
    get_response = Mock()
    get_response.status_code = 200
    get_response.content = b"""<?xml version="1.0"?>
<methodResponse>
  <params>
    <param>
      <value>
        <array>
          <data>
            <value><int>42</int></value>
            <value><int>1234567890</int></value>
            <value><int>1234567900</int></value>
            <value>
              <struct>
                <member>
                  <name>_ts</name>
                  <value><string>1234567900</string></value>
                </member>
                <member>
                  <name>status</name>
                  <value><string>new</string></value>
                </member>
              </struct>
            </value>
          </data>
        </array>
      </value>
    </param>
  </params>
</methodResponse>"""

    # Second mock response for ticket.update
    update_response = Mock()
    update_response.status_code = 200
    update_response.content = b"""<?xml version="1.0"?>
<methodResponse>
  <params>
    <param>
      <value>
        <array>
          <data>
            <value><int>42</int></value>
          </data>
        </array>
      </value>
    </param>
  </params>
</methodResponse>"""

    mock_post.side_effect = [get_response, update_response]

    client = TracClient(mock_config)
    _ = client.update_ticket(
        42,
        comment="Changed status to accepted",
        attributes={"status": "accepted"},
    )

    # Verify second call includes comment
    second_call_payload = mock_post.call_args_list[1][1]["data"]
    assert "ticket.update" in second_call_payload
    assert "Changed status to accepted" in second_call_payload
    assert "accepted" in second_call_payload


@patch("trac_mcp_server.core.client.requests.Session.post")
def test_update_ticket_comment_too_long(mock_post, mock_config):
    """Test update_ticket validation with comment exceeding 10k limit."""
    # First mock response for ticket.get
    get_response = Mock()
    get_response.status_code = 200
    get_response.content = b"""<?xml version="1.0"?>
<methodResponse>
  <params>
    <param>
      <value>
        <array>
          <data>
            <value><int>42</int></value>
            <value><int>1234567890</int></value>
            <value><int>1234567900</int></value>
            <value>
              <struct>
                <member>
                  <name>_ts</name>
                  <value><string>1234567900</string></value>
                </member>
              </struct>
            </value>
          </data>
        </array>
      </value>
    </param>
  </params>
</methodResponse>"""

    mock_post.return_value = get_response

    client = TracClient(mock_config)

    # Create comment exceeding 10000 characters
    long_comment = "x" * 10001

    with pytest.raises(ValueError) as exc_info:
        client.update_ticket(42, comment=long_comment)

    assert "Comment exceeds maximum length" in str(exc_info.value)


@patch("trac_mcp_server.core.client.requests.Session.post")
def test_update_ticket_without_base_ts_mints_own_token(
    mock_post, mock_config
):
    """Without base_ts, update_ticket forwards whatever ticket.get returns
    right before the write. This is the legacy fallback path -- it cannot
    reject anything, since the token it sends is read microseconds before
    it's used (ticket #50). See test_update_ticket_base_ts_conflict_raises
    below for the path that can actually reject a stale write."""
    # First mock response for ticket.get with specific _ts
    get_response = Mock()
    get_response.status_code = 200
    get_response.content = b"""<?xml version="1.0"?>
<methodResponse>
  <params>
    <param>
      <value>
        <array>
          <data>
            <value><int>42</int></value>
            <value><int>1234567890</int></value>
            <value><int>1234567900</int></value>
            <value>
              <struct>
                <member>
                  <name>_ts</name>
                  <value><string>SPECIFIC_TIMESTAMP_VALUE</string></value>
                </member>
              </struct>
            </value>
          </data>
        </array>
      </value>
    </param>
  </params>
</methodResponse>"""

    # Second mock response for ticket.update
    update_response = Mock()
    update_response.status_code = 200
    update_response.content = b"""<?xml version="1.0"?>
<methodResponse>
  <params>
    <param>
      <value>
        <array>
          <data>
            <value><int>42</int></value>
          </data>
        </array>
      </value>
    </param>
  </params>
</methodResponse>"""

    mock_post.side_effect = [get_response, update_response]

    client = TracClient(mock_config)
    _ = client.update_ticket(42, attributes={"priority": "high"})

    # Verify the _ts value from get was passed to update
    second_call_payload = mock_post.call_args_list[1][1]["data"]
    assert "SPECIFIC_TIMESTAMP_VALUE" in second_call_payload


@patch("trac_mcp_server.core.client.requests.Session.post")
def test_update_ticket_with_base_ts_skips_extra_get(
    mock_post, mock_config
):
    """When the caller supplies base_ts, it is forwarded verbatim as _ts
    and no preliminary ticket.get is made -- there is nothing left for
    that read to accomplish once the caller's own token is used."""
    update_response = Mock()
    update_response.status_code = 200
    update_response.content = b"""<?xml version="1.0"?>
<methodResponse>
  <params>
    <param>
      <value>
        <array>
          <data>
            <value><int>42</int></value>
          </data>
        </array>
      </value>
    </param>
  </params>
</methodResponse>"""
    mock_post.return_value = update_response

    client = TracClient(mock_config)
    _ = client.update_ticket(
        42, attributes={"priority": "high"}, base_ts=1788093861682305
    )

    assert mock_post.call_count == 1
    payload = mock_post.call_args[1]["data"]
    assert "ticket.update" in payload
    assert "1788093861682305" in payload


@patch("trac_mcp_server.core.client.requests.Session.post")
def test_update_ticket_base_ts_conflict_raises(mock_post, mock_config):
    """Seeded-defect regression for ticket #50: a stale base_ts must be
    rejected. This reproduces Trac's real mid-air-collision fault (a
    concurrent comment landed after base_ts) and asserts update_ticket
    turns it into a TicketUpdateConflict naming the comment's author,
    rather than either swallowing the fault or re-raising the bare
    xmlrpc.client.Fault. Before base_ts existed, nothing could make this
    fault fire at all -- the client always minted a token that matched by
    construction."""
    update_fault_response = Mock()
    update_fault_response.status_code = 200
    update_fault_response.content = b"""<?xml version="1.0"?>
<methodResponse>
  <fault>
    <value>
      <struct>
        <member>
          <name>faultCode</name>
          <value><int>1</int></value>
        </member>
        <member>
          <name>faultString</name>
          <value><string>Your changes have not been saved because this ticket has been modified since you started editing.</string></value>
        </member>
      </struct>
    </value>
  </fault>
</methodResponse>"""

    changelog_response = Mock()
    changelog_response.status_code = 200
    changelog_response.content = b"""<?xml version="1.0"?>
<methodResponse>
  <params>
    <param>
      <value>
        <array>
          <data>
            <value>
              <array>
                <data>
                  <value><dateTime.iso8601>20260830T12:44:51</dateTime.iso8601></value>
                  <value><string>operator</string></value>
                  <value><string>comment</string></value>
                  <value><string>1</string></value>
                  <value><string>Actually, hold off on this.</string></value>
                  <value><int>1</int></value>
                </data>
              </array>
            </value>
          </data>
        </array>
      </value>
    </param>
  </params>
</methodResponse>"""

    mock_post.side_effect = [update_fault_response, changelog_response]

    client = TracClient(mock_config)

    with pytest.raises(TicketUpdateConflict) as exc_info:
        client.update_ticket(
            42,
            attributes={"status": "accepted"},
            base_ts=1788093861682305,
        )

    err = exc_info.value
    assert err.ticket_id == 42
    assert err.base_ts == 1788093861682305
    assert len(err.changes) == 1
    assert err.changes[0]["author"] == "operator"
    assert err.changes[0]["field"] == "comment"
    assert "operator" in str(err)

    # Only two calls: the failed update, then the changelog fetch used to
    # explain the conflict -- no preliminary ticket.get, per the base_ts
    # path above.
    assert mock_post.call_count == 2
    assert "ticket.update" in mock_post.call_args_list[0][1]["data"]
    assert "ticket.changeLog" in mock_post.call_args_list[1][1]["data"]


@patch("trac_mcp_server.core.client.requests.Session.post")
def test_update_ticket_base_ts_conflict_without_changelog_detail(
    mock_post, mock_config
):
    """If the changelog fetch used to explain a conflict itself fails, the
    conflict is still raised (never silently swallowed into the bare
    Fault) -- just without the per-change detail."""
    update_fault_response = Mock()
    update_fault_response.status_code = 200
    update_fault_response.content = b"""<?xml version="1.0"?>
<methodResponse>
  <fault>
    <value>
      <struct>
        <member>
          <name>faultCode</name>
          <value><int>1</int></value>
        </member>
        <member>
          <name>faultString</name>
          <value><string>Your changes have not been saved because this ticket has been modified since you started editing.</string></value>
        </member>
      </struct>
    </value>
  </fault>
</methodResponse>"""

    changelog_fault_response = Mock()
    changelog_fault_response.status_code = 200
    changelog_fault_response.content = b"""<?xml version="1.0"?>
<methodResponse>
  <fault>
    <value>
      <struct>
        <member>
          <name>faultCode</name>
          <value><int>404</int></value>
        </member>
        <member>
          <name>faultString</name>
          <value><string>Ticket does not exist</string></value>
        </member>
      </struct>
    </value>
  </fault>
</methodResponse>"""

    mock_post.side_effect = [
        update_fault_response,
        changelog_fault_response,
    ]

    client = TracClient(mock_config)

    with pytest.raises(TicketUpdateConflict) as exc_info:
        client.update_ticket(
            42,
            attributes={"status": "accepted"},
            base_ts=1788093861682305,
        )

    assert exc_info.value.changes == []
    assert "42" in str(exc_info.value)


@patch("trac_mcp_server.core.client.requests.Session.post")
def test_update_ticket_base_ts_unrelated_fault_reraises(
    mock_post, mock_config
):
    """A base_ts caller still sees an ordinary Fault (e.g. ticket not
    found) as a plain xmlrpc.client.Fault, not misreported as a version
    conflict -- only Trac's specific mid-air-collision wording is
    reinterpreted."""
    import xmlrpc.client

    fault_response = Mock()
    fault_response.status_code = 200
    fault_response.content = b"""<?xml version="1.0"?>
<methodResponse>
  <fault>
    <value>
      <struct>
        <member>
          <name>faultCode</name>
          <value><int>404</int></value>
        </member>
        <member>
          <name>faultString</name>
          <value><string>Ticket does not exist</string></value>
        </member>
      </struct>
    </value>
  </fault>
</methodResponse>"""
    mock_post.return_value = fault_response

    client = TracClient(mock_config)

    with pytest.raises(xmlrpc.client.Fault) as exc_info:
        client.update_ticket(
            42,
            attributes={"status": "accepted"},
            base_ts=1788093861682305,
        )

    assert "does not exist" in str(exc_info.value)


@patch("trac_mcp_server.core.client.requests.Session.post")
def test_delete_ticket_success(mock_post, mock_config):
    """Test delete_ticket method with successful response."""
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.content = b"""<?xml version="1.0"?>
<methodResponse>
  <params>
    <param>
      <value><boolean>1</boolean></value>
    </param>
  </params>
</methodResponse>"""
    mock_post.return_value = mock_response

    client = TracClient(mock_config)
    result = client.delete_ticket(42)

    assert result is True
    # Verify the RPC call was made with ticket.delete
    call_data = mock_post.call_args[1]["data"]
    assert "ticket.delete" in call_data


# TestTracClientWiki tests
@patch("trac_mcp_server.core.client.requests.Session.post")
def test_list_wiki_pages_success(mock_post, mock_config):
    """Test list_wiki_pages method with successful response."""
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.content = b"""<?xml version="1.0"?>
<methodResponse>
  <params>
    <param>
      <value>
        <array>
          <data>
            <value><string>WikiStart</string></value>
            <value><string>UserGuide</string></value>
            <value><string>API/Reference</string></value>
          </data>
        </array>
      </value>
    </param>
  </params>
</methodResponse>"""
    mock_post.return_value = mock_response

    client = TracClient(mock_config)
    result = client.list_wiki_pages()

    # Verify the result
    assert result == ["WikiStart", "UserGuide", "API/Reference"]

    # Verify the RPC call
    mock_post.assert_called_once()
    payload = mock_post.call_args[1]["data"]
    assert "wiki.getAllPages" in payload


@patch("trac_mcp_server.core.client.requests.Session.post")
def test_list_wiki_pages_empty(mock_post, mock_config):
    """Test list_wiki_pages with no pages."""
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.content = b"""<?xml version="1.0"?>
<methodResponse>
  <params>
    <param>
      <value>
        <array>
          <data>
          </data>
        </array>
      </value>
    </param>
  </params>
</methodResponse>"""
    mock_post.return_value = mock_response

    client = TracClient(mock_config)
    result = client.list_wiki_pages()

    assert result == []


@patch("trac_mcp_server.core.client.requests.Session.post")
def test_get_wiki_page_success(mock_post, mock_config):
    """Test get_wiki_page method with successful response."""
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.content = b"""<?xml version="1.0"?>
<methodResponse>
  <params>
    <param>
      <value><string>= Page Title =
Page content here</string></value>
    </param>
  </params>
</methodResponse>"""
    mock_post.return_value = mock_response

    client = TracClient(mock_config)
    result = client.get_wiki_page("TestPage")

    # Verify raw TracWiki returned
    assert result == "= Page Title =\nPage content here"

    # Verify RPC call
    mock_post.assert_called_once()
    payload = mock_post.call_args[1]["data"]
    assert "wiki.getPage" in payload
    assert "TestPage" in payload


@patch("trac_mcp_server.core.client.requests.Session.post")
def test_get_wiki_page_with_version(mock_post, mock_config):
    """Test get_wiki_page with version parameter."""
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.content = b"""<?xml version="1.0"?>
<methodResponse>
  <params>
    <param>
      <value><string>Historical content</string></value>
    </param>
  </params>
</methodResponse>"""
    mock_post.return_value = mock_response

    client = TracClient(mock_config)
    result = client.get_wiki_page("PageName", version=2)

    assert result == "Historical content"

    # Verify RPC call to getPageVersion
    mock_post.assert_called_once()
    payload = mock_post.call_args[1]["data"]
    assert "wiki.getPageVersion" in payload
    assert "PageName" in payload
    assert "<int>2</int>" in payload


@patch("trac_mcp_server.core.client.requests.Session.post")
def test_get_wiki_page_not_found(mock_post, mock_config):
    """Test get_wiki_page when page doesn't exist."""
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.content = b"""<?xml version="1.0"?>
<methodResponse>
  <fault>
    <value>
      <struct>
        <member>
          <name>faultCode</name>
          <value><int>1</int></value>
        </member>
        <member>
          <name>faultString</name>
          <value><string>No such page</string></value>
        </member>
      </struct>
    </value>
  </fault>
</methodResponse>"""
    mock_post.return_value = mock_response

    client = TracClient(mock_config)

    with pytest.raises(Exception) as exc_info:
        client.get_wiki_page("NonExistentPage")

    # Verify it's an XML-RPC fault
    assert "No such page" in str(exc_info.value)


@patch("trac_mcp_server.core.client.requests.Session.post")
def test_get_wiki_page_info_success(mock_post, mock_config):
    """Test get_wiki_page_info method."""
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.content = b"""<?xml version="1.0"?>
<methodResponse>
  <params>
    <param>
      <value>
        <struct>
          <member>
            <name>name</name>
            <value><string>TestPage</string></value>
          </member>
          <member>
            <name>author</name>
            <value><string>admin</string></value>
          </member>
          <member>
            <name>version</name>
            <value><int>3</int></value>
          </member>
          <member>
            <name>lastModified</name>
            <value><int>1234567890</int></value>
          </member>
        </struct>
      </value>
    </param>
  </params>
</methodResponse>"""
    mock_post.return_value = mock_response

    client = TracClient(mock_config)
    result = client.get_wiki_page_info("TestPage")

    # Verify dict structure
    assert isinstance(result, dict)
    assert result["name"] == "TestPage"
    assert result["author"] == "admin"
    assert result["version"] == 3
    assert result["lastModified"] == 1234567890

    # Verify RPC call
    mock_post.assert_called_once()
    payload = mock_post.call_args[1]["data"]
    assert "wiki.getPageInfo" in payload


@patch("trac_mcp_server.core.client.requests.Session.post")
def test_search_wiki_pages_by_title(mock_post, mock_config):
    """Test search_wiki_pages_by_title method."""
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.content = b"""<?xml version="1.0"?>
<methodResponse>
  <params>
    <param>
      <value>
        <array>
          <data>
            <value><string>WikiStart</string></value>
            <value><string>APIGuide</string></value>
            <value><string>UserAPI</string></value>
            <value><string>AdminGuide</string></value>
          </data>
        </array>
      </value>
    </param>
  </params>
</methodResponse>"""
    mock_post.return_value = mock_response

    client = TracClient(mock_config)
    result = client.search_wiki_pages_by_title("API")

    # Verify results contain matching pages
    assert len(result) == 2
    names = [r["name"] for r in result]
    assert "APIGuide" in names
    assert "UserAPI" in names

    # Verify snippets present
    for r in result:
        assert "snippet" in r
        assert "name" in r


def _rpc_result_response(value) -> Mock:
    """A mocked HTTP response carrying ``value`` as an XML-RPC result."""
    response = Mock()
    response.status_code = 200
    response.content = xmlrpc.client.dumps(
        (value,), methodresponse=True
    ).encode()
    return response


@patch("trac_mcp_server.core.client.requests.Session.post")
def test_search_wiki_pages(mock_post, mock_config):
    """search_wiki_pages over the real transport: getRecentChanges (the
    listing of every page), then one system.multicall carrying getPageInfo and getPage for every page.
    Page2's fault entry is skipped; an empty page comes back as an empty
    <string/> and is cached without a match."""
    stamp = xmlrpc.client.DateTime("20260101T00:00:01")

    def info(name, version=1):
        return {
            "name": name,
            "author": "a",
            "version": version,
            "lastModified": stamp,
        }

    fault = {"faultCode": 1, "faultString": "Page not readable"}
    listing = [info(f"Page{i}") for i in range(1, 5)]
    mock_post.side_effect = [
        _rpc_result_response(listing),
        _rpc_result_response(
            [
                [info("Page1")],
                ["no match here"],
                [info("Page2")],
                fault,
                [info("Page3")],
                ["found the target keyword here"],
                [info("Page4")],
                [""],
            ]
        ),
    ]

    client = TracClient(mock_config)
    result = client.search_wiki_pages("target")

    # Only Page3 matches: Page2 is skipped, Page4 is empty
    assert len(result) == 1
    assert result[0]["name"] == "Page3"
    assert result[0]["score"] > 0
    assert "target" in result[0]["snippet"]
    assert mock_post.call_count == 2
    first_request = xmlrpc.client.loads(
        mock_post.call_args_list[0].kwargs["data"]
    )
    assert first_request[1] == "wiki.getRecentChanges"
    sent = mock_post.call_args_list[1].kwargs["data"]
    (calls,), method = xmlrpc.client.loads(sent)
    assert method == "system.multicall"
    assert [c["methodName"] for c in calls[:4]] == [
        "wiki.getPageInfo",
        "wiki.getPage",
        "wiki.getPageInfo",
        "wiki.getPage",
    ]
    assert calls[0]["params"] == ["Page1"]

    # A second search: the cache refresh is getRecentChanges and one
    # multicall for Page2, which was unreadable and is retried.
    mock_post.reset_mock()
    mock_post.side_effect = [
        _rpc_result_response(listing),
        _rpc_result_response(
            [[info("Page2")], ["now it has the target too"]]
        ),
    ]
    again = client.search_wiki_pages("target")
    assert sorted(h["name"] for h in again) == ["Page2", "Page3"]
    methods = [
        xmlrpc.client.loads(c.kwargs["data"])[1]
        for c in mock_post.call_args_list
    ]
    assert methods == ["wiki.getRecentChanges", "system.multicall"]


@patch("trac_mcp_server.core.client.requests.Session.post")
def test_search_wiki_pages_max_results(mock_post, mock_config):
    """Test search_wiki_pages_by_title respects max_results."""
    # Create response with many matching pages
    pages = [f"TestPage{i}" for i in range(20)]
    pages_xml = "".join(
        [f"<value><string>{p}</string></value>" for p in pages]
    )

    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.content = f"""<?xml version="1.0"?>
<methodResponse>
  <params>
    <param>
      <value>
        <array>
          <data>
            {pages_xml}
          </data>
        </array>
      </value>
    </param>
  </params>
</methodResponse>""".encode()
    mock_post.return_value = mock_response

    client = TracClient(mock_config)
    result = client.search_wiki_pages_by_title(
        "TestPage", max_results=5
    )

    # Verify only 5 results returned
    assert len(result) == 5


# TestValidators tests
def test_validate_page_name_valid():
    """Test validate_page_name with valid page names."""
    # Simple page name
    is_valid, msg = validate_page_name("WikiStart")
    assert is_valid
    assert msg == ""

    # Page with path
    is_valid, msg = validate_page_name("User/Guide")
    assert is_valid
    assert msg == ""

    # Page with underscores
    is_valid, msg = validate_page_name("API_Reference")
    assert is_valid
    assert msg == ""


def test_validate_page_name_empty():
    """Test validate_page_name with empty and whitespace-only names."""
    # Empty string
    is_valid, msg = validate_page_name("")
    assert not is_valid
    assert "cannot be empty" in msg

    # Whitespace only
    is_valid, msg = validate_page_name("   ")
    assert not is_valid
    assert "cannot be empty" in msg


def test_validate_page_name_path_traversal():
    """Test validate_page_name rejects path traversal attempts."""
    is_valid, msg = validate_page_name("../etc/passwd")
    assert not is_valid
    assert ".." in msg

    is_valid, msg = validate_page_name("Page/../Admin")
    assert not is_valid
    assert ".." in msg


def test_validate_page_name_empty_segment():
    """Test validate_page_name rejects empty path segments."""
    is_valid, msg = validate_page_name("Page//Name")
    assert not is_valid
    assert "empty path segments" in msg

    is_valid, msg = validate_page_name("//WikiStart")
    assert not is_valid
    assert "empty path segments" in msg


def test_validate_content_valid():
    """Test validate_content with valid content."""
    is_valid, msg = validate_content("This is valid wiki content")
    assert is_valid
    assert msg == ""

    # Large but within limit
    large_content = "x" * 100000
    is_valid, msg = validate_content(large_content)
    assert is_valid
    assert msg == ""


def test_validate_content_empty():
    """Test validate_content rejects empty content."""
    is_valid, msg = validate_content("")
    assert not is_valid
    assert "cannot be empty" in msg


def test_validate_content_too_large():
    """Test validate_content rejects oversized content."""
    # Create content exceeding default max (1MB)
    large_content = "x" * 1_000_001
    is_valid, msg = validate_content(large_content)
    assert not is_valid
    assert "exceeds maximum size" in msg

    # Test custom max_size
    is_valid, msg = validate_content("hello world", max_size=5)
    assert not is_valid
    assert "exceeds maximum size" in msg


# TestPutWikiPage tests
@patch("trac_mcp_server.core.client.requests.Session.post")
def test_put_wiki_page_create(mock_post, mock_config):
    """Test put_wiki_page for creating a new page."""
    # First response for putPage
    put_response = Mock()
    put_response.status_code = 200
    put_response.content = b"""<?xml version="1.0"?>
<methodResponse>
  <params>
    <param>
      <value><boolean>1</boolean></value>
    </param>
  </params>
</methodResponse>"""

    # Second response for getPageInfo
    info_response = Mock()
    info_response.status_code = 200
    info_response.content = b"""<?xml version="1.0"?>
<methodResponse>
  <params>
    <param>
      <value>
        <struct>
          <member>
            <name>name</name>
            <value><string>NewPage</string></value>
          </member>
          <member>
            <name>author</name>
            <value><string>testuser</string></value>
          </member>
          <member>
            <name>version</name>
            <value><int>1</int></value>
          </member>
          <member>
            <name>lastModified</name>
            <value><int>1234567890</int></value>
          </member>
        </struct>
      </value>
    </param>
  </params>
</methodResponse>"""

    mock_post.side_effect = [put_response, info_response]

    client = TracClient(mock_config)
    result = client.put_wiki_page(
        "NewPage", "= Page Content =", "Initial creation"
    )

    # Verify result structure
    assert result["name"] == "NewPage"
    assert result["version"] == 1
    assert result["author"] == "testuser"
    assert result["lastModified"] == 1234567890
    assert result["url"] == "https://trac.example.com/trac/wiki/NewPage"

    # Verify both RPC calls
    assert mock_post.call_count == 2

    # Verify putPage call
    first_payload = mock_post.call_args_list[0][1]["data"]
    assert "wiki.putPage" in first_payload
    assert "NewPage" in first_payload
    assert "= Page Content =" in first_payload
    assert "Initial creation" in first_payload


@patch("trac_mcp_server.core.client.requests.Session.post")
def test_put_wiki_page_update_with_version(mock_post, mock_config):
    """Test put_wiki_page with version number for optimistic locking."""
    # First response for putPage
    put_response = Mock()
    put_response.status_code = 200
    put_response.content = b"""<?xml version="1.0"?>
<methodResponse>
  <params>
    <param>
      <value><boolean>1</boolean></value>
    </param>
  </params>
</methodResponse>"""

    # Second response for getPageInfo
    info_response = Mock()
    info_response.status_code = 200
    info_response.content = b"""<?xml version="1.0"?>
<methodResponse>
  <params>
    <param>
      <value>
        <struct>
          <member>
            <name>name</name>
            <value><string>ExistingPage</string></value>
          </member>
          <member>
            <name>author</name>
            <value><string>testuser</string></value>
          </member>
          <member>
            <name>version</name>
            <value><int>3</int></value>
          </member>
          <member>
            <name>lastModified</name>
            <value><int>1234567900</int></value>
          </member>
        </struct>
      </value>
    </param>
  </params>
</methodResponse>"""

    # No conditional-put plugin (#129) on this server, so the version
    # precheck (ticket #120) reads the page first: v2 is current.
    mock_post.side_effect = [
        _method_not_found_response(),
        _page_info_response(2),
        put_response,
        info_response,
    ]

    client = TracClient(mock_config)
    result = client.put_wiki_page(
        "ExistingPage", "Updated content", "Minor fix", version=2
    )

    # Verify version was passed to RPC
    first_payload = mock_post.call_args_list[2][1]["data"]
    assert "wiki.putPage" in first_payload
    assert "<int>2</int>" in first_payload

    # Verify result
    assert result["version"] == 3


def test_put_wiki_page_invalid_name(mock_config):
    """Test put_wiki_page with invalid page name."""
    client = TracClient(mock_config)

    # Empty name
    with pytest.raises(ValueError) as exc_info:
        client.put_wiki_page("", "content", "comment")
    assert "Invalid page name" in str(exc_info.value)

    # Path traversal
    with pytest.raises(ValueError) as exc_info:
        client.put_wiki_page("../etc/passwd", "content", "comment")
    assert "Invalid page name" in str(exc_info.value)


def test_put_wiki_page_invalid_content(mock_config):
    """Test put_wiki_page with invalid content."""
    client = TracClient(mock_config)

    # Empty content
    with pytest.raises(ValueError) as exc_info:
        client.put_wiki_page("Page", "", "comment")
    assert "Invalid content" in str(exc_info.value)


_SQLITE_COLLISION = (
    "'UNIQUE constraint failed: wiki.name, wiki.version' "
    "while executing 'wiki.putPage()'"
)
_VERSION_NOTES_PERMISSION = (
    "WIKI_MODIFY privileges are required to perform this operation on "
    "VersionNotes. You don't have the required permissions."
)


@patch("trac_mcp_server.core.client.requests.Session.post")
def test_put_wiki_page_save_collision_is_a_conflict(
    mock_post, mock_config
):
    """Two putPage calls load the same version and both INSERT version
    N+1: the loser's fault names the DB constraint. It is a conflict
    because the page moved (the re-read), not because of the text (Trac
    /trac_mcp_server #185)."""
    mock_post.side_effect = [
        _method_not_found_response(),
        _page_info_response(1),
        _fault_response(_SQLITE_COLLISION),
        _page_info_response(2),
    ]

    client = TracClient(mock_config)

    with pytest.raises(WikiPageConflict) as exc_info:
        client.put_wiki_page("Page", "content", "comment", version=1)

    assert exc_info.value.current_version == 2
    assert exc_info.value.expected_version == 1
    assert exc_info.value.page_name == "Page"
    assert str(exc_info.value) == (
        "Version conflict on 'Page': you tried to update version 1 "
        "but its current version is 2"
    )


@patch("trac_mcp_server.core.client.requests.Session.post")
def test_put_wiki_page_fault_naming_a_version_page_is_not_a_conflict(
    mock_post, mock_config
):
    """A permission fault that merely names a page containing "version"
    is reported as itself, not as a version conflict (#185)."""
    mock_post.side_effect = [
        _method_not_found_response(),
        _page_info_response(1),
        _fault_response(_VERSION_NOTES_PERMISSION),
        _page_info_response(1),
    ]

    client = TracClient(mock_config)

    with pytest.raises(xmlrpc.client.Fault) as exc_info:
        client.put_wiki_page(
            "VersionNotes", "content", "comment", version=1
        )

    assert not isinstance(exc_info.value, ValueError)
    assert exc_info.value.faultString == _VERSION_NOTES_PERMISSION


@patch("trac_mcp_server.core.client.requests.Session.post")
def test_put_wiki_page_fault_with_failed_reread_is_the_original_fault(
    mock_post, mock_config
):
    """When the re-read after the fault itself faults, the state is
    unknown and the original putPage fault is raised (#185)."""
    mock_post.side_effect = [
        _method_not_found_response(),
        _page_info_response(1),
        _fault_response(_SQLITE_COLLISION),
        _fault_response("re-read failed"),
    ]

    client = TracClient(mock_config)

    with pytest.raises(xmlrpc.client.Fault) as exc_info:
        client.put_wiki_page("Page", "content", "comment", version=1)

    # The original putPage fault, unwrapped like any re-raised putPage
    # fault (#185 review).
    assert exc_info.value.faultString == (
        "UNIQUE constraint failed: wiki.name, wiki.version"
    )


@patch("trac_mcp_server.core.client.requests.Session.post")
def test_put_wiki_page_unversioned_fault_is_reraised_without_reread(
    mock_post, mock_config
):
    """A create / unversioned write has no version to compare: the fault
    is re-raised and nothing more is posted (#185)."""
    mock_post.side_effect = [_fault_response(_VERSION_NOTES_PERMISSION)]

    client = TracClient(mock_config)

    with pytest.raises(xmlrpc.client.Fault) as exc_info:
        client.put_wiki_page("VersionNotes", "content", "comment")

    assert exc_info.value.faultString == _VERSION_NOTES_PERMISSION
    assert mock_post.call_count == 1


_WRAPPED_VERSION_NOTES_PERMISSION = (
    f"'{_VERSION_NOTES_PERMISSION}' while executing 'wiki.putPage()'"
)


@patch("trac_mcp_server.core.client.requests.Session.post")
def test_put_wiki_page_versioned_fault_is_reraised_unwrapped(
    mock_post, mock_config
):
    """A putPage fault re-raised after an unchanged re-read loses
    TracXMLRPC's "while executing" wrapper, as the plugin path's does,
    and keeps its faultCode (#185 review)."""
    mock_post.side_effect = [
        _method_not_found_response(),
        _page_info_response(1),
        _fault_response(_WRAPPED_VERSION_NOTES_PERMISSION, code=403),
        _page_info_response(1),
    ]

    client = TracClient(mock_config)

    with pytest.raises(xmlrpc.client.Fault) as exc_info:
        client.put_wiki_page(
            "VersionNotes", "content", "comment", version=1
        )

    assert exc_info.value.faultString == _VERSION_NOTES_PERMISSION
    assert exc_info.value.faultCode == 403


@patch("trac_mcp_server.core.client.requests.Session.post")
def test_put_wiki_page_unversioned_fault_is_reraised_unwrapped(
    mock_post, mock_config
):
    """The same unwrap for a create / unversioned write (#185 review)."""
    mock_post.side_effect = [
        _fault_response(_WRAPPED_VERSION_NOTES_PERMISSION, code=403)
    ]

    client = TracClient(mock_config)

    with pytest.raises(xmlrpc.client.Fault) as exc_info:
        client.put_wiki_page("VersionNotes", "content", "comment")

    assert exc_info.value.faultString == _VERSION_NOTES_PERMISSION
    assert exc_info.value.faultCode == 403
    assert mock_post.call_count == 1


def _xmlrpc_response(body: bytes) -> Mock:
    response = Mock()
    response.status_code = 200
    response.content = (
        b'<?xml version="1.0"?><methodResponse><params><param><value>'
        + body
        + b"</value></param></params></methodResponse>"
    )
    return response


def _method_not_found_response(
    method: str = "wiki.putPageIfVersion",
) -> Mock:
    """TracXMLRPC's answer for a method no component provides: the
    server has no tracrpc_comment plugin (or has it disabled)."""
    return _fault_response(
        f'RPC method "{method}" not found', code=-32601
    )


def _page_info_response(version: int) -> Mock:
    return _xmlrpc_response(
        b"<struct>"
        b"<member><name>name</name><value><string>Page</string></value></member>"
        b"<member><name>author</name><value><string>u</string></value></member>"
        b"<member><name>version</name><value><int>"
        + str(version).encode()
        + b"</int></value></member>"
        b"<member><name>lastModified</name><value><int>1234567900</int></value></member>"
        b"</struct>"
    )


def _posted_methods(mock_post) -> list[str]:
    import re

    return [
        re.search(
            r"<methodName>(.*?)</methodName>", c[1]["data"]
        ).group(1)
        for c in mock_post.call_args_list
    ]


@patch("trac_mcp_server.core.client.requests.Session.post")
def test_put_wiki_page_stale_version_is_refused_before_putpage(
    mock_post, mock_config
):
    """Trac ignores the `version` attr of wiki.putPage (ticket #120), so
    without the plugin (#129) the client compares it itself and must never
    post putPage when stale."""
    mock_post.side_effect = [
        _method_not_found_response(),
        _page_info_response(7),
    ]

    client = TracClient(mock_config)
    with pytest.raises(WikiPageConflict) as exc_info:
        client.put_wiki_page("Page", "content", "comment", version=5)

    assert exc_info.value.page_name == "Page"
    assert exc_info.value.expected_version == 5
    assert exc_info.value.current_version == 7
    assert isinstance(exc_info.value, ValueError)
    assert _posted_methods(mock_post) == [
        "wiki.putPageIfVersion",
        "wiki.getPageInfo",
    ]


@patch("trac_mcp_server.core.client.requests.Session.post")
def test_put_wiki_page_matching_version_writes(mock_post, mock_config):
    mock_post.side_effect = [
        _method_not_found_response(),
        _page_info_response(7),
        _xmlrpc_response(b"<boolean>1</boolean>"),
        _page_info_response(8),
    ]

    client = TracClient(mock_config)
    result = client.put_wiki_page(
        "Page", "content", "comment", version=7
    )

    assert result["version"] == 8
    assert _posted_methods(mock_post) == [
        "wiki.putPageIfVersion",
        "wiki.getPageInfo",
        "wiki.putPage",
        "wiki.getPageInfo",
    ]


@patch("trac_mcp_server.core.client.requests.Session.post")
def test_put_wiki_page_unversioned_makes_no_precheck(
    mock_post, mock_config
):
    """version=None (create, unversioned callers) must cost no extra RPC."""
    mock_post.side_effect = [
        _xmlrpc_response(b"<boolean>1</boolean>"),
        _page_info_response(1),
    ]

    client = TracClient(mock_config)
    client.put_wiki_page("Page", "content", "comment")

    assert _posted_methods(mock_post) == [
        "wiki.putPage",
        "wiki.getPageInfo",
    ]


@patch("trac_mcp_server.core.client.requests.Session.post")
def test_put_wiki_page_versioned_missing_page_is_a_conflict(
    mock_post, mock_config
):
    """Some Trac instances answer 0 rather than a Fault for a missing page."""
    mock_post.side_effect = [
        _method_not_found_response(),
        _xmlrpc_response(b"<int>0</int>"),
    ]

    client = TracClient(mock_config)
    with pytest.raises(WikiPageConflict) as exc_info:
        client.put_wiki_page("Page", "content", "comment", version=1)

    assert exc_info.value.expected_version == 1
    assert exc_info.value.current_version is None
    assert _posted_methods(mock_post) == [
        "wiki.putPageIfVersion",
        "wiki.getPageInfo",
    ]


def _fault_response(fault_string: str, code: int = 1) -> Mock:
    response = Mock()
    response.status_code = 200
    response.content = (
        b'<?xml version="1.0"?><methodResponse><fault><value><struct>'
        b"<member><name>faultCode</name><value><int>"
        + str(code).encode()
        + b"</int></value></member>"
        b"<member><name>faultString</name><value><string>"
        + fault_string.encode()
        + b"</string></value></member>"
        b"</struct></value></fault></methodResponse>"
    )
    return response


@patch("trac_mcp_server.core.client.requests.Session.post")
def test_put_wiki_page_missing_page_fault_is_a_conflict(
    mock_post, mock_config
):
    """Some Trac instances raise a not-found Fault from getPageInfo
    instead of answering 0; both mean the page is missing."""
    mock_post.side_effect = [
        _method_not_found_response(),
        _fault_response("Wiki page 'Page' does not exist"),
    ]

    client = TracClient(mock_config)
    with pytest.raises(WikiPageConflict) as exc_info:
        client.put_wiki_page("Page", "content", "comment", version=1)

    assert exc_info.value.current_version is None
    assert _posted_methods(mock_post) == [
        "wiki.putPageIfVersion",
        "wiki.getPageInfo",
    ]


@patch("trac_mcp_server.core.client.requests.Session.post")
def test_put_wiki_page_other_precheck_fault_propagates(
    mock_post, mock_config
):
    import xmlrpc.client

    mock_post.side_effect = [
        _method_not_found_response(),
        _fault_response("Permission denied"),
    ]

    client = TracClient(mock_config)
    with pytest.raises(xmlrpc.client.Fault):
        client.put_wiki_page("Page", "content", "comment", version=1)

    assert _posted_methods(mock_post) == [
        "wiki.putPageIfVersion",
        "wiki.getPageInfo",
    ]


@patch("trac_mcp_server.core.client.requests.Session.post")
def test_put_wiki_page_real_404_precheck_fault_is_a_conflict(
    mock_post, mock_config
):
    """TracXMLRPC's real missing-page fault, Fault(404, 'Wiki page "X"
    does not exist'), is classified by its code: the page is absent
    (Trac #191)."""
    mock_post.side_effect = [
        _method_not_found_response(),
        _fault_response('Wiki page "Page" does not exist', code=404),
    ]

    client = TracClient(mock_config)
    with pytest.raises(WikiPageConflict) as exc_info:
        client.put_wiki_page("Page", "content", "comment", version=1)

    assert exc_info.value.current_version is None
    assert "the page does not exist" in str(exc_info.value)
    assert _posted_methods(mock_post) == [
        "wiki.putPageIfVersion",
        "wiki.getPageInfo",
    ]


@patch("trac_mcp_server.core.client.requests.Session.post")
def test_put_wiki_page_code_1_quoted_name_precheck_fault_is_not_absence(
    mock_post, mock_config
):
    """A code-1 fault is any TracError, and the name it quotes,
    'Not Found Notes', is no evidence that the page is missing: the
    pre-check re-raises it instead of reporting a conflict, and
    putPage is never posted (Trac #191)."""
    mock_post.side_effect = [
        _method_not_found_response(),
        _fault_response(
            "'Invalid Wiki page name 'Not Found Notes'' while "
            "executing 'wiki.getPageInfo()'"
        ),
    ]

    client = TracClient(mock_config)
    with pytest.raises(xmlrpc.client.Fault) as exc_info:
        client.put_wiki_page(
            "Not Found Notes", "content", "comment", version=1
        )

    assert not isinstance(exc_info.value, WikiPageConflict)
    assert exc_info.value.faultCode == 1
    assert _posted_methods(mock_post) == [
        "wiki.putPageIfVersion",
        "wiki.getPageInfo",
    ]


@patch("trac_mcp_server.core.client.requests.Session.post")
def test_put_wiki_page_precheck_fault_is_re_raised_unwrapped(
    mock_post, mock_config
):
    """The pre-check's fault is re-raised like putPage's: TracXMLRPC's
    "while executing" wrapper removed, the fault code kept (Trac
    #191)."""
    mock_post.side_effect = [
        _method_not_found_response(),
        _fault_response(
            "'Invalid Wiki page name 'Not Found Notes'' while "
            "executing 'wiki.getPageInfo()'"
        ),
    ]

    client = TracClient(mock_config)
    with pytest.raises(xmlrpc.client.Fault) as exc_info:
        client.put_wiki_page(
            "Not Found Notes", "content", "comment", version=1
        )

    assert exc_info.value.faultCode == 1
    assert "while executing" not in exc_info.value.faultString
    assert (
        exc_info.value.faultString
        == "Invalid Wiki page name 'Not Found Notes'"
    )


@patch("trac_mcp_server.core.client.requests.Session.post")
def test_get_recent_wiki_changes_falls_back_on_a_missing_method(
    mock_post, mock_config
):
    """A real MethodNotFound (-32601) for wiki.getRecentChanges falls
    back to listing the pages (Trac #191)."""
    mock_post.side_effect = [
        _method_not_found_response("wiki.getRecentChanges")
    ]
    client = TracClient(mock_config)
    with patch.object(
        client, "list_wiki_pages", return_value=[]
    ) as listed:
        assert client.get_recent_wiki_changes() == []

    listed.assert_called_once_with()


@patch("trac_mcp_server.core.client.requests.Session.post")
def test_get_recent_wiki_changes_falls_back_on_an_unprefixed_fault(
    mock_post, mock_config
):
    """A server whose text omits the service prefix ("No such method:
    getRecentChanges", code 1) still counts as a missing method (Trac
    #191)."""
    mock_post.side_effect = [
        _fault_response("No such method: getRecentChanges")
    ]
    client = TracClient(mock_config)
    with patch.object(
        client, "list_wiki_pages", return_value=[]
    ) as listed:
        assert client.get_recent_wiki_changes() == []

    listed.assert_called_once_with()


@patch("trac_mcp_server.core.client.requests.Session.post")
def test_get_recent_wiki_changes_resource_fault_is_not_a_missing_method(
    mock_post, mock_config
):
    """A fault whose text says "not found" is not a missing method: it
    is re-raised and the listing fallback is not tried (Trac #191)."""
    mock_post.side_effect = [
        _fault_response("Wiki page 'Not Found Notes' not found")
    ]
    client = TracClient(mock_config)
    with patch.object(client, "list_wiki_pages") as listed:
        with pytest.raises(xmlrpc.client.Fault) as exc_info:
            client.get_recent_wiki_changes()

    assert exc_info.value.faultCode == 1
    listed.assert_not_called()


@patch("trac_mcp_server.core.client.requests.Session.post")
def test_put_wiki_page_not_modified(mock_post, mock_config):
    """Test put_wiki_page handles 'not modified' errors."""
    fault_response = Mock()
    fault_response.status_code = 200
    fault_response.content = b"""<?xml version="1.0"?>
<methodResponse>
  <fault>
    <value>
      <struct>
        <member>
          <name>faultCode</name>
          <value><int>1</int></value>
        </member>
        <member>
          <name>faultString</name>
          <value><string>Page not modified - content is identical</string></value>
        </member>
      </struct>
    </value>
  </fault>
</methodResponse>"""

    mock_post.return_value = fault_response

    client = TracClient(mock_config)

    with pytest.raises(ValueError) as exc_info:
        client.put_wiki_page("Page", "content", "comment")

    assert "not modified" in str(exc_info.value)
    # Ticket #174: a typed no-op, still a ValueError for old callers.
    assert isinstance(exc_info.value, WikiPageNotModified)
    assert (
        str(exc_info.value) == "Page not modified (content identical)"
    )
    assert exc_info.value.page_name == "Page"
    assert exc_info.value.version is None


# ---------------------------------------------------------------------------
# put_wiki_page: a read-back failure after a stored write (Trac
# /trac_mcp_server #140)
# ---------------------------------------------------------------------------


_NO_PLUGIN = xmlrpc.client.Fault(
    -32601, 'RPC method "wiki.putPageIfVersion" not found'
)


def _rpc_putpage_then(readback, plugin=False):
    """An ``_rpc_request`` stand-in: the version precheck sees version 3,
    putPage returns True, and the read-back after it raises ``readback``
    (an exception) or returns it (anything else). With ``plugin`` the
    server has ``wiki.putPageIfVersion`` (#129), which stores; without, it
    answers method-not-found and the client falls back."""
    calls: list[tuple] = []
    stored = False

    def _rpc(namespace, method, *args):
        nonlocal stored
        calls.append((namespace, method, *args))
        if method == "putPageIfVersion":
            if not plugin:
                raise _NO_PLUGIN
            stored = True
            return True
        if method == "putPage":
            stored = True
            return True
        if method == "getPageInfo":
            if stored:
                if isinstance(readback, BaseException):
                    raise readback
                return readback
            return {"name": "X", "version": 3}
        raise AssertionError(f"unexpected RPC {method}")

    return calls, _rpc


@pytest.mark.parametrize(
    "readback",
    [
        pytest.param(
            xmlrpc.client.Fault(1, "Page 'X' not found"),
            id="not-found-fault",
        ),
        pytest.param(
            xmlrpc.client.Fault(1, "Internal error"),
            id="other-fault",
        ),
        # A "version" fault from the read-back used to be translated into
        # "Version conflict" -- a write refused -- for a stored page.
        pytest.param(
            xmlrpc.client.Fault(1, "Bad version request"),
            id="version-fault",
        ),
        pytest.param(
            requests.exceptions.ConnectionError("reset"),
            id="transport-error",
        ),
    ],
)
@pytest.mark.parametrize(
    "plugin", [False, True], ids=["precheck", "plugin"]
)
def test_put_wiki_page_readback_failure_says_stored(
    mock_config, readback, plugin
):
    calls, rpc = _rpc_putpage_then(readback, plugin)
    client = TracClient(mock_config)
    with patch.object(client, "_rpc_request", side_effect=rpc):
        with pytest.raises(WikiPageStoredReadbackFailed) as exc_info:
            client.put_wiki_page("X", "content", "comment", version=3)

    err = exc_info.value
    # Neither a Fault nor a ValueError: no caller's "nothing stored"
    # handler may match it.
    assert not isinstance(err, xmlrpc.client.Fault)
    assert not isinstance(err, ValueError)
    assert err.page_name == "X"
    assert err.url == "https://trac.example.com/trac/wiki/X"
    assert err.cause is readback
    assert err.__cause__ is readback
    assert "was stored" in str(err)
    methods = [c[1] for c in calls]
    assert methods.count("putPageIfVersion") == 1
    assert methods.count("putPage") == (0 if plugin else 1)


def test_put_wiki_page_non_dict_readback_says_stored(mock_config):
    """Some Trac instances answer 0 for a missing page; after a stored
    write that is a failed read-back, not an AttributeError."""
    calls, rpc = _rpc_putpage_then(0)
    client = TracClient(mock_config)
    with patch.object(client, "_rpc_request", side_effect=rpc):
        with pytest.raises(WikiPageStoredReadbackFailed) as exc_info:
            client.put_wiki_page("X", "content", "comment")

    assert exc_info.value.cause is None
    assert [c[1] for c in calls] == ["putPage", "getPageInfo"]


# ---------------------------------------------------------------------------
# put_wiki_page: flag a possible overwrite when the read-back version is
# not version + 1 (Trac /trac_mcp_server #130)
# ---------------------------------------------------------------------------


def _rpc_versions(precheck, readback, plugin=False):
    """An ``_rpc_request`` stand-in: the version precheck sees
    ``precheck`` (or a not-found fault when ``None``), putPage returns
    True, and the read-back after it sees ``readback``. ``plugin`` as in
    ``_rpc_putpage_then``: with it, ``wiki.putPageIfVersion`` stores (and
    there is no precheck); without, it answers method-not-found."""
    calls: list[tuple] = []
    stored = False

    def _rpc(namespace, method, *args):
        nonlocal stored
        calls.append((namespace, method, *args))
        if method == "putPageIfVersion":
            if not plugin:
                raise _NO_PLUGIN
            stored = True
            return True
        if method == "putPage":
            stored = True
            return True
        if method == "getPageInfo":
            if stored:
                return {"name": "X", "version": readback, "author": "a"}
            if precheck is None:
                raise xmlrpc.client.Fault(1, "Page 'X' not found")
            return {"name": "X", "version": precheck, "author": "a"}
        raise AssertionError(f"unexpected RPC {method}")

    return calls, _rpc


@pytest.mark.parametrize(
    "plugin", [False, True], ids=["precheck", "plugin"]
)
def test_put_wiki_page_flags_possible_overwrite(mock_config, plugin):
    """By construction: the precheck sees 3, another writer stores 4,
    this putPage stores 5. The result must say so, not report a plain
    success. Through the plugin (#129) only a write landing after the
    save can do this, but the check is kept on that path too."""
    calls, rpc = _rpc_versions(precheck=3, readback=5, plugin=plugin)
    client = TracClient(mock_config)
    with patch.object(client, "_rpc_request", side_effect=rpc):
        result = client.put_wiki_page("X", "content", "c", version=3)

    assert result["version"] == 5
    assert result["possible_overwrite"] is True
    assert result["intervening_versions"] == [4]
    # Detect only: no extra RPC beyond the write(s), precheck and read-back.
    if plugin:
        assert [c[1] for c in calls] == [
            "putPageIfVersion",
            "getPageInfo",
        ]
    else:
        assert [c[1] for c in calls] == [
            "putPageIfVersion",
            "getPageInfo",
            "putPage",
            "getPageInfo",
        ]


def test_put_wiki_page_lists_every_intervening_version(mock_config):
    _, rpc = _rpc_versions(precheck=3, readback=7)
    client = TracClient(mock_config)
    with patch.object(client, "_rpc_request", side_effect=rpc):
        result = client.put_wiki_page("X", "content", "c", version=3)

    assert result["possible_overwrite"] is True
    assert result["intervening_versions"] == [4, 5, 6]


@pytest.mark.parametrize(
    "plugin", [False, True], ids=["precheck", "plugin"]
)
def test_put_wiki_page_uncontended_update_is_not_flagged(
    mock_config, plugin
):
    """The normal path: version 3 in, version 4 back. It must stay
    quiet."""
    _, rpc = _rpc_versions(precheck=3, readback=4, plugin=plugin)
    client = TracClient(mock_config)
    with patch.object(client, "_rpc_request", side_effect=rpc):
        result = client.put_wiki_page("X", "content", "c", version=3)

    assert result["version"] == 4
    assert result["possible_overwrite"] is False
    assert "intervening_versions" not in result


@pytest.mark.parametrize("readback", [1, 5])
def test_put_wiki_page_create_is_never_flagged(mock_config, readback):
    """A create (version=None) has nothing to compare with, whatever
    version the read-back reports."""
    calls, rpc = _rpc_versions(precheck=None, readback=readback)
    client = TracClient(mock_config)
    with patch.object(client, "_rpc_request", side_effect=rpc):
        result = client.put_wiki_page("X", "content", "c")

    assert result["possible_overwrite"] is False
    assert "intervening_versions" not in result
    assert [c[1] for c in calls] == ["putPage", "getPageInfo"]


def test_put_wiki_page_putpage_fault_still_means_nothing_stored(
    mock_config,
):
    """A fault from putPage itself keeps its old meaning (nothing
    stored): it propagates as a Fault, not as the stored exception."""
    import xmlrpc.client

    def _rpc(namespace, method, *args):
        raise xmlrpc.client.Fault(1, "Internal error")

    client = TracClient(mock_config)
    with patch.object(client, "_rpc_request", side_effect=_rpc):
        with pytest.raises(xmlrpc.client.Fault):
            client.put_wiki_page("X", "content", "comment")


# ---------------------------------------------------------------------------
# put_wiki_page: the server-side conditional put, wiki.putPageIfVersion
# (Trac /trac_mcp_server #129)
# ---------------------------------------------------------------------------


def _conflict_fault_string(current: str) -> str:
    """The plugin's conflict message, as TracXMLRPC wraps it."""
    return (
        f"'[wiki-version-conflict current_version={current}] Version "
        'conflict on wiki page "Page": you tried to update version 7 '
        f"but ...' while executing 'wiki.putPageIfVersion()'"
    )


@patch("trac_mcp_server.core.client.requests.Session.post")
def test_put_wiki_page_versioned_takes_the_plugin_path(
    mock_post, mock_config
):
    """With a version, the write goes through wiki.putPageIfVersion:
    no client-side precheck, no putPage, and the read-back still runs."""
    mock_post.side_effect = [
        _xmlrpc_response(b"<boolean>1</boolean>"),
        _page_info_response(8),
    ]

    client = TracClient(mock_config)
    result = client.put_wiki_page("Page", "new text", "why", version=7)

    assert _posted_methods(mock_post) == [
        "wiki.putPageIfVersion",
        "wiki.getPageInfo",
    ]
    params = xmlrpc.client.loads(
        mock_post.call_args_list[0][1]["data"]
    )[0]
    assert params == ("Page", "new text", {"comment": "why"}, 7)
    assert result["version"] == 8
    assert result["possible_overwrite"] is False


@pytest.mark.parametrize(
    "current, expected_current",
    [("9", 9), ("none", None)],
    ids=["newer", "missing"],
)
@patch("trac_mcp_server.core.client.requests.Session.post")
def test_put_wiki_page_plugin_conflict_becomes_wiki_page_conflict(
    mock_post, mock_config, current, expected_current
):
    """The plugin's 409 conflict maps to the same WikiPageConflict the
    precheck raises -- so the tools need no new error path -- and
    nothing else is posted: no fallback, no putPage, no read-back."""
    mock_post.side_effect = [
        _fault_response(_conflict_fault_string(current), code=409)
    ]

    client = TracClient(mock_config)
    with pytest.raises(WikiPageConflict) as exc_info:
        client.put_wiki_page("Page", "content", "comment", version=7)

    err = exc_info.value
    assert err.page_name == "Page"
    assert err.expected_version == 7
    assert err.current_version == expected_current
    assert _posted_methods(mock_post) == ["wiki.putPageIfVersion"]


@pytest.mark.parametrize(
    "code, fault_string",
    [
        # A 409 that is not the plugin's: no marker.
        (409, "'Conflict' while executing 'wiki.putPageIfVersion()'"),
        # The marker without the 409, e.g. inside a page name.
        (
            1,
            "'Invalid Wiki page name "
            '"[wiki-version-conflict current_version=3]"\' '
            "while executing 'wiki.putPageIfVersion()'",
        ),
    ],
    ids=["409-without-marker", "marker-without-409"],
)
def test_put_wiki_page_only_the_exact_conflict_fault_is_a_conflict(
    mock_config, code, fault_string
):
    calls = []

    def _rpc(namespace, method, *args):
        calls.append(method)
        raise xmlrpc.client.Fault(code, fault_string)

    client = TracClient(mock_config)
    with patch.object(client, "_rpc_request", side_effect=_rpc):
        with pytest.raises(xmlrpc.client.Fault) as exc_info:
            client.put_wiki_page(
                "Page", "content", "comment", version=7
            )

    assert not isinstance(exc_info.value, WikiPageConflict)
    assert calls == ["putPageIfVersion"]


@patch("trac_mcp_server.core.client.requests.Session.post")
def test_put_wiki_page_missing_plugin_falls_back_and_precheck_refuses(
    mock_post, mock_config
):
    """No plugin on the server: the ticket #120 precheck runs as before
    and still refuses a stale version before any putPage."""
    mock_post.side_effect = [
        _method_not_found_response(),
        _page_info_response(9),
    ]

    client = TracClient(mock_config)
    with pytest.raises(WikiPageConflict) as exc_info:
        client.put_wiki_page("Page", "content", "comment", version=7)

    assert exc_info.value.current_version == 9
    assert _posted_methods(mock_post) == [
        "wiki.putPageIfVersion",
        "wiki.getPageInfo",
    ]


@pytest.mark.parametrize(
    "version, current_page, expected_current",
    [
        (0, None, None),
        (0, 3, 3),
        (-1, None, None),
        (-1, 3, 3),
    ],
    ids=[
        "zero-missing",
        "zero-exists",
        "negative-missing",
        "negative-exists",
    ],
)
def test_put_wiki_page_version_below_one_never_reaches_the_plugin(
    mock_config, version, current_page, expected_current
):
    """Versions start at 1, so 0 and below name no page: they skip
    wiki.putPageIfVersion (which would create a missing page for 0 and
    fault on a negative with a message `wiki_update` mislabels as a
    modified page) and take the precheck path, which refuses them with
    WikiPageConflict whether or not the page exists."""
    calls = []

    def _rpc(namespace, method, *args):
        calls.append(method)
        assert method == "getPageInfo", method
        if current_page is None:
            raise xmlrpc.client.Fault(
                1, "Wiki page 'Page' does not exist"
            )
        return {"name": "Page", "version": current_page}

    client = TracClient(mock_config)
    with patch.object(client, "_rpc_request", side_effect=_rpc):
        with pytest.raises(WikiPageConflict) as exc_info:
            client.put_wiki_page(
                "Page", "content", "comment", version=version
            )

    err = exc_info.value
    assert err.expected_version == version
    assert err.current_version == expected_current
    assert "putPageIfVersion" not in calls
    assert "putPage" not in calls
    assert calls == ["getPageInfo"]


def test_put_wiki_page_missing_plugin_by_text_also_falls_back(
    mock_config,
):
    """A server that reports a missing method with code 1 but the stock
    text is recognised too."""
    calls, rpc = _rpc_versions(precheck=3, readback=4)

    def _rpc(namespace, method, *args):
        if method == "putPageIfVersion":
            calls.append((namespace, method, *args))
            raise xmlrpc.client.Fault(
                1, 'RPC method "wiki.putPageIfVersion" not found'
            )
        return rpc(namespace, method, *args)

    client = TracClient(mock_config)
    with patch.object(client, "_rpc_request", side_effect=_rpc):
        result = client.put_wiki_page("X", "content", "c", version=3)

    assert result["version"] == 4
    assert [c[1] for c in calls] == [
        "putPageIfVersion",
        "getPageInfo",
        "putPage",
        "getPageInfo",
    ]


def test_put_wiki_page_page_not_found_fault_is_not_a_missing_method(
    mock_config,
):
    """ "not found" alone (a page, a resource) must not trigger the
    fallback: only a missing method may."""
    calls = []

    def _rpc(namespace, method, *args):
        calls.append(method)
        raise xmlrpc.client.Fault(404, 'Wiki page "X" not found')

    client = TracClient(mock_config)
    with patch.object(client, "_rpc_request", side_effect=_rpc):
        with pytest.raises(xmlrpc.client.Fault) as exc_info:
            client.put_wiki_page("X", "content", "c", version=3)

    assert exc_info.value.faultCode == 404
    assert calls == ["putPageIfVersion"]


def test_put_wiki_page_remembers_a_missing_plugin_for_a_while(
    mock_config,
):
    """After a method-not-found the client skips the plugin call until
    WIKI_CONDITIONAL_PUT_ABSENT_TTL_SECONDS have passed, then tries it
    again (so a plugin deployed later is picked up without a restart)."""
    from trac_mcp_server.core import client as client_module

    client = TracClient(mock_config)
    now = [1000.0]
    with patch.object(
        client_module.time, "monotonic", side_effect=lambda: now[0]
    ):
        calls, rpc = _rpc_versions(precheck=3, readback=4)
        with patch.object(client, "_rpc_request", side_effect=rpc):
            client.put_wiki_page("X", "content", "c", version=3)
        assert [c[1] for c in calls][0] == "putPageIfVersion"

        now[0] += (
            client_module.WIKI_CONDITIONAL_PUT_ABSENT_TTL_SECONDS - 1
        )
        calls, rpc = _rpc_versions(precheck=3, readback=4)
        with patch.object(client, "_rpc_request", side_effect=rpc):
            client.put_wiki_page("X", "content", "c", version=3)
        assert [c[1] for c in calls] == [
            "getPageInfo",
            "putPage",
            "getPageInfo",
        ]

        now[0] += 2
        calls, rpc = _rpc_versions(precheck=3, readback=4, plugin=True)
        with patch.object(client, "_rpc_request", side_effect=rpc):
            client.put_wiki_page("X", "content", "c", version=3)
        assert [c[1] for c in calls] == [
            "putPageIfVersion",
            "getPageInfo",
        ]


@pytest.mark.parametrize("readback", [1, 5])
def test_put_wiki_page_create_never_calls_the_plugin(
    mock_config, readback
):
    """version=None keeps today's path exactly: putPage, read-back."""
    calls, rpc = _rpc_versions(
        precheck=None, readback=readback, plugin=True
    )
    client = TracClient(mock_config)
    with patch.object(client, "_rpc_request", side_effect=rpc):
        client.put_wiki_page("X", "content", "c")

    assert [c[1] for c in calls] == ["putPage", "getPageInfo"]


def test_put_wiki_page_plugin_not_modified_is_the_typed_no_op(
    mock_config,
):
    def _rpc(namespace, method, *args):
        raise xmlrpc.client.Fault(
            1,
            "'Page not modified' while executing 'wiki.putPageIfVersion()'",
        )

    client = TracClient(mock_config)
    with patch.object(client, "_rpc_request", side_effect=_rpc):
        with pytest.raises(ValueError) as exc_info:
            client.put_wiki_page("X", "content", "c", version=3)

    assert not isinstance(exc_info.value, WikiPageConflict)
    assert "not modified" in str(exc_info.value)
    # Ticket #174.
    assert isinstance(exc_info.value, WikiPageNotModified)
    assert (
        str(exc_info.value) == "Page not modified (content identical)"
    )
    assert exc_info.value.page_name == "X"
    assert exc_info.value.version == 3


def test_put_wiki_page_precheck_not_modified_carries_the_version(
    mock_config,
):
    """The client-side path (no plugin) raises the same typed no-op,
    with the version the caller passed (ticket #174)."""

    def _rpc(namespace, method, *args):
        if method == "putPageIfVersion":
            raise xmlrpc.client.Fault(
                -32601, 'RPC method "wiki.putPageIfVersion" not found'
            )
        if method == "getPageInfo":
            return {"name": "X", "version": 3}
        if method == "putPage":
            raise xmlrpc.client.Fault(1, "Page not modified")
        raise AssertionError(f"unexpected RPC {namespace}.{method}")

    client = TracClient(mock_config)
    with patch.object(client, "_rpc_request", side_effect=_rpc):
        with pytest.raises(WikiPageNotModified) as exc_info:
            client.put_wiki_page("X", "content", "c", version=3)

    assert isinstance(exc_info.value, ValueError)
    assert (
        str(exc_info.value) == "Page not modified (content identical)"
    )
    assert exc_info.value.page_name == "X"
    assert exc_info.value.version == 3


def test_put_wiki_page_plugin_loose_not_modified_text_is_no_no_op(
    mock_config,
):
    """Only Trac's own "page not modified" is the no-op (ticket #174
    review): a plugin fault that merely contains "not modified" goes
    through `unwrap_tracrpc_fault`, since a false match is reported as
    success."""

    def _rpc(namespace, method, *args):
        raise xmlrpc.client.Fault(
            1,
            "'Plugin refused: not modified by policy' while executing "
            "'wiki.putPageIfVersion()'",
        )

    client = TracClient(mock_config)
    with patch.object(client, "_rpc_request", side_effect=_rpc):
        with pytest.raises(xmlrpc.client.Fault) as exc_info:
            client.put_wiki_page("X", "content", "c", version=3)

    assert not isinstance(exc_info.value, WikiPageNotModified)
    assert (
        exc_info.value.faultString
        == "Plugin refused: not modified by policy"
    )


def test_put_wiki_page_precheck_loose_not_modified_text_reraises_fault(
    mock_config,
):
    def _rpc(namespace, method, *args):
        if method == "putPageIfVersion":
            raise xmlrpc.client.Fault(
                -32601, 'RPC method "wiki.putPageIfVersion" not found'
            )
        if method == "getPageInfo":
            return {"name": "X", "version": 3}
        if method == "putPage":
            raise xmlrpc.client.Fault(
                1, "Plugin refused: not modified by policy"
            )
        raise AssertionError(f"unexpected RPC {namespace}.{method}")

    client = TracClient(mock_config)
    with patch.object(client, "_rpc_request", side_effect=_rpc):
        with pytest.raises(xmlrpc.client.Fault) as exc_info:
            client.put_wiki_page("X", "content", "c", version=3)

    assert not isinstance(exc_info.value, ValueError)
    assert (
        exc_info.value.faultString
        == "Plugin refused: not modified by policy"
    )


def _not_modified_rpc(reread, plugin: bool):
    """An `_rpc_request` stand-in for a no-op write: the write answers
    "Page not modified"; `getPageInfo` answers per ``reread`` (the
    precheck's own call sees version 5, the re-read after the no-op sees
    ``reread``, a value or an exception to raise)."""
    seen_put = []

    def _rpc(namespace, method, *args):
        if method == "putPageIfVersion":
            if not plugin:
                raise xmlrpc.client.Fault(
                    -32601,
                    'RPC method "wiki.putPageIfVersion" not found',
                )
            seen_put.append(method)
            raise xmlrpc.client.Fault(
                1,
                "'Page not modified' while executing "
                "'wiki.putPageIfVersion()'",
            )
        if method == "putPage":
            seen_put.append(method)
            raise xmlrpc.client.Fault(1, "Page not modified")
        if method == "getPageInfo":
            if not seen_put:
                return {"name": "X", "version": 5}
            if isinstance(reread, BaseException):
                raise reread
            return reread
        raise AssertionError(f"unexpected RPC {namespace}.{method}")

    return _rpc


@pytest.mark.parametrize("plugin", [True, False])
def test_put_wiki_page_not_modified_rereads_the_current_version(
    mock_config, plugin
):
    """Identical content stored as v6 by someone else while the caller
    passed v5: the no-op carries 6 as `current_version` (ticket #174
    review), on both write paths."""
    client = TracClient(mock_config)
    rpc = _not_modified_rpc({"name": "X", "version": 6}, plugin)
    with patch.object(client, "_rpc_request", side_effect=rpc):
        with pytest.raises(WikiPageNotModified) as exc_info:
            client.put_wiki_page("X", "content", "c", version=5)

    assert exc_info.value.version == 5
    assert exc_info.value.current_version == 6


@pytest.mark.parametrize("plugin", [True, False])
@pytest.mark.parametrize(
    "reread",
    [
        xmlrpc.client.Fault(1, "Page not found"),
        OSError("connection reset"),
        0,
        {},
        "garbage",
    ],
    ids=["fault", "transport", "zero", "empty-dict", "non-dict"],
)
def test_put_wiki_page_not_modified_failed_reread_leaves_none(
    mock_config, plugin, reread
):
    """The best-effort re-read never replaces the no-op: any failure,
    or a non-dict or falsy reply, gives `current_version` None and the
    exception still raises."""
    client = TracClient(mock_config)
    rpc = _not_modified_rpc(reread, plugin)
    with patch.object(client, "_rpc_request", side_effect=rpc):
        with pytest.raises(WikiPageNotModified) as exc_info:
            client.put_wiki_page("X", "content", "c", version=5)

    assert exc_info.value.version == 5
    assert exc_info.value.current_version is None


@pytest.mark.parametrize(
    "code, raw, unwrapped",
    [
        (
            1,
            "'Invalid Wiki page name \"a//b\"' while executing "
            "'wiki.putPageIfVersion()'",
            'Invalid Wiki page name "a//b"',
        ),
        # Permission faults carry no wrapper; they pass through as-is.
        (
            403,
            "WIKI_MODIFY privileges are required to perform this "
            "operation on X",
            "WIKI_MODIFY privileges are required to perform this "
            "operation on X",
        ),
    ],
    ids=["wrapped", "unwrapped"],
)
def test_put_wiki_page_other_plugin_faults_propagate_unwrapped(
    mock_config, code, raw, unwrapped
):
    """Any other fault propagates as a Fault (nothing stored), without
    TracXMLRPC's "while executing 'wiki.putPageIfVersion()'" suffix --
    whose "version" wiki_update would otherwise read as a conflict."""

    def _rpc(namespace, method, *args):
        raise xmlrpc.client.Fault(code, raw)

    client = TracClient(mock_config)
    with patch.object(client, "_rpc_request", side_effect=_rpc):
        with pytest.raises(xmlrpc.client.Fault) as exc_info:
            client.put_wiki_page("X", "content", "c", version=3)

    assert exc_info.value.faultCode == code
    assert exc_info.value.faultString == unwrapped
    assert "version" not in exc_info.value.faultString.lower()


# ---------------------------------------------------------------------------
# put_wiki_page(create_only=True): a create that refuses an existing page
# (Trac /trac_mcp_server #272)
# ---------------------------------------------------------------------------

_PAGE_ABSENT_FAULT = (1, "Wiki page 'Page' does not exist")


def _params_of(mock_post, index: int):
    return xmlrpc.client.loads(
        mock_post.call_args_list[index][1]["data"]
    )[0]


@patch("trac_mcp_server.core.client.requests.Session.post")
def test_put_wiki_page_create_only_plugin_posts_version_zero(
    mock_post, mock_config
):
    """With the plugin, a create is putPageIfVersion(..., 0): the server
    checks "absent" and saves in one transaction. No precheck, no
    putPage; the read-back is as for any create."""
    mock_post.side_effect = [
        _xmlrpc_response(b"<boolean>1</boolean>"),
        _page_info_response(1),
    ]

    client = TracClient(mock_config)
    result = client.put_wiki_page(
        "Page", "new text", "why", create_only=True
    )

    assert _posted_methods(mock_post) == [
        "wiki.putPageIfVersion",
        "wiki.getPageInfo",
    ]
    assert _params_of(mock_post, 0) == (
        "Page",
        "new text",
        {"comment": "why"},
        0,
    )
    assert result["version"] == 1
    assert result["possible_overwrite"] is False


@pytest.mark.parametrize("current", ["4", "1"])
@patch("trac_mcp_server.core.client.requests.Session.post")
def test_put_wiki_page_create_only_plugin_conflict_is_page_exists(
    mock_post, mock_config, current
):
    """The plugin's conflict fault for expected version 0 means the page
    exists: WikiPageExists carrying the version the other writer
    reached. Nothing else is posted."""
    mock_post.side_effect = [
        _fault_response(_conflict_fault_string(current), code=409),
        _xmlrpc_response(b"<string>another writer's text</string>"),
    ]

    client = TracClient(mock_config)
    with pytest.raises(WikiPageExists) as exc_info:
        client.put_wiki_page(
            "Page", "content", "comment", create_only=True
        )

    err = exc_info.value
    assert err.page_name == "Page"
    assert err.current_version == int(current)
    assert err.expected_version is None
    # Still a WikiPageConflict, hence a ValueError: "nothing stored".
    assert isinstance(err, WikiPageConflict)
    assert isinstance(err, ValueError)
    # The text read that tells a page with other content from an
    # identical one (#174), and nothing stored.
    assert _posted_methods(mock_post) == [
        "wiki.putPageIfVersion",
        "wiki.getPage",
    ]


@patch("trac_mcp_server.core.client.requests.Session.post")
def test_put_wiki_page_create_only_plugin_not_modified_is_the_noop(
    mock_post, mock_config
):
    """Handled as an ordinary write's is: "Page not modified" is
    WikiPageNotModified, with no version of the caller's."""
    mock_post.side_effect = [
        _fault_response(
            "'Page not modified' while executing "
            "'wiki.putPageIfVersion()'"
        ),
        _page_info_response(2),
    ]

    client = TracClient(mock_config)
    with pytest.raises(WikiPageNotModified) as exc_info:
        client.put_wiki_page(
            "Page", "content", "comment", create_only=True
        )

    assert exc_info.value.version is None
    assert exc_info.value.current_version == 2


@patch("trac_mcp_server.core.client.requests.Session.post")
def test_put_wiki_page_create_only_other_plugin_fault_propagates(
    mock_post, mock_config
):
    """A fault that is not the conflict marker is not "page exists"."""
    mock_post.side_effect = [
        _fault_response(
            "'WIKI_CREATE privileges are required' while executing "
            "'wiki.putPageIfVersion()'",
            code=403,
        )
    ]

    client = TracClient(mock_config)
    with pytest.raises(xmlrpc.client.Fault) as exc_info:
        client.put_wiki_page(
            "Page", "content", "comment", create_only=True
        )

    assert not isinstance(exc_info.value, WikiPageConflict)
    assert exc_info.value.faultCode == 403
    assert exc_info.value.faultString == (
        "WIKI_CREATE privileges are required"
    )


@pytest.mark.parametrize(
    "absent_reply",
    [
        pytest.param(("fault",), id="not-found-fault"),
        pytest.param(("zero",), id="zero-reply"),
    ],
)
@patch("trac_mcp_server.core.client.requests.Session.post")
def test_put_wiki_page_create_only_without_plugin_rechecks_then_puts(
    mock_post, mock_config, absent_reply
):
    """No plugin (method-not-found, which falls back): the page is
    looked up immediately before putPage, and an absent page (a
    not-found fault, or the 0 some Tracs answer) is created."""
    absent = (
        _fault_response(
            _PAGE_ABSENT_FAULT[1], code=_PAGE_ABSENT_FAULT[0]
        )
        if absent_reply == ("fault",)
        else _xmlrpc_response(b"<int>0</int>")
    )
    mock_post.side_effect = [
        _method_not_found_response(),
        absent,
        _xmlrpc_response(b"<boolean>1</boolean>"),
        _page_info_response(1),
    ]

    client = TracClient(mock_config)
    result = client.put_wiki_page(
        "Page", "new text", "why", create_only=True
    )

    assert _posted_methods(mock_post) == [
        "wiki.putPageIfVersion",
        "wiki.getPageInfo",
        "wiki.putPage",
        "wiki.getPageInfo",
    ]
    # The create carries no version attribute, as before.
    assert _params_of(mock_post, 2) == (
        "Page",
        "new text",
        {"comment": "why"},
    )
    assert result["version"] == 1
    assert result["possible_overwrite"] is False


@patch("trac_mcp_server.core.client.requests.Session.post")
def test_put_wiki_page_create_only_without_plugin_refuses_existing_page(
    mock_post, mock_config
):
    """No plugin, and the re-check finds the page: WikiPageExists with
    its version, and putPage is never posted."""
    mock_post.side_effect = [
        _method_not_found_response(),
        _page_info_response(3),
        _xmlrpc_response(b"<string>another writer's text</string>"),
    ]

    client = TracClient(mock_config)
    with pytest.raises(WikiPageExists) as exc_info:
        client.put_wiki_page(
            "Page", "content", "comment", create_only=True
        )

    assert exc_info.value.page_name == "Page"
    assert exc_info.value.current_version == 3
    assert exc_info.value.expected_version is None
    assert _posted_methods(mock_post) == [
        "wiki.putPageIfVersion",
        "wiki.getPageInfo",
        "wiki.getPage",
    ]


def test_put_wiki_page_create_only_remembered_no_plugin_skips_the_call(
    mock_config,
):
    """Once the plugin is known absent, a create goes straight to the
    re-check: no putPageIfVersion round trip."""
    calls = []

    def _rpc(namespace, method, *args):
        calls.append(method)
        if method == "putPageIfVersion":
            raise _NO_PLUGIN
        if method == "getPage":
            return "another writer's text"
        return {"name": "Page", "version": 2}

    client = TracClient(mock_config)
    with patch.object(client, "_rpc_request", side_effect=_rpc):
        for _ in range(2):
            with pytest.raises(WikiPageExists):
                client.put_wiki_page(
                    "Page", "content", "comment", create_only=True
                )

    assert calls == [
        "putPageIfVersion",
        "getPageInfo",
        "getPage",
        "getPageInfo",
        "getPage",
    ]


def test_put_wiki_page_create_only_recheck_fault_propagates(
    mock_config,
):
    """The re-check's own fault, if it is not "no such page", is
    raised unwrapped and nothing is put."""
    calls = []

    def _rpc(namespace, method, *args):
        calls.append(method)
        if method == "putPageIfVersion":
            raise _NO_PLUGIN
        raise xmlrpc.client.Fault(
            403,
            "'WIKI_VIEW privileges are required' while executing "
            "'wiki.getPageInfo()'",
        )

    client = TracClient(mock_config)
    with patch.object(client, "_rpc_request", side_effect=_rpc):
        with pytest.raises(xmlrpc.client.Fault) as exc_info:
            client.put_wiki_page(
                "Page", "content", "comment", create_only=True
            )

    assert exc_info.value.faultCode == 403
    assert calls == ["putPageIfVersion", "getPageInfo"]


@pytest.mark.parametrize("version", [0, 1, 7])
def test_put_wiki_page_create_only_with_a_version_is_refused(
    mock_config, version
):
    """create_only means "no such page": a version contradicts it. A
    plain ValueError (not a conflict), raised before any RPC."""
    client = TracClient(mock_config)
    with patch.object(client, "_rpc_request") as rpc:
        with pytest.raises(ValueError) as exc_info:
            client.put_wiki_page(
                "Page", "content", "comment", version, create_only=True
            )

    assert not isinstance(exc_info.value, WikiPageConflict)
    assert "create_only" in str(exc_info.value)
    rpc.assert_not_called()


@pytest.mark.parametrize(
    "current_page", [None, 3], ids=["page-absent", "page-exists"]
)
@pytest.mark.parametrize(
    "plugin", [True, False], ids=["plugin", "precheck"]
)
def test_put_wiki_page_ordinary_version_zero_is_still_a_conflict(
    mock_config, plugin, current_page
):
    """Guard (#272): create_only did not redefine version=0. An ordinary
    call with 0 is refused as a conflict on a page that is absent AND
    on one that exists, and never reaches wiki.putPageIfVersion (where 0
    would mean "create")."""
    calls = []

    def _rpc(namespace, method, *args):
        calls.append(method)
        assert method == "getPageInfo", method
        if current_page is None:
            raise xmlrpc.client.Fault(*_PAGE_ABSENT_FAULT)
        return {"name": "Page", "version": current_page}

    client = TracClient(mock_config)
    if not plugin:
        client._wiki_conditional_put.note_fault(_NO_PLUGIN)
    with patch.object(client, "_rpc_request", side_effect=_rpc):
        with pytest.raises(WikiPageConflict) as exc_info:
            client.put_wiki_page(
                "Page", "content", "comment", version=0
            )

    assert not isinstance(exc_info.value, WikiPageExists)
    assert exc_info.value.expected_version == 0
    assert exc_info.value.current_version == current_page
    assert calls == ["getPageInfo"]


# A create that finds the page already holding this very content is the
# #174 no-op, not a conflict; one that cannot read the text says "exists".


def _text_reply(text: str) -> Mock:
    return _xmlrpc_response(b"<string>" + text.encode() + b"</string>")


_CREATE_EXISTING_PATHS = {
    "plugin": lambda current: [
        _fault_response(_conflict_fault_string(str(current)), code=409)
    ],
    "no-plugin": lambda current: [
        _method_not_found_response(),
        _page_info_response(current),
    ],
}


@pytest.mark.parametrize("path", ["plugin", "no-plugin"])
@patch("trac_mcp_server.core.client.requests.Session.post")
def test_put_wiki_page_create_only_identical_content_is_not_modified(
    mock_post, mock_config, path
):
    """The page exists with exactly this text: on both paths that is
    WikiPageNotModified (ticket #174's no-op) carrying the page's
    version, not WikiPageExists. Nothing is stored."""
    mock_post.side_effect = [
        *_CREATE_EXISTING_PATHS[path](3),
        _text_reply("same text"),
    ]

    client = TracClient(mock_config)
    with pytest.raises(WikiPageNotModified) as exc_info:
        client.put_wiki_page(
            "Page", "same text", "comment", create_only=True
        )

    assert not isinstance(exc_info.value, WikiPageExists)
    assert exc_info.value.page_name == "Page"
    assert exc_info.value.version is None
    assert exc_info.value.current_version == 3
    assert "wiki.putPage" not in _posted_methods(mock_post)


@pytest.mark.parametrize("path", ["plugin", "no-plugin"])
@patch("trac_mcp_server.core.client.requests.Session.post")
def test_put_wiki_page_create_only_near_identical_content_still_exists(
    mock_post, mock_config, path
):
    """Byte for byte: a trailing newline is different content."""
    mock_post.side_effect = [
        *_CREATE_EXISTING_PATHS[path](3),
        _text_reply("same text\n"),
    ]

    client = TracClient(mock_config)
    with pytest.raises(WikiPageExists) as exc_info:
        client.put_wiki_page(
            "Page", "same text", "comment", create_only=True
        )

    assert exc_info.value.current_version == 3


@pytest.mark.parametrize("path", ["plugin", "no-plugin"])
@patch("trac_mcp_server.core.client.requests.Session.post")
def test_put_wiki_page_create_only_unreadable_text_is_page_exists(
    mock_post, mock_config, path
):
    """If the page's text cannot be read, nothing says it is identical:
    WikiPageExists."""
    mock_post.side_effect = [
        *_CREATE_EXISTING_PATHS[path](3),
        _fault_response("denied", code=403),
    ]

    client = TracClient(mock_config)
    with pytest.raises(WikiPageExists) as exc_info:
        client.put_wiki_page(
            "Page", "same text", "comment", create_only=True
        )

    assert exc_info.value.current_version == 3


# A create is flagged when it reads back above version 1 (ticket #272
# review): a write landed after the existence check and was replaced.


@pytest.mark.parametrize(
    "readback, intervening",
    [(2, [1]), (4, [1, 2, 3])],
)
@patch("trac_mcp_server.core.client.requests.Session.post")
def test_put_wiki_page_create_only_flags_a_readback_above_one(
    mock_post, mock_config, readback, intervening
):
    mock_post.side_effect = [
        _method_not_found_response(),
        _xmlrpc_response(b"<int>0</int>"),
        _xmlrpc_response(b"<boolean>1</boolean>"),
        _page_info_response(readback),
    ]

    client = TracClient(mock_config)
    result = client.put_wiki_page(
        "Page", "text", "comment", create_only=True
    )

    assert result["version"] == readback
    assert result["possible_overwrite"] is True
    assert result["intervening_versions"] == intervening


@patch("trac_mcp_server.core.client.requests.Session.post")
def test_put_wiki_page_create_only_flags_a_readback_above_one_plugin(
    mock_post, mock_config
):
    mock_post.side_effect = [
        _xmlrpc_response(b"<boolean>1</boolean>"),
        _page_info_response(2),
    ]

    result = TracClient(mock_config).put_wiki_page(
        "Page", "text", "comment", create_only=True
    )

    assert result["possible_overwrite"] is True
    assert result["intervening_versions"] == [1]


@patch("trac_mcp_server.core.client.requests.Session.post")
def test_put_wiki_page_plain_create_is_never_flagged(
    mock_post, mock_config
):
    """Without create_only a create has no expectation to compare."""
    mock_post.side_effect = [
        _xmlrpc_response(b"<boolean>1</boolean>"),
        _page_info_response(5),
    ]

    result = TracClient(mock_config).put_wiki_page(
        "Page", "text", "comment"
    )

    assert result["possible_overwrite"] is False
    assert "intervening_versions" not in result


@pytest.mark.live
class TestPutWikiPageCreateOnlyLive:
    """Requires --run-live and TRAC_URL/USERNAME/PASSWORD. Writes two
    Scratch/ pages on /trac_test and deletes both afterwards.

    The offline rows script the transport; only a real server shows that
    a create over an existing page is refused with the page untouched
    (via the plugin's version 0, or the re-check on a server without it)
    and that a fresh name is created as version 1.
    """

    def test_create_only_refuses_an_existing_page_and_creates_a_new_one(
        self,
    ):
        import uuid

        from tests.live_helpers import trac_test_client

        client = trac_test_client()
        run = uuid.uuid4().hex[:8]
        existing = f"Scratch/Ticket272CreateOnly{run}Existing"
        fresh = f"Scratch/Ticket272CreateOnly{run}Fresh"
        first = "Ticket 272 create-only scratch, the first writer.\n"
        second = "Ticket 272 create-only scratch, must never land.\n"
        try:
            client.put_wiki_page(existing, first, "ticket 272 live")
            before = client.get_wiki_page_info(existing)["version"]

            with pytest.raises(WikiPageExists) as exc_info:
                client.put_wiki_page(
                    existing,
                    second,
                    "ticket 272 live",
                    create_only=True,
                )

            assert exc_info.value.current_version == before
            assert (
                client.get_wiki_page_info(existing)["version"] == before
            )
            assert client.get_wiki_page(existing) == first

            created = client.put_wiki_page(
                fresh, first, "ticket 272 live", create_only=True
            )
            assert created["version"] == 1
            assert created["possible_overwrite"] is False
            assert client.get_wiki_page(fresh) == first
        finally:
            for name in (existing, fresh):
                try:
                    client.delete_wiki_page(name)
                except Exception:  # never created
                    pass


# ---------------------------------------------------------------------------
# Milestone methods
# ---------------------------------------------------------------------------


@patch("trac_mcp_server.core.client.requests.Session.post")
def test_get_all_milestones_success(mock_post, mock_config):
    """Test get_all_milestones returns list of milestone names."""
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.content = b"""<?xml version="1.0"?>
<methodResponse>
  <params>
    <param>
      <value>
        <array>
          <data>
            <value><string>v1.0</string></value>
            <value><string>v2.0</string></value>
            <value><string>Future</string></value>
          </data>
        </array>
      </value>
    </param>
  </params>
</methodResponse>"""
    mock_post.return_value = mock_response

    client = TracClient(mock_config)
    result = client.get_all_milestones()

    assert result == ["v1.0", "v2.0", "Future"]
    payload = mock_post.call_args[1]["data"]
    assert "ticket.milestone.getAll" in payload


@patch("trac_mcp_server.core.client.requests.Session.post")
def test_get_milestone_success(mock_post, mock_config):
    """Test get_milestone returns milestone details dict."""
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.content = b"""<?xml version="1.0"?>
<methodResponse>
  <params>
    <param>
      <value>
        <struct>
          <member>
            <name>name</name>
            <value><string>v1.0</string></value>
          </member>
          <member>
            <name>description</name>
            <value><string>First release</string></value>
          </member>
          <member>
            <name>due</name>
            <value><int>1700000000</int></value>
          </member>
          <member>
            <name>completed</name>
            <value><int>0</int></value>
          </member>
        </struct>
      </value>
    </param>
  </params>
</methodResponse>"""
    mock_post.return_value = mock_response

    client = TracClient(mock_config)
    result = client.get_milestone("v1.0")

    assert isinstance(result, dict)
    assert result["name"] == "v1.0"
    assert result["description"] == "First release"
    assert result["due"] == 1700000000
    assert result["completed"] == 0
    payload = mock_post.call_args[1]["data"]
    assert "ticket.milestone.get" in payload


@patch("trac_mcp_server.core.client.requests.Session.post")
def test_create_milestone_success(mock_post, mock_config):
    """Test create_milestone sends correct RPC call."""
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.content = b"""<?xml version="1.0"?>
<methodResponse>
  <params>
    <param>
      <value><int>0</int></value>
    </param>
  </params>
</methodResponse>"""
    mock_post.return_value = mock_response

    client = TracClient(mock_config)
    client.create_milestone("v3.0", {"description": "Next release"})

    payload = mock_post.call_args[1]["data"]
    assert "ticket.milestone.create" in payload
    assert "v3.0" in payload


@patch("trac_mcp_server.core.client.requests.Session.post")
def test_update_milestone_success(mock_post, mock_config):
    """Test update_milestone sends correct RPC call."""
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.content = b"""<?xml version="1.0"?>
<methodResponse>
  <params>
    <param>
      <value><int>0</int></value>
    </param>
  </params>
</methodResponse>"""
    mock_post.return_value = mock_response

    client = TracClient(mock_config)
    client.update_milestone("v1.0", {"description": "Updated desc"})

    payload = mock_post.call_args[1]["data"]
    assert "ticket.milestone.update" in payload
    assert "v1.0" in payload


@patch("trac_mcp_server.core.client.requests.Session.post")
def test_delete_milestone_success(mock_post, mock_config):
    """Test delete_milestone sends correct RPC call."""
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.content = b"""<?xml version="1.0"?>
<methodResponse>
  <params>
    <param>
      <value><int>0</int></value>
    </param>
  </params>
</methodResponse>"""
    mock_post.return_value = mock_response

    client = TracClient(mock_config)
    client.delete_milestone("v1.0")

    payload = mock_post.call_args[1]["data"]
    assert "ticket.milestone.delete" in payload
    assert "v1.0" in payload


# ---------------------------------------------------------------------------
# Ticket metadata methods
# ---------------------------------------------------------------------------


@patch("trac_mcp_server.core.client.requests.Session.post")
def test_get_ticket_changelog_success(mock_post, mock_config):
    """Test get_ticket_changelog returns list of change records."""
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.content = b"""<?xml version="1.0"?>
<methodResponse>
  <params>
    <param>
      <value>
        <array>
          <data>
            <value>
              <array>
                <data>
                  <value><int>1700000000</int></value>
                  <value><string>admin</string></value>
                  <value><string>status</string></value>
                  <value><string>new</string></value>
                  <value><string>accepted</string></value>
                  <value><int>1</int></value>
                </data>
              </array>
            </value>
          </data>
        </array>
      </value>
    </param>
  </params>
</methodResponse>"""
    mock_post.return_value = mock_response

    client = TracClient(mock_config)
    result = client.get_ticket_changelog(42)

    assert isinstance(result, list)
    assert len(result) == 1
    assert result[0][1] == "admin"  # author
    assert result[0][2] == "status"  # field
    payload = mock_post.call_args[1]["data"]
    assert "ticket.changeLog" in payload


@patch("trac_mcp_server.core.client.requests.Session.post")
def test_get_ticket_actions_success(mock_post, mock_config):
    """Test get_ticket_actions returns list of action tuples."""
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.content = b"""<?xml version="1.0"?>
<methodResponse>
  <params>
    <param>
      <value>
        <array>
          <data>
            <value>
              <array>
                <data>
                  <value><string>leave</string></value>
                  <value><string>leave</string></value>
                  <value><string>Leave as new</string></value>
                  <value>
                    <array>
                      <data>
                      </data>
                    </array>
                  </value>
                </data>
              </array>
            </value>
            <value>
              <array>
                <data>
                  <value><string>accept</string></value>
                  <value><string>accept</string></value>
                  <value><string>Accept ticket</string></value>
                  <value>
                    <array>
                      <data>
                      </data>
                    </array>
                  </value>
                </data>
              </array>
            </value>
          </data>
        </array>
      </value>
    </param>
  </params>
</methodResponse>"""
    mock_post.return_value = mock_response

    client = TracClient(mock_config)
    result = client.get_ticket_actions(1)

    assert isinstance(result, list)
    assert len(result) == 2
    assert result[0][0] == "leave"
    assert result[1][0] == "accept"
    payload = mock_post.call_args[1]["data"]
    assert "ticket.getActions" in payload


@patch("trac_mcp_server.core.client.requests.Session.post")
def test_get_ticket_actions_not_found(mock_post, mock_config):
    """Test get_ticket_actions raises Fault when ticket not found."""
    import xmlrpc.client

    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.content = b"""<?xml version="1.0"?>
<methodResponse>
  <fault>
    <value>
      <struct>
        <member>
          <name>faultCode</name>
          <value><int>1</int></value>
        </member>
        <member>
          <name>faultString</name>
          <value><string>Ticket 99999 does not exist.</string></value>
        </member>
      </struct>
    </value>
  </fault>
</methodResponse>"""
    mock_post.return_value = mock_response

    client = TracClient(mock_config)

    with pytest.raises(xmlrpc.client.Fault) as exc_info:
        client.get_ticket_actions(99999)

    assert "does not exist" in str(exc_info.value)


@patch("trac_mcp_server.core.client.requests.Session.post")
def test_get_ticket_fields_success(mock_post, mock_config):
    """Test get_ticket_fields returns list of field definitions."""
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.content = b"""<?xml version="1.0"?>
<methodResponse>
  <params>
    <param>
      <value>
        <array>
          <data>
            <value>
              <struct>
                <member>
                  <name>name</name>
                  <value><string>summary</string></value>
                </member>
                <member>
                  <name>type</name>
                  <value><string>text</string></value>
                </member>
                <member>
                  <name>label</name>
                  <value><string>Summary</string></value>
                </member>
              </struct>
            </value>
            <value>
              <struct>
                <member>
                  <name>name</name>
                  <value><string>priority</string></value>
                </member>
                <member>
                  <name>type</name>
                  <value><string>select</string></value>
                </member>
                <member>
                  <name>label</name>
                  <value><string>Priority</string></value>
                </member>
              </struct>
            </value>
          </data>
        </array>
      </value>
    </param>
  </params>
</methodResponse>"""
    mock_post.return_value = mock_response

    client = TracClient(mock_config)
    result = client.get_ticket_fields()

    assert isinstance(result, list)
    assert len(result) == 2
    assert result[0]["name"] == "summary"
    assert result[0]["type"] == "text"
    assert result[1]["name"] == "priority"
    payload = mock_post.call_args[1]["data"]
    assert "ticket.getTicketFields" in payload


@patch("trac_mcp_server.core.client.requests.Session.post")
def test_get_ticket_fields_returns_custom_fields(
    mock_post, mock_config
):
    """Test get_ticket_fields includes custom field indicators."""
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.content = b"""<?xml version="1.0"?>
<methodResponse>
  <params>
    <param>
      <value>
        <array>
          <data>
            <value>
              <struct>
                <member>
                  <name>name</name>
                  <value><string>custom_field</string></value>
                </member>
                <member>
                  <name>type</name>
                  <value><string>text</string></value>
                </member>
                <member>
                  <name>label</name>
                  <value><string>Custom Field</string></value>
                </member>
                <member>
                  <name>custom</name>
                  <value><boolean>1</boolean></value>
                </member>
              </struct>
            </value>
          </data>
        </array>
      </value>
    </param>
  </params>
</methodResponse>"""
    mock_post.return_value = mock_response

    client = TracClient(mock_config)
    result = client.get_ticket_fields()

    assert len(result) == 1
    assert result[0]["name"] == "custom_field"
    assert result[0]["custom"] is True


# ---------------------------------------------------------------------------
# Wiki methods
# ---------------------------------------------------------------------------


@patch("trac_mcp_server.core.client.requests.Session.post")
def test_get_wiki_page_html_success(mock_post, mock_config):
    """Test get_wiki_page_html returns rendered HTML string."""
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.content = b"""<?xml version="1.0"?>
<methodResponse>
  <params>
    <param>
      <value><string>&lt;h1&gt;Page Title&lt;/h1&gt;&lt;p&gt;Content&lt;/p&gt;</string></value>
    </param>
  </params>
</methodResponse>"""
    mock_post.return_value = mock_response

    client = TracClient(mock_config)
    result = client.get_wiki_page_html("TestPage")

    assert isinstance(result, str)
    assert "<h1>" in result
    payload = mock_post.call_args[1]["data"]
    assert "wiki.getPageHTML" in payload


@patch("trac_mcp_server.core.client.requests.Session.post")
def test_get_wiki_page_html_with_version(mock_post, mock_config):
    """Test get_wiki_page_html with version calls getPageHTMLVersion."""
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.content = b"""<?xml version="1.0"?>
<methodResponse>
  <params>
    <param>
      <value><string>&lt;p&gt;Old content&lt;/p&gt;</string></value>
    </param>
  </params>
</methodResponse>"""
    mock_post.return_value = mock_response

    client = TracClient(mock_config)
    result = client.get_wiki_page_html("TestPage", version=2)

    assert isinstance(result, str)
    payload = mock_post.call_args[1]["data"]
    # Should call getPageHTMLVersion, not getPageHTML
    assert "wiki.getPageHTMLVersion" in payload
    assert "<int>2</int>" in payload


@patch("trac_mcp_server.core.client.requests.Session.post")
def test_delete_wiki_page_success(mock_post, mock_config):
    """Test delete_wiki_page returns True on success."""
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.content = b"""<?xml version="1.0"?>
<methodResponse>
  <params>
    <param>
      <value><boolean>1</boolean></value>
    </param>
  </params>
</methodResponse>"""
    mock_post.return_value = mock_response

    client = TracClient(mock_config)
    result = client.delete_wiki_page("OldPage")

    assert result is True
    payload = mock_post.call_args[1]["data"]
    assert "wiki.deletePage" in payload
    assert "OldPage" in payload


@patch("trac_mcp_server.core.client.requests.Session.post")
def test_get_recent_wiki_changes_success(mock_post, mock_config):
    """Test get_recent_wiki_changes returns list of change dicts."""
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.content = b"""<?xml version="1.0"?>
<methodResponse>
  <params>
    <param>
      <value>
        <array>
          <data>
            <value>
              <struct>
                <member>
                  <name>name</name>
                  <value><string>WikiStart</string></value>
                </member>
                <member>
                  <name>author</name>
                  <value><string>admin</string></value>
                </member>
                <member>
                  <name>lastModified</name>
                  <value><int>1700000000</int></value>
                </member>
                <member>
                  <name>version</name>
                  <value><int>5</int></value>
                </member>
              </struct>
            </value>
          </data>
        </array>
      </value>
    </param>
  </params>
</methodResponse>"""
    mock_post.return_value = mock_response

    client = TracClient(mock_config)
    result = client.get_recent_wiki_changes()

    assert isinstance(result, list)
    assert len(result) == 1
    assert result[0]["name"] == "WikiStart"
    assert result[0]["author"] == "admin"
    payload = mock_post.call_args[1]["data"]
    assert "wiki.getRecentChanges" in payload


# ---------------------------------------------------------------------------
# System methods
# ---------------------------------------------------------------------------


@patch("trac_mcp_server.core.client.requests.Session.post")
def test_list_methods_success(mock_post, mock_config):
    """Test list_methods returns list of available RPC method names."""
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.content = b"""<?xml version="1.0"?>
<methodResponse>
  <params>
    <param>
      <value>
        <array>
          <data>
            <value><string>system.listMethods</string></value>
            <value><string>ticket.get</string></value>
            <value><string>wiki.getPage</string></value>
          </data>
        </array>
      </value>
    </param>
  </params>
</methodResponse>"""
    mock_post.return_value = mock_response

    client = TracClient(mock_config)
    result = client.list_methods()

    assert isinstance(result, list)
    assert "system.listMethods" in result
    assert "ticket.get" in result
    assert "wiki.getPage" in result
    payload = mock_post.call_args[1]["data"]
    assert "system.listMethods" in payload


# ---------------------------------------------------------------------------
# XML-RPC <base64> parsing (attachment payloads)
# ---------------------------------------------------------------------------


@patch("trac_mcp_server.core.client.requests.Session.post")
def test_parse_base64_value(mock_post, mock_config):
    """_rpc_request decodes <base64> elements to raw bytes."""
    mock_response = Mock()
    mock_response.status_code = 200
    # "Hello, World!" base64-encoded
    mock_response.content = b"""<?xml version="1.0"?>
<methodResponse>
  <params>
    <param>
      <value><base64>SGVsbG8sIFdvcmxkIQ==</base64></value>
    </param>
  </params>
</methodResponse>"""
    mock_post.return_value = mock_response

    client = TracClient(mock_config)
    result = client._rpc_request(
        "ticket", "getAttachment", 1, "test.txt"
    )

    assert isinstance(result, bytes)
    assert result == b"Hello, World!"


@patch("trac_mcp_server.core.client.requests.Session.post")
def test_parse_base64_empty(mock_post, mock_config):
    """_rpc_request handles empty <base64/> element gracefully."""
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.content = b"""<?xml version="1.0"?>
<methodResponse>
  <params>
    <param>
      <value><base64></base64></value>
    </param>
  </params>
</methodResponse>"""
    mock_post.return_value = mock_response

    client = TracClient(mock_config)
    result = client._rpc_request(
        "ticket", "getAttachment", 1, "empty.txt"
    )
    assert result == b""


# --------------------------------------------------------------------------- #
# Ticket admin: components and enums
# --------------------------------------------------------------------------- #


def test_list_components_returns_list_of_dicts(mock_config):
    """Test list_components returns enriched component dicts."""
    client = TracClient(mock_config)

    call_log: list[tuple] = []

    def fake_rpc(service, method, *params):
        call_log.append((service, method, params))
        if method == "getAll":
            return ["a", "b"]
        if method == "get":
            if params[0] == "a":
                return {
                    "name": "a",
                    "owner": "alice",
                    "description": "first",
                }
            return {"name": "b", "owner": "bob", "description": ""}
        raise AssertionError(f"unexpected call: {service}.{method}")

    client._rpc_request = fake_rpc
    result = client.list_components()

    assert len(result) == 2
    assert result[0] == {
        "name": "a",
        "owner": "alice",
        "description": "first",
    }
    assert result[1] == {"name": "b", "owner": "bob", "description": ""}


def test_create_component_calls_xmlrpc_with_attrs(mock_config):
    """Test create_component forwards name and attributes dict to XML-RPC."""
    client = TracClient(mock_config)

    call_log: list[tuple] = []

    def fake_rpc(service, method, *params):
        call_log.append((service, method, params))
        return None

    client._rpc_request = fake_rpc
    client.create_component("new", description="desc", owner="")

    assert len(call_log) == 1
    assert call_log[0] == (
        "ticket.component",
        "create",
        ("new", {"description": "desc", "owner": ""}),
    )


def test_list_enum_priority_returns_list_of_strings(mock_config):
    """Test list_enum returns the raw list from the RPC call."""
    client = TracClient(mock_config)

    def fake_rpc(service, method, *params):
        return ["blocker", "critical", "major"]

    client._rpc_request = fake_rpc
    result = client.list_enum("priority")
    assert result == ["blocker", "critical", "major"]


def test_list_enum_invalid_type_raises_value_error(mock_config):
    """Test list_enum raises ValueError for unsupported enum type."""
    client = TracClient(mock_config)

    call_count = 0

    def fake_rpc(service, method, *params):
        nonlocal call_count
        call_count += 1
        return []

    client._rpc_request = fake_rpc

    with pytest.raises(
        ValueError, match="Unsupported enum_type 'nonsense'"
    ):
        client.list_enum("nonsense")

    assert call_count == 0, (
        "No RPC call should be made for invalid enum_type"
    )


def test_create_enum_calls_xmlrpc_with_service_name_from_enum_type(
    mock_config,
):
    """Test create_enum routes to the correct ticket.<enum_type>.create service."""
    client = TracClient(mock_config)

    call_log: list[tuple] = []

    def fake_rpc(service, method, *params):
        call_log.append((service, method, params))
        return None

    client._rpc_request = fake_rpc
    client.create_enum("priority", "urgent")

    assert len(call_log) == 1
    assert call_log[0] == ("ticket.priority", "create", ("urgent", 0))


def test_create_enum_version_sends_an_attributes_dict(mock_config):
    """A version is created from (name, attributes dict), not (name, sort order).

    TracXMLRPC models ``ticket.version`` with ticketModelFactory, so its
    create takes ``(name, attributes)``; the int the four real enums take
    fails live with ``'int' object has no attribute 'items'`` (#251).
    """
    client = TracClient(mock_config)

    call_log: list[tuple] = []

    def fake_rpc(service, method, *params):
        call_log.append((service, method, params))
        return None

    client._rpc_request = fake_rpc
    client.create_enum("version", "2.0")

    assert len(call_log) == 1
    assert call_log[0] == ("ticket.version", "create", ("2.0", {}))


def test_create_enum_version_rejects_a_sort_order(mock_config):
    """A non-zero ``value`` for a version raises, rather than being dropped.

    Versions have no sort order; silently ignoring a caller's ``value``
    would hide that it had no effect (#251). No RPC call is made.
    """
    client = TracClient(mock_config)

    call_count = 0

    def fake_rpc(service, method, *params):
        nonlocal call_count
        call_count += 1
        return None

    client._rpc_request = fake_rpc

    with pytest.raises(
        ValueError,
        match="value is not supported for enum_type 'version'",
    ):
        client.create_enum("version", "2.0", 5)

    assert call_count == 0, (
        "No RPC call should be made for a version with a sort order"
    )


def test_create_enum_invalid_type_raises_value_error(mock_config):
    """Test create_enum raises ValueError for unsupported enum type."""
    client = TracClient(mock_config)

    call_count = 0

    def fake_rpc(service, method, *params):
        nonlocal call_count
        call_count += 1
        return None

    client._rpc_request = fake_rpc

    with pytest.raises(
        ValueError, match="Unsupported enum_type 'bogus'"
    ):
        client.create_enum("bogus", "foo")

    assert call_count == 0, (
        "No RPC call should be made for invalid enum_type"
    )


# -- delete_component --------------------------------------------------------


def test_delete_component_sends_name(mock_config):
    """Test delete_component sends correct service/method/name to _rpc_request."""
    client = TracClient(mock_config)

    call_log: list[tuple] = []

    def fake_rpc(service, method, *params):
        call_log.append((service, method, params))
        return None

    client._rpc_request = fake_rpc
    client.delete_component("component1")

    assert len(call_log) == 1
    assert call_log[0] == (
        "ticket.component",
        "delete",
        ("component1",),
    )


# -- delete_enum -------------------------------------------------------------


def test_delete_enum_sends_name_and_type(mock_config):
    """Test delete_enum routes to ticket.<enum_type>.delete with correct name."""
    client = TracClient(mock_config)

    call_log: list[tuple] = []

    def fake_rpc(service, method, *params):
        call_log.append((service, method, params))
        return None

    client._rpc_request = fake_rpc
    client.delete_enum("type", "defect")

    assert len(call_log) == 1
    assert call_log[0] == ("ticket.type", "delete", ("defect",))


def test_delete_enum_rejects_invalid_type(mock_config):
    """Test delete_enum raises ValueError for unsupported enum type."""
    client = TracClient(mock_config)

    call_count = 0

    def fake_rpc(service, method, *params):
        nonlocal call_count
        call_count += 1
        return None

    client._rpc_request = fake_rpc

    with pytest.raises(
        ValueError, match="Unsupported enum_type 'bogus'"
    ):
        client.delete_enum("bogus", "foo")

    assert call_count == 0, (
        "No RPC call should be made for invalid enum_type"
    )


@pytest.mark.parametrize(
    "plugin", [False, True], ids=["precheck", "plugin"]
)
def test_put_wiki_page_float_version_never_raises_after_the_write(
    mock_config, plugin
):
    """A JSON-schema "integer" admits 3.0, and the precheck accepts it
    (3 == 3.0). The overwrite check runs after putPage stored the page,
    so it must never raise there: a TypeError would reach the caller as
    a generic failure for a stored write and invite a second write."""
    calls, rpc = _rpc_versions(precheck=3, readback=5, plugin=plugin)
    client = TracClient(mock_config)
    with patch.object(client, "_rpc_request", side_effect=rpc):
        result = client.put_wiki_page("X", "content", "c", version=3.0)

    assert result["possible_overwrite"] is True
    assert result["intervening_versions"] == [4]
    # The plugin takes an int: 3.0 is sent as 3.
    assert calls[0][1] == "putPageIfVersion"
    assert calls[0][-1] == 3 and type(calls[0][-1]) is int

    _, rpc = _rpc_versions(precheck=3, readback=4, plugin=plugin)
    with patch.object(client, "_rpc_request", side_effect=rpc):
        result = client.put_wiki_page("X", "content", "c", version=3.0)

    assert result["possible_overwrite"] is False
    assert "intervening_versions" not in result


def test_put_wiki_page_readback_below_expected_is_flagged_with_none_between(
    mock_config,
):
    """A read-back version below version + 1 (the page was deleted and
    recreated in between) is "not version + 1" too: flagged, with no
    intervening versions to name."""
    _, rpc = _rpc_versions(precheck=3, readback=2)
    client = TracClient(mock_config)
    with patch.object(client, "_rpc_request", side_effect=rpc):
        result = client.put_wiki_page("X", "content", "c", version=3)

    assert result["possible_overwrite"] is True
    assert result["intervening_versions"] == []


# ---------------------------------------------------------------------------
# TracClient.parse_comment_number (Trac /trac_mcp_server #131)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("oldvalue", "cnum", "reply_to"),
    [
        ("5", 5, None),
        ("2.5", 5, 2),
        ("1.10", 10, 1),
        (5, 5, None),
        ("  5 ", 5, None),
        (" 2.5\n", 5, 2),
        # A reply to the DESCRIPTION is "description.N" (Trac #207).
        ("description.5", 5, "description"),
        ("description.10", 10, "description"),
        ("  description.2 ", 2, "description"),
    ],
)
def test_parse_comment_number_accepts(oldvalue, cnum, reply_to):
    from trac_mcp_server.core.client import CommentNumber

    parsed = TracClient.parse_comment_number(oldvalue)
    assert parsed == CommentNumber(cnum=cnum, reply_to=reply_to)
    assert parsed.cnum == cnum
    assert parsed.reply_to == reply_to


@pytest.mark.parametrize(
    "oldvalue",
    [
        None,
        "",
        "   ",
        "abc",
        "²",  # '²'.isdigit() is True; int('²') raises
        "2.",
        ".5",
        "1.2.3",
        "2..5",
        "0",
        "-1",
        "2.0",
        "2.-1",
        "0.5",
        "-1.5",
        "a.5",
        "1_0",
        "+5",
        "\u0665",  # Arabic-Indic five: int() accepts it
        "2.\u0665",
        True,
        False,
        5.0,
        0,
        -3,
        ["5"],
        # Only the lowercase token "description", then a positive
        # number (Trac #207).
        "Description.5",
        "DESCRIPTION.5",
        "description.",
        "description.0",
        "description.5.1",
        "description.-1",
        "description.\u0665",
        "foo.5",
        "description",
        ".description",
        "5.description",
    ],
)
def test_parse_comment_number_rejects(oldvalue):
    assert TracClient.parse_comment_number(oldvalue) is None


# ticket.getChangeAt (Trac /trac_mcp_server #132)


def test_get_ticket_change_at_returns_the_plugins_list(mock_config):
    client = TracClient(mock_config)
    with patch.object(
        client, "_rpc_request", return_value=["5", "alice", "text"]
    ) as rpc:
        result = client.get_ticket_change_at(7, "1762257600123456")

    assert result == ["5", "alice", "text"]
    rpc.assert_called_once_with(
        "ticket", "getChangeAt", 7, "1762257600123456"
    )


def _missing_getchangeat(*args):
    raise xmlrpc.client.Fault(
        -32601, 'RPC method "ticket.getChangeAt" not found'
    )


def test_get_ticket_change_at_missing_method_returns_none_and_caches(
    mock_config,
):
    client = TracClient(mock_config)
    with patch.object(
        client, "_rpc_request", side_effect=_missing_getchangeat
    ) as rpc:
        assert client.get_ticket_change_at(7, "1") is None
        assert client.get_ticket_change_at(7, "1") is None
        assert client.get_ticket_change_at(8, "2") is None

    assert rpc.call_count == 1


def test_get_ticket_change_at_tries_again_after_the_ttl(mock_config):
    from trac_mcp_server.core import client as client_module

    client = TracClient(mock_config)
    now = [1000.0]
    with patch.object(
        client_module.time, "monotonic", side_effect=lambda: now[0]
    ):
        with patch.object(
            client, "_rpc_request", side_effect=_missing_getchangeat
        ) as rpc:
            client.get_ticket_change_at(7, "1")
            now[0] += (
                client_module.TICKET_CHANGE_AT_ABSENT_TTL_SECONDS - 1
            )
            client.get_ticket_change_at(7, "1")
            assert rpc.call_count == 1

            now[0] += 2
            client.get_ticket_change_at(7, "1")
            assert rpc.call_count == 2

        # The plugin arrived meanwhile: the next call gets the answer.
        now[0] += client_module.TICKET_CHANGE_AT_ABSENT_TTL_SECONDS + 1
        with patch.object(
            client, "_rpc_request", return_value=["1", "a", "t"]
        ):
            assert client.get_ticket_change_at(7, "1") == [
                "1",
                "a",
                "t",
            ]


def test_get_ticket_change_at_ttl_is_five_minutes():
    from trac_mcp_server.core import client as client_module

    assert client_module.TICKET_CHANGE_AT_ABSENT_TTL_SECONDS == 300.0


def test_get_ticket_change_at_other_faults_propagate(mock_config):
    client = TracClient(mock_config)
    fault = xmlrpc.client.Fault(
        1, "TICKET_VIEW privileges are required"
    )
    with patch.object(client, "_rpc_request", side_effect=fault):
        with pytest.raises(xmlrpc.client.Fault) as exc_info:
            client.get_ticket_change_at(7, "1")

    assert exc_info.value is fault
    # An unrelated fault is not remembered as "plugin absent".
    with patch.object(client, "_rpc_request", return_value=[]) as rpc:
        assert client.get_ticket_change_at(7, "1") == []
    rpc.assert_called_once()


def test_get_ticket_change_at_page_not_found_is_not_a_missing_method(
    mock_config,
):
    client = TracClient(mock_config)
    fault = xmlrpc.client.Fault(
        404, "Ticket 9 does not exist.  not found"
    )
    with patch.object(client, "_rpc_request", side_effect=fault):
        with pytest.raises(xmlrpc.client.Fault):
            client.get_ticket_change_at(9, "1")


@patch("trac_mcp_server.core.client.requests.Session.post")
def test_get_recent_wiki_changes_zero_asks_from_the_epoch(
    mock_post, mock_config
):
    """``xmlrpc.client.DateTime(0)`` is "now" (a falsy value means the
    current time), so the documented default of 0, "all changes", asked
    for nothing. The wiki search lists every page this way."""
    mock_post.return_value = _rpc_result_response([])
    client = TracClient(mock_config)
    assert client.get_recent_wiki_changes(0) == []
    (params,), method = xmlrpc.client.loads(
        mock_post.call_args.kwargs["data"]
    )
    assert method == "wiki.getRecentChanges"
    assert params.value == "19700101T00:00:00"


def _date_header_response(date_header: str) -> Mock:
    response = Mock()
    response.status_code = 200
    response.headers = {"Date": date_header}
    response.content = b"""<?xml version="1.0"?>
<methodResponse>
  <params>
    <param>
      <value><string>1.0</string></value>
    </param>
  </params>
</methodResponse>"""
    return response


@patch("trac_mcp_server.core.client.requests.Session.post")
def test_get_server_time_minus_zero_offset_is_aware_utc(
    mock_post, mock_config
):
    """A ``-0000`` Date header parses to a naive datetime; it is UTC."""
    mock_post.return_value = _date_header_response(
        "Mon, 17 Aug 2026 17:06:07 -0000"
    )
    result = TracClient(mock_config).get_server_time()
    assert result.tzinfo is not None
    assert result.utcoffset() == timedelta(0)
    assert result == datetime(
        2026, 8, 17, 17, 6, 7, tzinfo=timezone.utc
    )


@patch("trac_mcp_server.core.client.requests.Session.post")
def test_get_server_time_non_gmt_offset_is_converted_to_utc(
    mock_post, mock_config
):
    """A ``+0200`` Date header is the same instant, reported in UTC."""
    mock_post.return_value = _date_header_response(
        "Mon, 17 Aug 2026 19:06:07 +0200"
    )
    result = TracClient(mock_config).get_server_time()
    assert result.utcoffset() == timedelta(0)
    assert result == datetime(
        2026, 8, 17, 17, 6, 7, tzinfo=timezone.utc
    )
    assert (result.hour, result.day) == (17, 17)
