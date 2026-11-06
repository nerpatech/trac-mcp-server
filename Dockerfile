# Runs trac-mcp-server over the streamable HTTP transport (see
# docs/reference/http-transport.md). The stdio transport isn't meant for
# container use -- there's no client on the other end of stdin/stdout -- so
# the image sets the http transport in the environment. Arguments to
# `docker run` replace CMD only and keep it, e.g.
#   docker run ... trac-mcp-server --file-access off
#
# Settings, all by environment (or a mounted config.yaml):
#   TRAC_URL, TRAC_USERNAME, TRAC_PASSWORD   the Trac connection
#   TRAC_MCP_AUTH_TOKEN    required: without auth the server refuses to
#                          bind 0.0.0.0
#   TRAC_MCP_ALLOWED_HOSTS required for any client that reaches the
#                          container by a name other than localhost, e.g.
#                          a compose service name: trac-mcp:8080. Without
#                          it such a client gets "421 Invalid Host header"
#                          while the container still reports healthy,
#                          because /healthz skips that check.
#   TRAC_MCP_PORT          listen port, default 8080; the health check
#                          follows it

FROM python:3.12-slim AS build

WORKDIR /build

# Only the build metadata + source tree are needed to install the package;
# keeping this separate from the runtime stage keeps the final image free
# of pip's build cache and any compiler toolchain pulled in transitively.
COPY pyproject.toml README.md LICENSE ./
COPY constraints/ constraints/
COPY src/ src/

# Installed against the deploy pins, the tested dependency set.
RUN pip install --no-cache-dir --prefix=/install -c constraints/deploy.txt .

FROM python:3.12-slim AS runtime

COPY --from=build /install /usr/local

RUN useradd --create-home --uid 1000 trac-mcp
USER trac-mcp
WORKDIR /home/trac-mcp

ENV TRAC_MCP_TRANSPORT=http TRAC_MCP_HOST=0.0.0.0 TRAC_MCP_PORT=8080

EXPOSE 8080

HEALTHCHECK --interval=10s --timeout=5s --retries=12 --start-period=15s \
    CMD python -c "import os, urllib.request; urllib.request.urlopen('http://localhost:%s/healthz' % os.environ.get('TRAC_MCP_PORT', '8080'))"

ENTRYPOINT ["trac-mcp-server"]
