"""Normalise a decoded XML-RPC attachment payload to bytes.

Shared by the MCP attachment tools (via ``mcp/tools/attachment_common``)
and the CLIs' ``--assets`` fetch. It lives under ``core`` because
importing anything from ``mcp.tools`` runs that package's ``__init__``,
which loads every tool module -- a cost the thin CLIs must not pay.
"""

import xmlrpc.client


def coerce_attachment_payload(data: object) -> bytes:
    """Normalize a decoded XML-RPC attachment payload to raw bytes.

    ``_parse_xmlrpc_value``'s base64 arm returns ``bytes``; this
    defensively handles the other shapes it or a future parser change
    could hand back, so callers always get bytes to write to disk.

    Raises:
        ValueError: If data is not str, bytes/bytearray, or Binary.
    """
    if isinstance(data, str):
        # Treat as already-decoded text payload (rare); encode to utf-8
        return data.encode("utf-8")
    if isinstance(data, (bytes, bytearray)):
        return bytes(data)
    if isinstance(data, xmlrpc.client.Binary):
        return data.data
    raise ValueError(
        f"Unexpected attachment payload type: {type(data).__name__}"
    )


__all__ = ["coerce_attachment_payload"]
