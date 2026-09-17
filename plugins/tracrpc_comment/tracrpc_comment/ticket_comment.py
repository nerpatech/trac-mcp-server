# -*- coding: utf-8 -*-
"""Comment-level ticket operations over XML-RPC: edit, delete, history.

Trac 1.6 already implements all three in ``trac.ticket.model.Ticket``
(``modify_comment``, ``delete_change``, ``get_comment_history``). The only
thing missing is RPC exposure, which is what this module adds.
"""

from trac.core import Component, TracError, implements
from trac.resource import Resource
from trac.ticket.model import Ticket

from tracrpc.api import IXMLRPCHandler

__all__ = ['TicketCommentRPC']


def _comment_resource(ticket, cnum):
    """The ``comment`` resource a permission check must be scoped to."""
    return Resource('comment', cnum, parent=ticket.resource)


def _require_change(ticket, cnum):
    """Return the change-set for ``cnum``, or raise as Trac's UI does."""
    change = ticket.get_change(cnum)
    if not change:
        raise TracError("Comment %s not found on ticket #%s"
                        % (cnum, ticket.id))
    return change


def _substantive_fields(change):
    """Field names in ``change`` that are not the comment body itself.

    Mirrors the filter ``Ticket.delete_change`` applies when it decides what
    to roll back: everything except ``comment`` and the ``_``-prefixed
    internal fields (``_comment0``, ``_comment1``, ... hold comment edit
    history). Those are exactly the fields a delete would REVERT, so this is
    the set the caller has to be warned about.
    """
    return sorted(name for name in change['fields']
                  if name != 'comment' and not name.startswith('_'))


def _quote_of(change, cnum):
    """Trac's own reply-quote preamble, as the web UI's Reply button builds it.

    Mirrors trac/ticket/web_ui.py:1643 -- a "Replying to" line naming the
    parent comment, then the original text with each line prefixed by "> ",
    then a blank line. Kept byte-compatible with the UI so a reply posted
    over RPC is indistinguishable from one typed in the browser.
    """
    original = change['fields'].get('comment', {}).get('new', '') or ''
    author = change.get('author') or ''
    lines = ['Replying to [comment:%s %s]:' % (cnum, author)]
    lines += ['> %s' % line for line in original.splitlines()]
    lines += ['', '']
    return '\n'.join(lines)


class TicketCommentRPC(Component):
    """Edit, delete and inspect the history of individual ticket comments."""

    implements(IXMLRPCHandler)

    # IXMLRPCHandler methods

    def xmlrpc_namespace(self):
        return 'ticket'

    def xmlrpc_methods(self):
        # Every permission below is declared as None -- NOT because these
        # methods are unprivileged, but because the dispatcher's own check
        # (``tracrpc.api.Method.__call__``) is a GLOBAL
        # ``req.perm.assert_permission(...)``, and Trac's real policy for
        # these operations is RESOURCE-SCOPED.
        #
        # ``TicketSystem.check_permission`` grants TICKET_EDIT_COMMENT
        # implicitly to a user who holds TICKET_APPEND on the ticket and
        # authored the comment in question (trac/ticket/web_ui.py:1923). A
        # global assert_permission('TICKET_EDIT_COMMENT') would deny exactly
        # that user, making this API stricter than the web UI it mirrors.
        #
        # So the checks are performed inside each method against the proper
        # resource, the same way trac/ticket/web_ui.py:606 and
        # tracopt/ticket/deleter.py:81 do it.
        yield (None, ((bool, int, int, str),), self.editComment)
        yield (None, ((bool, int, int), (bool, int, int, bool)),
               self.deleteComment)
        yield (None, ((list, int, int),), self.getCommentHistory)
        yield (None, ((int, int, int, str), (int, int, int, str, bool)),
               self.replyToComment)

    # Exported methods

    def editComment(self, req, id, cnum, comment):
        """Replace the body of comment `cnum` on ticket `id`.

        The previous text is retained as a comment edit revision, readable
        through `ticket.getCommentHistory`; nothing is overwritten
        destructively. The edit is attributed to the authenticated caller.

        Requires TICKET_EDIT_COMMENT on the comment, which a user holding
        TICKET_APPEND on the ticket also has for their own comments.
        """
        ticket = Ticket(self.env, id)
        change = _require_change(ticket, cnum)
        req.perm(_comment_resource(ticket, cnum)).require(
            'TICKET_EDIT_COMMENT')
        ticket.modify_comment(change['date'], req.authname, comment)
        return True

    def deleteComment(self, req, id, cnum, force=False):
        """Delete comment `cnum` on ticket `id`. Irreversible.

        Refuses by default when the comment was posted together with field
        changes, because `Ticket.delete_change` does not delete a comment --
        it deletes the whole change-set recorded at that timestamp, and
        ROLLS BACK every field change in it. Deleting the comment that
        accompanied a status change would therefore silently reopen the
        ticket. Pass `force=True` to accept that; the refusal message names
        the fields that would be reverted.

        Requires TICKET_ADMIN on the ticket.
        """
        ticket = Ticket(self.env, id)
        change = _require_change(ticket, cnum)
        req.perm(ticket.resource).require('TICKET_ADMIN')

        reverted = _substantive_fields(change)
        if reverted and not force:
            raise TracError(
                "Refusing to delete comment %s on ticket #%s: it was posted "
                "with changes to %s, and deleting it would revert them to "
                "their previous values. Re-read the ticket, then pass "
                "force=True if that is what you intend."
                % (cnum, ticket.id, ', '.join(reverted)))

        ticket.delete_change(cdate=change['date'])
        return True

    def getCommentHistory(self, req, id, cnum):
        """Return the edit history of comment `cnum` on ticket `id`.

        A list of `(revision, date, author, comment)`, oldest first. A
        never-edited comment yields exactly one entry, so an empty list means
        the comment does not exist rather than that it has no history.

        Requires TICKET_VIEW on the ticket.
        """
        ticket = Ticket(self.env, id)
        req.perm(ticket.resource).require('TICKET_VIEW')
        history = ticket.get_comment_history(cnum)
        if not history:
            return []
        return [[rev, date, author, comment]
                for rev, date, author, comment in history]

    def replyToComment(self, req, id, cnum, comment, quote=False):
        """Post `comment` on ticket `id` as a threaded reply to `cnum`.

        Returns the new comment's number.

        This is not the same as posting an ordinary comment whose text
        happens to quote another one. Trac records threading in the comment
        row itself -- `save_changes` stores the child's number as
        "<parent>.<child>" when given `replyto` -- and that is what makes the
        web UI nest the reply under its parent and render the "in reply to"
        link. `ticket.update` cannot produce it: neither of its
        `save_changes` calls passes `replyto`, so every comment created over
        RPC until now has been a flat one.

        With `quote=True` the parent's text is prepended as a quote block in
        Trac's own format, matching what the web UI's Reply button
        pre-fills. The threading is recorded either way; the quote is only
        presentation.

        Requires TICKET_APPEND on the ticket.
        """
        ticket = Ticket(self.env, id)
        parent = _require_change(ticket, cnum)
        req.perm(ticket.resource).require('TICKET_APPEND')

        body = comment
        if quote:
            body = _quote_of(parent, cnum) + comment
        return ticket.save_changes(req.authname, body, replyto=cnum)
