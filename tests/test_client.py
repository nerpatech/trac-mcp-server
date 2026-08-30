import calendar
import socket
from datetime import datetime, timezone
from unittest.mock import Mock, patch

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


def _ticket_get_response(tid, summary, created="20260812T12:00:00"):
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
def test_get_wiki_page_with_metadata_success(mock_post, mock_config):
    """Test get_wiki_page_with_metadata combining content and metadata."""
    # First response for getPage
    get_response = Mock()
    get_response.status_code = 200
    get_response.content = b"""<?xml version="1.0"?>
<methodResponse>
  <params>
    <param>
      <value><string>= Wiki Content =</string></value>
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
            <value><string>TestPage</string></value>
          </member>
          <member>
            <name>author</name>
            <value><string>john</string></value>
          </member>
          <member>
            <name>version</name>
            <value><int>5</int></value>
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

    mock_post.side_effect = [get_response, info_response]

    client = TracClient(mock_config)
    result = client.get_wiki_page_with_metadata("TestPage")

    # Verify combined result
    assert result["name"] == "TestPage"
    assert result["content"] == "= Wiki Content ="
    assert result["version"] == 5
    assert result["author"] == "john"
    assert result["lastModified"] == 1234567890

    # Verify both calls made
    assert mock_post.call_count == 2


@patch("trac_mcp_server.core.client.requests.Session.post")
def test_get_wiki_page_with_metadata_not_found_with_suggestions(
    mock_post, mock_config
):
    """Test get_wiki_page_with_metadata with suggestions on not found."""
    # First response for getPage (fault)
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
          <value><string>No such page</string></value>
        </member>
      </struct>
    </value>
  </fault>
</methodResponse>"""

    # Second response for getAllPages
    pages_response = Mock()
    pages_response.status_code = 200
    pages_response.content = b"""<?xml version="1.0"?>
<methodResponse>
  <params>
    <param>
      <value>
        <array>
          <data>
            <value><string>WikiStart</string></value>
            <value><string>TestPage</string></value>
            <value><string>TestPageTwo</string></value>
          </data>
        </array>
      </value>
    </param>
  </params>
</methodResponse>"""

    mock_post.side_effect = [fault_response, pages_response]

    client = TracClient(mock_config)

    with pytest.raises(ValueError) as exc_info:
        client.get_wiki_page_with_metadata("Test")

    # Verify error message includes suggestions
    error_msg = str(exc_info.value)
    assert "Page 'Test' not found" in error_msg
    assert "Similar pages:" in error_msg
    assert "TestPage" in error_msg


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


@patch("trac_mcp_server.core.client.requests.Session.post")
def test_search_wiki_pages_by_content(mock_post, mock_config):
    """Test search_wiki_pages_by_content method."""
    # First response for getAllPages
    pages_response = Mock()
    pages_response.status_code = 200
    pages_response.content = b"""<?xml version="1.0"?>
<methodResponse>
  <params>
    <param>
      <value>
        <array>
          <data>
            <value><string>Page1</string></value>
            <value><string>Page2</string></value>
          </data>
        </array>
      </value>
    </param>
  </params>
</methodResponse>"""

    # Second response for getPage("Page1")
    page1_response = Mock()
    page1_response.status_code = 200
    page1_response.content = b"""<?xml version="1.0"?>
<methodResponse>
  <params>
    <param>
      <value><string>no match here</string></value>
    </param>
  </params>
</methodResponse>"""

    # Third response for getPage("Page2")
    page2_response = Mock()
    page2_response.status_code = 200
    page2_response.content = b"""<?xml version="1.0"?>
<methodResponse>
  <params>
    <param>
      <value><string>found the target keyword here</string></value>
    </param>
  </params>
</methodResponse>"""

    mock_post.side_effect = [
        pages_response,
        page1_response,
        page2_response,
    ]

    client = TracClient(mock_config)
    result = client.search_wiki_pages_by_content("target")

    # Verify only Page2 returned
    assert len(result) == 1
    assert result[0]["name"] == "Page2"
    assert "target" in result[0]["snippet"]


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

    mock_post.side_effect = [put_response, info_response]

    client = TracClient(mock_config)
    result = client.put_wiki_page(
        "ExistingPage", "Updated content", "Minor fix", version=2
    )

    # Verify version was passed to RPC
    first_payload = mock_post.call_args_list[0][1]["data"]
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


@patch("trac_mcp_server.core.client.requests.Session.post")
def test_put_wiki_page_version_conflict(mock_post, mock_config):
    """Test put_wiki_page handles version conflict errors."""
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
          <value><string>Page has been modified, version conflict detected</string></value>
        </member>
      </struct>
    </value>
  </fault>
</methodResponse>"""

    mock_post.return_value = fault_response

    client = TracClient(mock_config)

    with pytest.raises(ValueError) as exc_info:
        client.put_wiki_page("Page", "content", "comment", version=1)

    assert "Version conflict" in str(exc_info.value)


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
    """_parse_xmlrpc_value decodes <base64> elements to raw bytes."""
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
    """_parse_xmlrpc_value handles empty <base64/> element gracefully."""
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
