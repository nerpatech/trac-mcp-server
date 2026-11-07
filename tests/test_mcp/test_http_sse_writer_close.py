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
from mcp import types
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from mcp.server import Server

from tests.test_mcp.test_http_identity import _free_port, _running_app
from trac_mcp_server import __version__
from trac_mcp_server.config_schema import ServerConfig
from trac_mcp_server.mcp.http_app import (
    _SseWriterCloseFilter,
    build_http_app,
)

_SDK_LOGGER = "mcp.server.streamable_http"
_MESSAGE = "Error in standalone SSE writer"
_TOKEN = "sse-close-token"


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


@pytest.fixture
def sdk_logger_level():
    """Set the SDK logger's level for one test, restoring it after."""
    lg = logging.getLogger(_SDK_LOGGER)
    saved = lg.level

    def _set(level: int) -> None:
        lg.setLevel(level)

    yield _set
    lg.setLevel(saved)


class TestSseWriterCloseFilter:
    def test_a_matching_record_is_dropped_when_the_logger_is_at_info(
        self, sdk_logger_level
    ):
        sdk_logger_level(logging.INFO)
        record = _record(exc=anyio.ClosedResourceError())
        assert _SseWriterCloseFilter().filter(record) is False

    def test_a_matching_record_is_kept_but_demoted_at_debug(
        self, sdk_logger_level
    ):
        sdk_logger_level(logging.DEBUG)
        record = _record(exc=anyio.ClosedResourceError())
        assert _SseWriterCloseFilter().filter(record) is True
        assert record.levelno == logging.DEBUG
        assert record.levelname == "DEBUG"

    def test_the_same_message_with_another_exception_stays_error(
        self, sdk_logger_level
    ):
        sdk_logger_level(logging.INFO)
        record = _record(exc=RuntimeError("boom"))
        assert _SseWriterCloseFilter().filter(record) is True
        assert record.levelno == logging.ERROR
        assert record.levelname == "ERROR"

    def test_another_message_with_the_same_exception_stays_error(
        self, sdk_logger_level
    ):
        sdk_logger_level(logging.INFO)
        record = _record(
            msg="Error in standalone SSE response",
            exc=anyio.ClosedResourceError(),
        )
        assert _SseWriterCloseFilter().filter(record) is True
        assert record.levelno == logging.ERROR
        assert record.levelname == "ERROR"

    def test_the_same_record_from_another_logger_stays_error(
        self, sdk_logger_level
    ):
        sdk_logger_level(logging.INFO)
        record = _record(
            name="some.other.logger", exc=anyio.ClosedResourceError()
        )
        assert _SseWriterCloseFilter().filter(record) is True
        assert record.levelno == logging.ERROR
        assert record.levelname == "ERROR"

    def test_a_record_without_exc_info_passes_unchanged(
        self, sdk_logger_level
    ):
        sdk_logger_level(logging.INFO)
        record = _record()
        assert record.exc_info is None
        assert _SseWriterCloseFilter().filter(record) is True
        assert record.levelno == logging.ERROR
        assert record.levelname == "ERROR"


def _probe_config(port: int) -> ServerConfig:
    return ServerConfig(
        transport="http",
        host="127.0.0.1",
        port=port,
        path="/mcp",
        auth_token=_TOKEN,
    )


def _empty_server() -> Server:
    probe = Server("sse-close-probe", version=__version__)

    @probe.list_tools()
    async def _list_tools() -> list[types.Tool]:
        return []

    return probe


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
        config = _probe_config(_free_port())
        build_http_app(_empty_server(), config)
        build_http_app(_empty_server(), config)
        installed = [
            f
            for f in lg.filters
            if isinstance(f, _SseWriterCloseFilter)
        ]
        assert len(installed) == 1


class _Collect(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.ERROR)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


class TestSessionCloseIsQuiet:
    def test_closing_a_session_logs_no_error_from_the_sdk_transport(
        self, clean_sdk_logger_filters, sdk_logger_level
    ):
        """Guards SDK 1.30.0 behaviour: there, closing a streamable-http
        session (the client's DELETE) made the server's standalone SSE
        writer log ``Error in standalone SSE writer`` at ERROR with a
        ``ClosedResourceError``. On SDK 1.29.1 the SDK never logs it, so
        this passes trivially there; on 1.30.0 it fails without the
        filter ``build_http_app`` installs (#169).

        Real uvicorn server and the SDK's real client, no mocks. Every
        ERROR-or-above record from ``mcp.server.streamable_http`` is
        collected by a handler on that logger; a demoted record is DEBUG
        and is not collected.
        """
        sdk_logger_level(logging.INFO)
        lg = clean_sdk_logger_filters
        handler = _Collect()
        lg.addHandler(handler)

        async def _body():
            port = _free_port()
            with _running_app(
                _empty_server(), _probe_config(port)
            ) as base_url:
                async with streamablehttp_client(
                    f"{base_url}/mcp",
                    headers={"Authorization": f"Bearer {_TOKEN}"},
                ) as (read, write, _get_session_id):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        await session.list_tools()
                # The session is closed (DELETE sent). Give the server
                # up to ~3 s to process it and run the writer's cleanup.
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline:
                    if handler.records:
                        break
                    await anyio.sleep(0.1)

        try:
            anyio.run(_body)
        finally:
            lg.removeHandler(handler)

        assert [
            (r.levelname, r.getMessage()) for r in handler.records
        ] == []
