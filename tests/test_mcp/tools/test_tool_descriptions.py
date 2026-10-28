"""Tool descriptions must not tell agents a capability is missing when it exists (ticket #107).

``ticket_update`` once said comments "can't be edited or deleted through this
tool" because the Trac host had no comment RPC. The ``tracrpc_comment`` plugin
added ``ticket_comment_edit``/``ticket_comment_delete``, the sentence went
stale, and agents read it as fact and posted a second correcting comment.
"""

from trac_mcp_server.mcp.tools.ticket_write import TICKET_WRITE_TOOLS


def _description(name: str) -> str:
    return next(
        t.description for t in TICKET_WRITE_TOOLS if t.name == name
    )


def test_ticket_update_points_at_comment_edit():
    desc = _description("ticket_update")
    assert "ticket_comment_edit" in desc
    assert "can't be edited" not in desc
    assert "no comment edit" not in desc
