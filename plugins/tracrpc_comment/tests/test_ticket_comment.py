# -*- coding: utf-8 -*-
"""Tests for TicketCommentRPC against a real Trac environment."""

import unittest

from trac.core import TracError
from trac.perm import PermissionSystem
from trac.test import EnvironmentStub, MockRequest
from trac.ticket.model import Ticket

from tracrpc_comment.ticket_comment import (
    TicketCommentRPC, _quote_of, _substantive_fields)


class TicketCommentRPCTestCase(unittest.TestCase):

    def setUp(self):
        self.env = EnvironmentStub(default_data=True,
                                   enable=['trac.*', 'tracrpc_comment.*'])
        perm = PermissionSystem(self.env)
        for action in ('TICKET_VIEW', 'TICKET_APPEND', 'TICKET_MODIFY',
                       'TICKET_EDIT_COMMENT', 'TICKET_ADMIN'):
            perm.grant_permission('editor', action)
        self.rpc = TicketCommentRPC(self.env)

    def tearDown(self):
        self.env.reset_db()

    # helpers

    def _req(self, authname='editor'):
        return MockRequest(self.env, authname=authname)

    def _ticket(self, **kw):
        ticket = Ticket(self.env)
        ticket['summary'] = kw.pop('summary', 'a ticket')
        ticket['status'] = kw.pop('status', 'new')
        for key, value in kw.items():
            ticket[key] = value
        ticket.insert()
        return ticket

    def _comment(self, ticket, text, author='editor', **fields):
        for key, value in fields.items():
            ticket[key] = value
        ticket.save_changes(author, text)
        return ticket

    def _body(self, ticket_id, cnum):
        change = Ticket(self.env, ticket_id).get_change(cnum)
        return change['fields']['comment']['new']

    # editComment

    def test_edit_replaces_body_and_keeps_history(self):
        ticket = self._ticket()
        self._comment(ticket, 'original text')

        self.rpc.editComment(self._req(), ticket.id, 1, 'corrected text')

        self.assertEqual('corrected text', self._body(ticket.id, 1))
        history = self.rpc.getCommentHistory(self._req(), ticket.id, 1)
        self.assertEqual(2, len(history))
        self.assertEqual('original text', history[0][3])
        self.assertEqual('corrected text', history[1][3])

    def test_edit_twice_appends_to_the_history_chain(self):
        """A second edit must extend the history, not overwrite the first.

        Each edit adds another ``_comment<rev>`` row holding the superseded
        text, so revision numbering has to keep walking that chain. An
        implementation that resolved the comment once and reused a stale
        timestamp would pass with a single edit and lose ``v1`` here.
        """
        ticket = self._ticket()
        self._comment(ticket, 'v0')

        self.rpc.editComment(self._req(), ticket.id, 1, 'v1')
        self.rpc.editComment(self._req(), ticket.id, 1, 'v2')

        self.assertEqual('v2', self._body(ticket.id, 1))
        history = self.rpc.getCommentHistory(self._req(), ticket.id, 1)
        self.assertEqual(['v0', 'v1', 'v2'], [row[3] for row in history])

    def test_edit_unknown_comment_raises(self):
        ticket = self._ticket()
        with self.assertRaises(TracError):
            self.rpc.editComment(self._req(), ticket.id, 99, 'nope')

    # deleteComment

    def test_delete_comment_only_change_succeeds(self):
        ticket = self._ticket()
        self._comment(ticket, 'just a comment')

        self.assertTrue(
            self.rpc.deleteComment(self._req(), ticket.id, 1))
        self.assertIsNone(Ticket(self.env, ticket.id).get_change(1))

    def test_delete_refuses_when_the_change_carries_fields(self):
        """The headline guard.

        ``delete_change`` deletes the whole change-set at that timestamp and
        REVERTS its field changes. Deleting the comment that accompanied a
        close would reopen the ticket.
        """
        ticket = self._ticket()
        self._comment(ticket, 'closing this', status='closed',
                      resolution='fixed')

        with self.assertRaises(TracError) as caught:
            self.rpc.deleteComment(self._req(), ticket.id, 1)

        message = str(caught.exception)
        self.assertIn('status', message)
        self.assertIn('resolution', message)
        self.assertIn('force=True', message)

        # The refusal must be inert: nothing deleted, nothing reverted.
        after = Ticket(self.env, ticket.id)
        self.assertEqual('closed', after['status'])
        self.assertIsNotNone(after.get_change(1))

    def test_delete_with_force_reverts_the_bundled_fields(self):
        """Documents the hazard rather than merely avoiding it.

        This is the behaviour the guard exists to warn about: forcing the
        delete really does roll the ticket back to 'new'.
        """
        ticket = self._ticket()
        self._comment(ticket, 'closing this', status='closed',
                      resolution='fixed')

        self.assertTrue(
            self.rpc.deleteComment(self._req(), ticket.id, 1, force=True))

        after = Ticket(self.env, ticket.id)
        self.assertIsNone(after.get_change(1))
        self.assertEqual('new', after['status'])

    def test_delete_unknown_comment_raises(self):
        ticket = self._ticket()
        with self.assertRaises(TracError):
            self.rpc.deleteComment(self._req(), ticket.id, 99)

    # _substantive_fields

    def test_substantive_fields_ignores_comment_and_internals(self):
        change = {'fields': {'comment': {}, '_comment0': {}, 'status': {},
                             'owner': {}}}
        self.assertEqual(['owner', 'status'], _substantive_fields(change))

    def test_substantive_fields_empty_for_a_plain_comment(self):
        self.assertEqual([], _substantive_fields({'fields': {'comment': {}}}))

    def test_an_edited_comment_is_still_deletable_without_force(self):
        """An edit adds ``_comment0``/``_comment1`` rows to the change-set.

        Those are internal, so a comment that has merely been edited must not
        start looking like a bundled field change and become undeletable.
        """
        ticket = self._ticket()
        self._comment(ticket, 'original')
        self.rpc.editComment(self._req(), ticket.id, 1, 'edited')

        self.assertTrue(self.rpc.deleteComment(self._req(), ticket.id, 1))

    # replyToComment

    def test_reply_records_threading_in_the_stored_cnum(self):
        """The point of the method: real threading, not just quoted text.

        Trac stores a reply's number as "<parent>.<child>" in the comment
        row's oldvalue. That marker is what makes the UI nest the reply, and
        it is precisely what `ticket.update` cannot produce.
        """
        ticket = self._ticket()
        self._comment(ticket, 'the question')

        new_cnum = self.rpc.replyToComment(
            self._req(), ticket.id, 1, 'the answer')

        self.assertEqual(2, new_cnum)
        stored = dict(self.env.db_query(
            "SELECT newvalue, oldvalue FROM ticket_change "
            "WHERE ticket=%s AND field='comment'", (ticket.id,)))
        self.assertEqual('1.2', stored['the answer'])
        self.assertEqual('1', stored['the question'])

    def test_reply_is_addressable_by_its_plain_number(self):
        """A threaded reply must still resolve through the normal cnum path.

        This is the `%.N` LIKE branch of `_find_change`, and it is the reason
        every other method here keeps working on replies.
        """
        ticket = self._ticket()
        self._comment(ticket, 'the question')
        self.rpc.replyToComment(self._req(), ticket.id, 1, 'the answer')

        self.rpc.editComment(self._req(), ticket.id, 2, 'a better answer')
        self.assertEqual('a better answer', self._body(ticket.id, 2))

    def test_reply_without_quote_posts_only_the_new_text(self):
        ticket = self._ticket()
        self._comment(ticket, 'the question')
        self.rpc.replyToComment(self._req(), ticket.id, 1, 'the answer')
        self.assertEqual('the answer', self._body(ticket.id, 2))

    def test_reply_with_quote_prepends_tracs_own_preamble(self):
        ticket = self._ticket()
        self._comment(ticket, 'line one\nline two')
        self.rpc.replyToComment(
            self._req(), ticket.id, 1, 'my answer', True)

        self.assertEqual(
            'Replying to [comment:1 editor]:\n'
            '> line one\n'
            '> line two\n'
            '\n'
            'my answer',
            self._body(ticket.id, 2))

    def test_reply_to_missing_comment_raises(self):
        ticket = self._ticket()
        with self.assertRaises(TracError):
            self.rpc.replyToComment(self._req(), ticket.id, 99, 'nope')

    def test_quote_helper_handles_an_empty_parent_body(self):
        quoted = _quote_of({'fields': {}, 'author': 'someone'}, 3)
        self.assertEqual('Replying to [comment:3 someone]:\n\n', quoted)

    # getCommentHistory

    def test_history_of_unedited_comment_has_one_entry(self):
        ticket = self._ticket()
        self._comment(ticket, 'only version')
        history = self.rpc.getCommentHistory(self._req(), ticket.id, 1)
        self.assertEqual(1, len(history))
        self.assertEqual('only version', history[0][3])

    def test_history_of_missing_comment_is_empty(self):
        ticket = self._ticket()
        self.assertEqual(
            [], self.rpc.getCommentHistory(self._req(), ticket.id, 99))


if __name__ == '__main__':
    unittest.main()
