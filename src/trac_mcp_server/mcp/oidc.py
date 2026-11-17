"""Per-user OIDC bearer-token auth for the http transport.

Lets a shared deployment (e.g. one trac-mcp-server serving many LibreChat
users behind a single Keycloak-backed Trac endpoint) act as *each caller's
own* Trac identity instead of one shared service account. Every request
must carry the caller's own OIDC access token in the standard
``Authorization: Bearer <token>`` header; that token is forwarded verbatim
to the operator-configured OIDC-protected Trac endpoint
(``ServerConfig.oidc_rpc_url``). Trac's own web server (mod_auth_openidc)
validates the token and maps it to a Trac username -- this module does not
decode, verify, or cache anything about the token's contents.

The standard header is used deliberately, not a custom one: an MCP client
that performs its own OAuth flow per server (LibreChat's ``oauth:`` config,
matching the MCP spec's authorization flow) has no way to attach anything
but a normal ``Authorization`` header -- it isn't driving a bespoke
per-server header scheme, it's acting as a generic OAuth client. Because of
that, this mode and ``ServerConfig.auth_token`` (the static shared-secret
gate) are mutually exclusive -- both would need the same header for
different purposes; see ``config.validate_server_config``.

There is deliberately no fallback to a shared identity anywhere in this
module: a missing or malformed token is always an error, never a silent
substitution.
"""

from __future__ import annotations

import dataclasses
import hashlib
import logging
import threading
import time
from collections import OrderedDict
from collections.abc import Callable

from ..config import Config
from ..core.client import TracClient

logger = logging.getLogger(__name__)

# Bounds memory for a long-running process seeing many distinct short-lived
# Keycloak tokens (roughly one per user session). Plain FIFO eviction is
# enough here -- this is a connection-pooling cache, not a security
# boundary, so an imperfect LRU is not worth the extra bookkeeping.
_MAX_CACHED_CLIENTS = 256

# Tokens Trac answered 401 to (Trac /trac_mcp_server #205). Only a digest
# is kept. An entry outlives any access token a deployment would plausibly
# issue, so it is dropped by age rather than held until eviction; FIFO
# eviction bounds the list for a process that sees many distinct tokens.
_MAX_REJECTED_TOKENS = 1024
_REJECTED_TTL_SECONDS = 3600


def extract_bearer_token(
    authorization_header: str | None,
) -> str | None:
    """Pull the token out of an ``Authorization: Bearer <token>`` value.

    Returns None if the header is missing, blank, uses a different scheme,
    or has an empty token -- callers treat all of those identically (see
    OidcClientCache.get_client's MissingOidcTokenError).
    """
    if not authorization_header:
        return None
    scheme, _, token = authorization_header.partition(" ")
    if scheme.lower() != "bearer":
        return None
    token = token.strip()
    return token or None


class MissingOidcTokenError(ValueError):
    """Raised when a request needs a per-user token and none was provided."""


class OidcClientCache:
    """Builds and caches a per-token :class:`TracClient`.

    ``base_config`` supplies everything except identity (SSL, proxies,
    timeouts, ``trac_url`` for cosmetic page-URL construction); identity is
    replaced per token with :class:`TracClient` targeting ``oidc_rpc_url``.
    """

    def __init__(
        self,
        base_config: Config,
        oidc_rpc_url: str,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._base_config = base_config
        self._oidc_rpc_url = oidc_rpc_url
        self._clients: OrderedDict[str, TracClient] = OrderedDict()
        self._lock = threading.Lock()
        self._clock = clock
        # sha256 hex digest of a rejected token -> when it was recorded.
        # Written from the worker threads that run tool calls, read from
        # the event loop, hence its own lock.
        self._rejected: OrderedDict[str, float] = OrderedDict()
        self._rejected_lock = threading.Lock()

    @staticmethod
    def _digest(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    def mark_rejected(self, token: str) -> None:
        """Remember that Trac answered 401 to ``token``.

        The call that got the 401 cannot itself be answered 401 (the
        response stream has started by then), so the middleware answers
        the next request carrying this token instead; see
        :meth:`is_rejected`. Marking a token again refreshes its age.
        """
        key = self._digest(token)
        with self._rejected_lock:
            self._rejected.pop(key, None)
            self._rejected[key] = self._clock()
            while len(self._rejected) > _MAX_REJECTED_TOKENS:
                self._rejected.popitem(last=False)

    def is_rejected(self, token: str) -> bool:
        """Whether ``token`` was marked within the last hour."""
        key = self._digest(token)
        with self._rejected_lock:
            recorded = self._rejected.get(key)
            if recorded is None:
                return False
            if self._clock() - recorded >= _REJECTED_TTL_SECONDS:
                del self._rejected[key]
                return False
            return True

    def get_client(self, token: str | None) -> TracClient:
        """Return the cached client for ``token``, creating one if needed.

        Raises:
            MissingOidcTokenError: If ``token`` is empty/blank/None.
        """
        if not token or not token.strip():
            raise MissingOidcTokenError(
                "Missing or malformed 'Authorization: Bearer <token>' "
                "header. This server requires each request to carry its "
                "own per-user Trac OIDC access token; there is no shared "
                "fallback identity."
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
            client = TracClient(
                config,
                on_unauthorized=lambda token=token: self.mark_rejected(
                    token
                ),
            )
            self._clients[token] = client
            evicted = None
            if len(self._clients) > _MAX_CACHED_CLIENTS:
                _, evicted = self._clients.popitem(last=False)
        if evicted is not None:
            evicted.close()
        return client
