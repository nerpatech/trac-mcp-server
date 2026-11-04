# -*- coding: utf-8 -*-
"""Tests for WikiConditionalPutRPC (wiki.putPageIfVersion) against real Trac.

Trac /trac_mcp_server #129: the version check and the save must be one
step, so a write that lands between them is refused, not overwritten.
"""

import shutil
import tempfile
import threading
import unittest
from unittest import mock

from trac.core import Component, implements
from trac.env import Environment
from trac.perm import IPermissionPolicy, PermissionError, PermissionSystem
from trac.test import EnvironmentStub, MockRequest
from trac.wiki.api import IWikiChangeListener
from trac.wiki.model import WikiPage
# Registers WikiModule, the IPermissionRequestor that defines the WIKI_*
# actions; EnvironmentStub enables components but does not import them.
import trac.wiki.web_ui  # noqa: F401

from tracrpc_comment import wiki_put
from tracrpc_comment.wiki_put import (
    CONFLICT_FAULT_CODE, CONFLICT_MARKER_RE, WikiConditionalPutRPC,
    WikiVersionConflict)


class DenyLockedPagePolicy(Component):
    """Denies WIKI_MODIFY on the page ``Locked`` only -- a resource-scoped
    refusal that a global permission check would not see."""

    implements(IPermissionPolicy)

    def check_permission(self, action, username, resource, perm):
        if (action == 'WIKI_MODIFY' and resource is not None
                and resource.realm == 'wiki' and resource.id == 'Locked'):
            return False
        return None


class RecordingListener(Component):
    """Records IWikiChangeListener calls, to show save()'s listeners run."""

    implements(IWikiChangeListener)

    calls = []

    def wiki_page_added(self, page):
        self.calls.append(('added', page.name, page.version))

    def wiki_page_changed(self, page, version, t, comment, author):
        self.calls.append(('changed', page.name, version, comment, author))

    def wiki_page_deleted(self, page):
        pass

    def wiki_page_version_deleted(self, page):
        pass

    def wiki_page_renamed(self, page, old_name):
        pass

    def wiki_page_comment_modified(self, page, old_comment):
        pass


_THIS = __name__


def _grant_editor(env, *extra):
    perm = PermissionSystem(env)
    for action in ('WIKI_VIEW', 'WIKI_CREATE', 'WIKI_MODIFY') + extra:
        perm.grant_permission('editor', action)


def _write(env, name, text, author='someone', comment=''):
    page = WikiPage(env, name)
    page.text = text
    page.save(author, comment)
    return page.version


def _history(env, name):
    return [(int(v), author, text) for v, author, text in env.db_query(
        "SELECT version, author, text FROM wiki WHERE name=%s "
        "ORDER BY version", (name,))]


class WikiConditionalPutTestCase(unittest.TestCase):

    def setUp(self):
        self.env = EnvironmentStub(
            default_data=False,
            enable=['trac.*', 'tracrpc_comment.*',
                    _THIS + '.DenyLockedPagePolicy',
                    _THIS + '.RecordingListener'])
        self.env.config.set(
            'trac', 'permission_policies',
            'DenyLockedPagePolicy, DefaultPermissionPolicy, '
            'LegacyAttachmentPolicy')
        _grant_editor(self.env)
        # Granted up front: DefaultPermissionPolicy caches a user's
        # permissions for a few seconds, so a mid-test grant is not seen.
        PermissionSystem(self.env).grant_permission('admin', 'WIKI_ADMIN')
        RecordingListener.calls = []
        self.rpc = WikiConditionalPutRPC(self.env)

    def tearDown(self):
        self.env.reset_db()

    def _req(self, authname='editor'):
        return MockRequest(self.env, authname=authname)

    def _put(self, name, text, expected, req=None, **attrs):
        attrs.setdefault('comment', 'c')
        return self.rpc.putPageIfVersion(req or self._req(), name, text,
                                         attrs, expected)

    # registration

    def test_claims_only_put_page_if_version_in_the_wiki_namespace(self):
        self.assertEqual('wiki', self.rpc.xmlrpc_namespace())
        methods = [m[2].__name__ for m in self.rpc.xmlrpc_methods()]
        self.assertEqual(['putPageIfVersion'], methods)

    # a matching version stores the page

    def test_matching_version_stores_the_page(self):
        _write(self.env, 'Page', 'v1')
        _write(self.env, 'Page', 'v2')

        self.assertIs(True, self._put('Page', 'v3', 2, comment='edit'))

        page = WikiPage(self.env, 'Page')
        self.assertEqual(3, page.version)
        self.assertEqual('v3', page.text)
        self.assertEqual('editor', page.author)
        self.assertEqual('edit', page.comment)

    def test_save_semantics_come_from_wikipage_save(self):
        """Author is the caller (an `author` attribute is ignored), the
        comment is stored, and the change listeners fire -- all WikiPage.save
        behaviour, not a reimplementation of it."""
        _write(self.env, 'Page', 'v1')

        self._put('Page', 'v2', 1, comment='why', author='impostor')

        page = WikiPage(self.env, 'Page')
        self.assertEqual('editor', page.author)
        self.assertEqual('why', page.comment)
        self.assertIn(('changed', 'Page', 2, 'why', 'editor'),
                      RecordingListener.calls)

    def test_identical_content_is_refused_as_not_modified(self):
        _write(self.env, 'Page', 'same')
        with self.assertRaises(Exception) as caught:
            self._put('Page', 'same', 1)
        self.assertNotIsInstance(caught.exception, WikiVersionConflict)
        self.assertIn('not modified', str(caught.exception))
        self.assertEqual(1, WikiPage(self.env, 'Page').version)

    # a stale version raises the fault and stores nothing

    def test_stale_version_raises_the_conflict_and_stores_nothing(self):
        _write(self.env, 'Page', 'v1')
        _write(self.env, 'Page', 'v2')
        before = _history(self.env, 'Page')

        with self.assertRaises(WikiVersionConflict) as caught:
            self._put('Page', 'mine', 1)

        err = caught.exception
        self.assertEqual(CONFLICT_FAULT_CODE, err.code)
        self.assertEqual(409, err.code)
        self.assertEqual(1, err.expected_version)
        self.assertEqual(2, err.current_version)
        self.assertEqual('2', CONFLICT_MARKER_RE.search(str(err)).group(1))
        self.assertTrue(str(err).startswith(
            '[wiki-version-conflict current_version=2]'))
        self.assertEqual(before, _history(self.env, 'Page'))
        self.assertEqual([], [c for c in RecordingListener.calls
                              if c[0] == 'changed' and c[3] == 'c'])

    def test_a_newer_version_than_current_is_also_a_conflict(self):
        _write(self.env, 'Page', 'v1')
        with self.assertRaises(WikiVersionConflict) as caught:
            self._put('Page', 'mine', 5)
        self.assertEqual(1, caught.exception.current_version)
        self.assertEqual(1, WikiPage(self.env, 'Page').version)

    # a page that does not exist

    def test_version_zero_creates_an_absent_page(self):
        self.assertIs(True, self._put('NewPage', 'first', 0))
        page = WikiPage(self.env, 'NewPage')
        self.assertEqual(1, page.version)
        self.assertEqual('first', page.text)
        self.assertIn(('added', 'NewPage', 1), RecordingListener.calls)

    def test_version_zero_refuses_a_page_that_now_exists(self):
        """0 means "create only if absent", not "create or overwrite"."""
        _write(self.env, 'Page', 'theirs')
        with self.assertRaises(WikiVersionConflict) as caught:
            self._put('Page', 'mine', 0)
        self.assertEqual(1, caught.exception.current_version)
        self.assertEqual('theirs', WikiPage(self.env, 'Page').text)

    def test_a_positive_version_on_a_missing_page_is_a_conflict(self):
        """Mirrors the client-side precheck's "the page does not exist"."""
        with self.assertRaises(WikiVersionConflict) as caught:
            self._put('Gone', 'mine', 3)
        err = caught.exception
        self.assertIsNone(err.current_version)
        self.assertEqual('none', CONFLICT_MARKER_RE.search(str(err)).group(1))
        self.assertIn('does not exist', str(err))
        self.assertFalse(WikiPage(self.env, 'Gone').exists)

    def test_a_negative_or_non_integer_version_is_rejected(self):
        _write(self.env, 'Page', 'v1')
        for bad in (-1, '1', None, True):
            with self.assertRaises(Exception) as caught:
                self._put('Page', 'mine', bad)
            self.assertNotIsInstance(caught.exception, WikiVersionConflict)
            self.assertIn('expected_version', str(caught.exception))
        self.assertEqual(1, WikiPage(self.env, 'Page').version)

    # permissions

    def test_permission_denied_without_wiki_modify(self):
        _write(self.env, 'Page', 'v1')
        PermissionSystem(self.env).grant_permission('viewer', 'WIKI_VIEW')

        with self.assertRaises(PermissionError):
            self._put('Page', 'mine', 1, req=self._req('viewer'))
        self.assertEqual(1, WikiPage(self.env, 'Page').version)

    def test_permission_is_checked_against_the_page_resource(self):
        """`editor` holds WIKI_MODIFY globally, but the policy denies it on
        `Locked`. A global check would let this write through."""
        _write(self.env, 'Locked', 'v1')
        _write(self.env, 'Open', 'v1')

        with self.assertRaises(PermissionError):
            self._put('Locked', 'mine', 1)
        self.assertEqual('v1', WikiPage(self.env, 'Locked').text)

        self._put('Open', 'mine', 1)
        self.assertEqual('mine', WikiPage(self.env, 'Open').text)

    def test_create_needs_wiki_create(self):
        perm = PermissionSystem(self.env)
        perm.grant_permission('modifier', 'WIKI_VIEW')
        perm.grant_permission('modifier', 'WIKI_MODIFY')
        with self.assertRaises(PermissionError):
            self._put('NewPage', 'x', 0, req=self._req('modifier'))
        self.assertFalse(WikiPage(self.env, 'NewPage').exists)

    def test_version_is_not_disclosed_without_wiki_view(self):
        _write(self.env, 'Page', 'v1')
        with self.assertRaises(PermissionError):
            self._put('Page', 'mine', 7, req=self._req('anonymous'))

    def test_readonly_page_needs_wiki_admin(self):
        page = WikiPage(self.env, 'Ro')
        page.text = 'v1'
        page.readonly = 1
        page.save('admin', '')

        with self.assertRaises(PermissionError):
            self._put('Ro', 'mine', 1)
        self.assertEqual('v1', WikiPage(self.env, 'Ro').text)

        self._put('Ro', 'mine', 1, req=self._req('admin'))
        page = WikiPage(self.env, 'Ro')
        self.assertEqual('mine', page.text)
        # Omitted readonly attribute: the flag is kept, not cleared.
        self.assertEqual(1, page.readonly)

    def test_readonly_attribute_needs_wiki_admin(self):
        _write(self.env, 'Page', 'v1')
        self._put('Page', 'v2', 1, readonly=True)
        self.assertEqual(0, WikiPage(self.env, 'Page').readonly)

        self._put('Page', 'v3', 2, req=self._req('admin'), readonly=True)
        self.assertEqual(1, WikiPage(self.env, 'Page').readonly)


class WikiConditionalPutInterleaveTestCase(unittest.TestCase):
    """The forced interleave, on a real file-backed SQLite database.

    A second writer -- on its own thread, so its own connection and its own
    transaction, as another web request would be -- saves the page after
    putPageIfVersion has read the version and before it saves. No sleeps:
    the read is hooked, and the second writer is joined before the hook
    returns.

    EnvironmentStub's in-memory database is one shared connection, so a
    second writer there would join this call's transaction and be rolled
    back with it -- not what happens on a real server. Hence a real
    Environment on disk.
    """

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix='tracrpc_wiki_put_')
        self.env = Environment(self.dir, create=True, options=[
            ('trac', 'database', 'sqlite:db/trac.db'),
            ('components', 'tracrpc_comment.*', 'enabled'),
        ])
        _grant_editor(self.env)
        _write(self.env, 'Page', 'v1')
        self.rpc = WikiConditionalPutRPC(self.env)

    def tearDown(self):
        self.env.shutdown()
        shutil.rmtree(self.dir)

    def _hook_read(self, second_writer):
        """Patch the read so `second_writer` runs, to completion and
        committed, right after the version has been read."""
        real_load = wiki_put._load_page
        errors = []

        def load_then_interleave(env, pagename):
            page = real_load(env, pagename)

            def run():
                try:
                    second_writer()
                except BaseException as exc:  # pragma: no cover
                    errors.append(exc)
            thread = threading.Thread(target=run)
            thread.start()
            thread.join()
            return page

        return mock.patch.object(wiki_put, '_load_page',
                                 load_then_interleave), errors

    def test_a_write_between_the_read_and_the_save_is_refused(self):
        patcher, errors = self._hook_read(
            lambda: _write(self.env, 'Page', 'theirs', author='other'))

        req = MockRequest(self.env, authname='editor')
        with patcher:
            with self.assertRaises(WikiVersionConflict) as caught:
                self.rpc.putPageIfVersion(req, 'Page', 'mine',
                                          {'comment': 'c'}, 1)

        self.assertEqual([], errors)
        err = caught.exception
        self.assertEqual(1, err.expected_version)
        # The version the interleaved write left behind.
        self.assertEqual(2, err.current_version)
        # Their write survives; ours stored nothing.
        self.assertEqual([(1, 'someone', 'v1'), (2, 'other', 'theirs')],
                         _history(self.env, 'Page'))

    def test_without_an_interleave_the_same_call_stores(self):
        """Control for the test above: the hook alone changes nothing."""
        patcher, errors = self._hook_read(lambda: None)

        req = MockRequest(self.env, authname='editor')
        with patcher:
            self.rpc.putPageIfVersion(req, 'Page', 'mine',
                                      {'comment': 'c'}, 1)

        self.assertEqual([], errors)
        self.assertEqual([(1, 'someone', 'v1'), (2, 'editor', 'mine')],
                         _history(self.env, 'Page'))

    def test_a_create_raced_by_another_create_is_refused(self):
        """expected_version 0 ("only if absent") under the same race."""
        patcher, errors = self._hook_read(
            lambda: _write(self.env, 'Fresh', 'theirs', author='other'))

        req = MockRequest(self.env, authname='editor')
        with patcher:
            with self.assertRaises(WikiVersionConflict) as caught:
                self.rpc.putPageIfVersion(req, 'Fresh', 'mine',
                                          {'comment': 'c'}, 0)

        self.assertEqual([], errors)
        self.assertEqual(1, caught.exception.current_version)
        self.assertEqual([(1, 'other', 'theirs')],
                         _history(self.env, 'Fresh'))

    def test_an_integrity_error_with_no_other_writer_is_not_a_conflict(self):
        """Only the (name, version) primary-key race is a version
        conflict. A NOT NULL, or a constraint another plugin added, fails
        the INSERT with the same IntegrityError while the page's version is
        unchanged; reporting that as a conflict would send the client to
        refetch and retry forever, so it must propagate as what it is."""
        req = MockRequest(self.env, authname='editor')
        boom = self.env.db_exc.IntegrityError('NOT NULL constraint failed')
        with mock.patch.object(WikiPage, 'save', side_effect=boom):
            with self.assertRaises(self.env.db_exc.IntegrityError) as caught:
                self.rpc.putPageIfVersion(req, 'Page', 'mine',
                                          {'comment': 'c'}, 1)

        self.assertIs(boom, caught.exception)
        self.assertNotIsInstance(caught.exception, WikiVersionConflict)
        # Nothing stored.
        self.assertEqual([(1, 'someone', 'v1')], _history(self.env, 'Page'))

    def test_an_integrity_error_for_a_missing_page_is_not_a_conflict(self):
        """Same, for expected_version 0 on a page that stays absent: its
        re-read version is 0, which equals expected_version."""
        req = MockRequest(self.env, authname='editor')
        boom = self.env.db_exc.IntegrityError('NOT NULL constraint failed')
        with mock.patch.object(WikiPage, 'save', side_effect=boom):
            with self.assertRaises(self.env.db_exc.IntegrityError):
                self.rpc.putPageIfVersion(req, 'Fresh', 'mine',
                                          {'comment': 'c'}, 0)

        self.assertEqual([], _history(self.env, 'Fresh'))
