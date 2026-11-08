"""The MCP SDK 1.30.0 "Error in standalone SSE writer" record on session
close (Trac /trac_mcp_server #169).

With ``mcp==1.30.0`` on the SERVER side, every streamable-http session
close logs ``Error in standalone SSE writer`` at ERROR (``logger.exception``,
``exc_info`` holding an ``anyio.ClosedResourceError``) from the logger
``mcp.server.streamable_http``: the client's DELETE terminates the
transport, which closes the receive stream the standalone GET writer is
iterating. The session ends normally; the record is noise.
``build_http_app`` installs ``_SseWriterCloseFilter`` on that logger.

Sections: unit tests of the filter on synthetic ``LogRecord`` s, an
install test, and one real-substrate test (real uvicorn + the SDK's real
``streamablehttp_client``) that guards the SDK behaviour end to end.
"""

import logging
import sys
import time

import anyio
import pytest
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from tests.test_mcp.test_http_identity import (
    _LEGACY_TOKEN,
    _free_port,
    _make_identity_probe_server,
    _probe_server_config,
    _running_app,
)
from trac_mcp_server.mcp.http_app import (
    _SseWriterCloseFilter,
    build_http_app,
)

_SDK_LOGGER = "mcp.server.streamable_http"
_MESSAGE = "Error in standalone SSE writer"
_WRITER_CLOSED = "Closing standalone SSE writer"


def _record(
    *,
    name: str = _SDK_LOGGER,
    msg: str = _MESSAGE,
    exc: BaseException | None = None,
) -> logging.LogRecord:
    """A synthetic ERROR record; ``exc`` is raised and caught so
    ``exc_info`` is real, as ``logger.exception`` would build it."""
    exc_info = None
    if exc is not None:
        try:
            raise exc
        except BaseException:
            exc_info = sys.exc_info()
    return logging.LogRecord(
        name=name,
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg=msg,
        args=(),
        exc_info=exc_info,
    )


class TestSseWriterCloseFilter:
    def test_a_matching_record_is_dropped_when_the_logger_is_at_info(
        self, caplog
    ):
        caplog.set_level(logging.INFO, logger=_SDK_LOGGER)
        record = _record(exc=anyio.ClosedResourceError())
        assert _SseWriterCloseFilter().filter(record) is False

    def test_a_matching_record_is_kept_but_demoted_at_debug(
        self, caplog
    ):
        caplog.set_level(logging.DEBUG, logger=_SDK_LOGGER)
        record = _record(exc=anyio.ClosedResourceError())
        assert _SseWriterCloseFilter().filter(record) is True
        assert record.levelno == logging.DEBUG
        assert record.levelname == "DEBUG"

    def test_the_same_message_with_another_exception_stays_error(
        self, caplog
    ):
        caplog.set_level(logging.INFO, logger=_SDK_LOGGER)
        record = _record(exc=RuntimeError("boom"))
        assert _SseWriterCloseFilter().filter(record) is True
        assert record.levelno == logging.ERROR
        assert record.levelname == "ERROR"

    def test_another_message_with_the_same_exception_stays_error(
        self, caplog
    ):
        caplog.set_level(logging.INFO, logger=_SDK_LOGGER)
        record = _record(
            msg="Error in standalone SSE response",
            exc=anyio.ClosedResourceError(),
        )
        assert _SseWriterCloseFilter().filter(record) is True
        assert record.levelno == logging.ERROR
        assert record.levelname == "ERROR"

    def test_the_same_record_from_another_logger_stays_error(
        self, caplog
    ):
        caplog.set_level(logging.INFO, logger=_SDK_LOGGER)
        record = _record(
            name="some.other.logger", exc=anyio.ClosedResourceError()
        )
        assert _SseWriterCloseFilter().filter(record) is True
        assert record.levelno == logging.ERROR
        assert record.levelname == "ERROR"

    def test_a_record_without_exc_info_passes_unchanged(self, caplog):
        caplog.set_level(logging.INFO, logger=_SDK_LOGGER)
        record = _record()
        assert record.exc_info is None
        assert _SseWriterCloseFilter().filter(record) is True
        assert record.levelno == logging.ERROR
        assert record.levelname == "ERROR"


@pytest.fixture
def clean_sdk_logger_filters():
    """Leave no ``_SseWriterCloseFilter`` on the SDK logger afterwards,
    whatever ``build_http_app`` installed during the test."""
    lg = logging.getLogger(_SDK_LOGGER)

    def _strip() -> None:
        for f in [
            f
            for f in lg.filters
            if isinstance(f, _SseWriterCloseFilter)
        ]:
            lg.removeFilter(f)

    _strip()
    yield lg
    _strip()


class TestFilterInstall:
    def test_build_http_app_twice_installs_exactly_one_filter(
        self, clean_sdk_logger_filters
    ):
        lg = clean_sdk_logger_filters
        config = _probe_server_config(_free_port())
        build_http_app(_make_identity_probe_server(), config)
        build_http_app(_make_identity_probe_server(), config)
        installed = [
            f
            for f in lg.filters
            if isinstance(f, _SseWriterCloseFilter)
        ]
        assert len(installed) == 1


class TestSessionCloseIsQuiet:
    def test_closing_a_session_logs_no_error_from_the_sdk_transport(
        self, clean_sdk_logger_filters, caplog
    ):
        """Closing a streamable-http session (the client's DELETE) must
        not log an ERROR from the SDK's server transport (#169).

        Real uvicorn server (in its own thread) and the SDK's real
        client, no mocks. The SDK logs ``Closing standalone SSE writer``
        at DEBUG from ``standalone_sse_writer``'s ``finally`` on both
        1.29.1 and 1.30.0; the test waits for that record, so it cannot
        pass before the close path has run, and fails with a clear
        message if the client's GET stream or the close never happened.
        It then asserts nothing from ``mcp.server.streamable_http`` is
        at ERROR or above, and that any ``Error in standalone SSE
        writer`` record is at DEBUG.

        On SDK 1.29.1 this proves the close path ran cleanly. On 1.30.0
        the writer logs ``Error in standalone SSE writer`` at ERROR with
        a ``ClosedResourceError``, and this test fails without the
        filter ``build_http_app`` installs; with it the record is
        demoted to DEBUG and kept, because DEBUG is enabled here.
        """
        caplog.set_level(logging.DEBUG, logger=_SDK_LOGGER)

        def _sdk_records() -> list[logging.LogRecord]:
            return [
                r for r in list(caplog.records) if r.name == _SDK_LOGGER
            ]

        async def _body():
            port = _free_port()
            with _running_app(
                _make_identity_probe_server(),
                _probe_server_config(port),
            ) as base_url:
                async with streamablehttp_client(
                    f"{base_url}/mcp",
                    headers={
                        "Authorization": f"Bearer {_LEGACY_TOKEN}"
                    },
                ) as (read, write, _get_session_id):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        await session.list_tools()
                # The session is closed (DELETE sent). Wait up to ~5 s
                # for the server's writer cleanup to log its record.
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    if any(
                        r.getMessage() == _WRITER_CLOSED
                        for r in _sdk_records()
                    ):
                        return
                    await anyio.sleep(0.05)

        anyio.run(_body)

        records = _sdk_records()
        seen = [(r.levelname, r.getMessage()) for r in records]
        assert any(r.getMessage() == _WRITER_CLOSED for r in records), (
            f"never saw the SDK's {_WRITER_CLOSED!r} record within 5 s "
            "of closing the session: the close path, or the client's "
            f"GET stream, did not happen. Records seen: {seen}"
        )
        assert [
            (r.levelname, r.getMessage())
            for r in records
            if r.levelno >= logging.ERROR
        ] == []
        assert [
            r.levelname for r in records if r.getMessage() == _MESSAGE
        ] in ([], ["DEBUG"])
