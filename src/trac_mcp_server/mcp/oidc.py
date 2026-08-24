"""Per-user OIDC bearer-token auth for the http transport.

Lets a shared deployment (e.g. one trac-mcp-server serving many LibreChat
users behind a single Keycloak-backed Trac endpoint) act as *each caller's
own* Trac identity instead of one shared service account. Every request
must carry the caller's own OIDC access token in the ``X-Trac-OIDC-Token``
header; that token is forwarded verbatim as ``Authorization: Bearer
<token>`` to the operator-configured OIDC-protected Trac endpoint
(``ServerConfig.oidc_rpc_url``). Trac's own web server (mod_auth_openidc)
validates the token and maps it to a Trac username -- this module does not
decode, verify, or cache anything about the token's contents.

There is deliberately no fallback to a shared identity anywhere in this
module: a missing token is always an error, never a silent substitution.
"""

from __future__ import annotations

import dataclasses
import logging
import threading
from collections import OrderedDict

from ..config import Config
from ..core.client import TracClient

logger = logging.getLogger(__name__)

# Starlette normalizes header names to lowercase in Headers/Request.headers,
# and raw ASGI scope headers arrive as lowercase byte strings -- this
# constant is used both ways, so keep it lowercase at the source.
OIDC_TOKEN_HEADER = "x-trac-oidc-token"

# Bounds memory for a long-running process seeing many distinct short-lived
# Keycloak tokens (roughly one per user session). Plain FIFO eviction is
# enough here -- this is a connection-pooling cache, not a security
# boundary, so an imperfect LRU is not worth the extra bookkeeping.
_MAX_CACHED_CLIENTS = 256


class MissingOidcTokenError(ValueError):
    """Raised when a request needs a per-user token and none was provided."""


class OidcClientCache:
    """Builds and caches a per-token :class:`TracClient`.

    ``base_config`` supplies everything except identity (SSL, proxies,
    timeouts, ``trac_url`` for cosmetic page-URL construction); identity is
    replaced per token with :class:`TracClient` targeting ``oidc_rpc_url``.
    """

    def __init__(self, base_config: Config, oidc_rpc_url: str) -> None:
        self._base_config = base_config
        self._oidc_rpc_url = oidc_rpc_url
        self._clients: OrderedDict[str, TracClient] = OrderedDict()
        self._lock = threading.Lock()

    def get_client(self, token: str) -> TracClient:
        """Return the cached client for ``token``, creating one if needed.

        Raises:
            MissingOidcTokenError: If ``token`` is empty/blank.
        """
        if not token or not token.strip():
            raise MissingOidcTokenError(
                f"Missing {OIDC_TOKEN_HEADER!r} header. This server requires "
                "each request to carry its own per-user Trac OIDC access "
                "token; there is no shared fallback identity."
            )
        token = token.strip()
        with self._lock:
            client = self._clients.get(token)
            if client is not None:
                self._clients.move_to_end(token)
                return client

            config = dataclasses.replace(
                self._base_config,
                username="",
                password="",
                bearer_token=token,
                rpc_url_override=self._oidc_rpc_url,
                oidc_only=True,
            )
            client = TracClient(config)
            self._clients[token] = client
            if len(self._clients) > _MAX_CACHED_CLIENTS:
                self._clients.popitem(last=False)
            return client
