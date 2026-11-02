# Runs trac-mcp-server over the streamable HTTP transport (see
# docs/reference/http-transport.md). The stdio transport this image also
# supports isn't meant for container use -- there's no client on the other
# end of stdin/stdout inside a container -- so CMD defaults to --transport
# http; override it in docker-compose.yml (or `docker run`) if you need
# something else.

FROM python:3.12-slim AS build

WORKDIR /build

# Only the build metadata + source tree are needed to install the package;
# keeping this separate from the runtime stage keeps the final image free
# of pip's build cache and any compiler toolchain pulled in transitively.
COPY pyproject.toml README.md LICENSE ./
COPY src/ src/

RUN pip install --no-cache-dir --prefix=/install .

FROM python:3.12-slim AS runtime

COPY --from=build /install /usr/local

RUN useradd --create-home --uid 1000 trac-mcp
USER trac-mcp
WORKDIR /home/trac-mcp

EXPOSE 8080

HEALTHCHECK --interval=10s --timeout=5s --retries=12 --start-period=15s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/healthz')"

ENTRYPOINT ["trac-mcp-server"]
CMD ["--transport", "http", "--host", "0.0.0.0", "--port", "8080"]
