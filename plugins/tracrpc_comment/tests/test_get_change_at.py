# -*- coding: utf-8 -*-
"""Tests for ``ticket.getChangeAt`` against a real Trac environment (#132)."""

import unittest
from datetime import datetime

from trac.core import TracError
from trac.perm import PermissionError, PermissionSystem
from trac.resource import ResourceNotFound
from trac.test import EnvironmentStub, MockRequest
from trac.ticket.model import Ticket
from trac.util.datefmt import to_utimestamp, utc

from tracrpc_comment.ticket_comment import TicketCommentRPC


class GetChangeAtTestCase(unittest.TestCase):

    def setUp(self):
        self.env = EnvironmentStub(default_data=True,
                                   enable=['trac.*', 'tracrpc_comment.*'])
        perm = PermissionSystem(self.env)
        for action in ('TICKET_VIEW', 'TICKET_APPEND'):
            perm.grant_permission('editor', action)
        self.rpc = TicketCommentRPC(self.env)

    def tearDown(self):
        self.env.reset_db()

    # helpers

    def _req(self, authname='editor'):
        return MockRequest(self.env, authname=authname)

    def _ticket(self):
        ticket = Ticket(self.env)
        ticket['summary'] = 'a ticket'
        ticket['status'] = 'new'
        ticket.insert()
        return ticket

    def _save(self, ticket, text, author='editor', micro=123456, **kw):
        """Save a change at a known microsecond; return it as a string."""
        when = datetime(2026, 11, 4, 12, 0, 0, micro, tzinfo=utc)
        for key, value in kw.items():
            ticket[key] = value
        ticket.save_changes(author, text, when=when)
        return str(to_utimestamp(when))

    # the method

    def test_returns_cnum_author_and_text_of_the_change_at_that_time(self):
        ticket = self._ticket()
        ts1 = self._save(ticket, 'first', author='alice', micro=111111)
        ts2 = self._save(ticket, 'second\nline', author='bob', micro=222222)

        self.assertEqual(
            ['2', 'bob', 'second\nline'],
            self.rpc.getChangeAt(self._req(), ticket.id, ts2))
        # Same whole second, different microsecond: still the right one.
        self.assertEqual(
            ['1', 'alice', 'first'],
            self.rpc.getChangeAt(self._req(), ticket.id, ts1))

    def test_cnum_is_a_string(self):
        ticket = self._ticket()
        ts = self._save(ticket, 'x')
        result = self.rpc.getChangeAt(self._req(), ticket.id, ts)
        self.assertIsInstance(result[0], str)

    def test_stored_text_is_returned_unmodified(self):
        ticket = self._ticket()
        ts = self._save(ticket, 'a\r\nb  \n')
        result = self.rpc.getChangeAt(self._req(), ticket.id, ts)
        self.assertEqual('a\r\nb  \n', result[2])

    def test_no_change_at_that_time_is_an_empty_list(self):
        ticket = self._ticket()
        ts = self._save(ticket, 'only')
        off_by_one = str(int(ts) + 1)
        self.assertEqual(
            [], self.rpc.getChangeAt(self._req(), ticket.id, off_by_one))

    def test_change_without_a_comment_field_is_an_empty_list(self):
        """``save_changes`` always writes a comment row, even an empty one,
        so a change with none has to be planted: another plugin or a manual
        repair can leave one behind."""
        ticket = self._ticket()
        when = datetime(2026, 11, 4, 12, 0, 0, 5, tzinfo=utc)
        ts = to_utimestamp(when)
        with self.env.db_transaction as db:
            db("INSERT INTO ticket_change "
               "(ticket, time, author, field, oldvalue, newvalue) "
               "VALUES (%s, %s, 'editor', 'owner', '', 'someone')",
               (ticket.id, ts))
        self.assertIsNotNone(
            Ticket(self.env, ticket.id).get_change(cdate=when))
        self.assertEqual(
            [], self.rpc.getChangeAt(self._req(), ticket.id, str(ts)))

    def test_threaded_reply_returns_the_dotted_cnum(self):
        ticket = self._ticket()
        self._save(ticket, 'the question')
        self.rpc.replyToComment(self._req(), ticket.id, 1, 'the answer')
        stored_ts = self.env.db_query(
            "SELECT time FROM ticket_change WHERE ticket=%s "
            "AND field='comment' AND newvalue='the answer'",
            (ticket.id,))[0][0]

        self.assertEqual(
            ['1.2', 'editor', 'the answer'],
            self.rpc.getChangeAt(self._req(), ticket.id, str(stored_ts)))

    def test_ticket_view_is_required(self):
        ticket = self._ticket()
        ts = self._save(ticket, 'secret')
        # default_data grants TICKET_VIEW to anonymous, which everyone inherits.
        PermissionSystem(self.env).revoke_permission(
            'anonymous', 'TICKET_VIEW')
        with self.assertRaises(PermissionError):
            self.rpc.getChangeAt(self._req('nobody'), ticket.id, ts)

    def test_non_digit_ts_raises_tracerror(self):
        ticket = self._ticket()
        for bad in ('', 'abc', '12.5', '-1', '1e6', ' 12'):
            with self.assertRaises(TracError, msg=repr(bad)):
                self.rpc.getChangeAt(self._req(), ticket.id, bad)

    def test_over_long_digit_string_raises_tracerror(self):
        """All digits, but no timestamp: must be the documented TracError,
        not an OverflowError/OSError out of the datetime conversion."""
        ticket = self._ticket()
        for huge in ('9' * 25, '9' * 400, '1' + '0' * 30):
            with self.assertRaises(TracError, msg=huge):
                self.rpc.getChangeAt(self._req(), ticket.id, huge)

    def test_non_string_ts_raises_tracerror(self):
        ticket = self._ticket()
        with self.assertRaises(TracError):
            self.rpc.getChangeAt(self._req(), ticket.id, 12345)

    def test_missing_ticket_raises(self):
        with self.assertRaises(ResourceNotFound):
            self.rpc.getChangeAt(self._req(), 9999, '1')

    def test_listed_in_xmlrpc_methods_with_no_global_permission(self):
        entries = {m[2].__name__: m for m in self.rpc.xmlrpc_methods()}
        self.assertIn('getChangeAt', entries)
        self.assertIsNone(entries['getChangeAt'][0])


if __name__ == '__main__':
    unittest.main()
