"""Shared helpers for the ticket/wiki attachment tool modules.

Both ``ticket_attachment.py`` and ``wiki_attachment.py`` wrap Trac
attachment XML-RPC calls that hand back the fetched bytes wrapped
differently depending on how ``_parse_xmlrpc_value`` decoded the
response. The coercion logic is identical for both realms -- an
attachment's bytes don't care whether they're attached to a ticket or a
wiki page -- so it lives here once rather than twice.
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
