"""Shared helpers for the ticket/wiki attachment tool modules.

Both ``ticket_attachment.py`` and ``wiki_attachment.py`` wrap Trac
attachment XML-RPC calls that hand back the fetched bytes wrapped
differently depending on how ``_parse_xmlrpc_value`` decoded the
response. The coercion logic is identical for both realms -- an
attachment's bytes don't care whether they're attached to a ticket or a
wiki page -- so it lives once, in ``core.attachment_payload`` (which
the CLIs can import without loading this package), and is re-exported
here.
"""

from ...core.attachment_payload import coerce_attachment_payload

__all__ = ["coerce_attachment_payload"]
