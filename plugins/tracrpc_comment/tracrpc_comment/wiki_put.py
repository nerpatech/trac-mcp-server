# -*- coding: utf-8 -*-
"""A wiki ``putPage`` that refuses to overwrite a version it was not given.

Stock TracXMLRPC's ``wiki.putPage`` ignores a ``version`` attribute, so a
client that wants optimistic locking has to read ``wiki.getPageInfo`` first
and compare -- and a write by someone else that lands between that read and
the ``putPage`` is silently replaced (Trac /trac_mcp_server #129). This
module adds ``wiki.putPageIfVersion``, which takes the version the caller
read and makes the comparison and the save inside one request and one
database transaction.

How the window is closed
------------------------
The page is loaded, its version compared and ``WikiPage.save`` called inside
one ``env.db_transaction``; ``save`` opens its own ``db_transaction``, which
Trac joins to the outer one, so the check and the ``INSERT`` commit or roll
back together.

A transaction alone is not a lock, though: SQLite (``isolation_level
DEFERRED``) only begins one at the first write, and PostgreSQL's READ
COMMITTED lets a concurrent writer commit between our ``SELECT`` and our
``INSERT``. What makes the refusal hold on every backend is the ``wiki``
table's primary key, ``(name, version)``: ``save`` inserts
``version + 1`` of the version it loaded, so if another writer committed
that row first, our ``INSERT`` fails with an ``IntegrityError``, the
transaction rolls back, and the call raises the same conflict as a stale
version does. Nothing is overwritten either way.

One consequence of nesting ``save`` in the outer transaction: its
``IWikiChangeListener`` notifications run just before the commit rather than
just after it. ``save`` runs each listener under ``component_guard``, which
logs and swallows a listener's exception, so a listener cannot roll the
write back; the commit follows immediately.
"""

import re

from trac.core import Component, TracError, implements
from trac.wiki.model import WikiPage

from tracrpc.api import IXMLRPCHandler

__all__ = ['WikiConditionalPutRPC', 'WikiVersionConflict',
           'CONFLICT_FAULT_CODE', 'CONFLICT_MARKER_RE']

#: The XML-RPC ``faultCode`` of a version conflict. TracXMLRPC's
#: ``send_rpc_error`` has no branch for a plain ``TracError`` subclass and
#: falls through to ``Fault(e.code or 1, "'<message>' while executing
#: '<method>()'")``, so a ``code`` attribute is how a plugin picks its own.
#: 409 (HTTP Conflict) is not used by TracXMLRPC itself, whose own codes are
#: -32601 (method not found), 403 (permission), 404 (resource not found) and
#: 1 (everything else).
CONFLICT_FAULT_CODE = 409

#: The fault message starts with this token, so a client can tell the
#: conflict apart from every other fault without parsing prose, and read the
#: page's current version out of it (``none`` when the page does not exist).
CONFLICT_MARKER = '[wiki-version-conflict current_version=%s]'
CONFLICT_MARKER_RE = re.compile(
    r"\[wiki-version-conflict current_version=(\d+|none)\]")


class WikiVersionConflict(TracError):
    """``expected_version`` is not the page's current version.

    Raised before anything is written, or -- when another write lands
    between the check and the save -- after the ``INSERT`` failed and the
    transaction rolled back. Either way nothing was stored.
    """

    code = CONFLICT_FAULT_CODE

    def __init__(self, pagename, expected, current):
        self.pagename = pagename
        self.expected_version = expected
        self.current_version = current
        if current is None:
            detail = 'the page does not exist'
        else:
            detail = 'its current version is %s' % current
        if expected == 0:
            wanted = 'create it (expected_version 0: only if absent)'
        else:
            wanted = 'update version %s' % expected
        message = ('%s Version conflict on wiki page "%s": you tried to '
                   '%s but %s.' % (
                       CONFLICT_MARKER % (
                           'none' if current is None else current),
                       pagename, wanted, detail))
        super(WikiVersionConflict, self).__init__(message)


def _load_page(env, pagename):
    """Read the page (and so its version). A seam for the interleave test:
    the save must refuse when another write lands right after this."""
    return WikiPage(env, pagename)


def _current_version(page):
    """``page.version`` with "does not exist" spelled ``None``."""
    return page.version if page.exists else None


class WikiConditionalPutRPC(Component):
    """``wiki.putPageIfVersion``: a ``putPage`` with a version precondition.
    """

    implements(IXMLRPCHandler)

    # IXMLRPCHandler methods

    def xmlrpc_namespace(self):
        return 'wiki'

    def xmlrpc_methods(self):
        # Permission None for the same reason as TicketCommentRPC: the
        # dispatcher's check is a GLOBAL assert_permission, and a wiki
        # write's real policy is per page (fine-grained policies such as
        # AuthzPolicy can grant or deny WIKI_MODIFY on one page). The checks
        # are made inside the method, against the page's resource.
        yield (None, ((bool, str, str, dict, int),), self.putPageIfVersion)

    # Exported methods

    def putPageIfVersion(self, req, pagename, content, attributes,
                         expected_version):
        """Write `content` to wiki page `pagename` only if its current
        version is `expected_version`. Returns True.

        `expected_version` is the version the caller last read. A page that
        does not exist has version 0, so 0 means "create the page only if
        it is still absent"; any other number on a missing page is a
        conflict. On a mismatch nothing is stored and the fault code is 409,
        its message starting `[wiki-version-conflict current_version=N]`
        (`none` when the page does not exist). That holds even when another
        write lands while this call is running: the version check and the
        save are one database transaction, backed by the wiki table's
        (name, version) primary key.

        `attributes` takes `comment` and, for a caller holding WIKI_ADMIN
        on the page, `readonly`. Unlike `wiki.putPage`, the author is always
        the authenticated caller; an `author` attribute is ignored.

        Requires WIKI_VIEW on the page to learn its version, then
        WIKI_MODIFY (WIKI_CREATE for a new page, WIKI_ADMIN for a read-only
        one) -- the same rules as `wiki.putPage`.
        """
        if (not isinstance(expected_version, int)
                or isinstance(expected_version, bool)
                or expected_version < 0):
            raise TracError('expected_version must be a non-negative '
                            'integer, got %r' % (expected_version,))
        attributes = attributes or {}

        try:
            with self.env.db_transaction:
                page = _load_page(self.env, pagename)
                resource = page.resource
                # The version is visible to anyone who may view the page,
                # and to no one else: check that before reporting it.
                req.perm(resource).require('WIKI_VIEW')

                current = _current_version(page)
                if page.version != expected_version:
                    raise WikiVersionConflict(pagename, expected_version,
                                              current)

                # The same rules, in the same order, as stock putPage.
                if page.readonly:
                    req.perm(resource).require('WIKI_ADMIN')
                elif not page.exists:
                    req.perm(resource).require('WIKI_CREATE')
                else:
                    req.perm(resource).require('WIKI_MODIFY')

                page.text = content
                # Stock putPage sets readonly from the attribute whenever
                # the caller is WIKI_ADMIN, so an admin who omits it clears
                # the flag. Here it changes only when it is passed.
                if ('readonly' in attributes
                        and 'WIKI_ADMIN' in req.perm(resource)):
                    page.readonly = 1 if attributes['readonly'] else 0

                # WikiPage.save itself: name validation, "Page not
                # modified", the INSERT, the page-name cache, and the
                # IWikiChangeListener notifications -- not reimplemented.
                page.save(req.authname, attributes.get('comment'))
        except self.env.db_exc.IntegrityError:
            # Another write committed version + 1 between our read and our
            # INSERT; the transaction has rolled back. Report the version
            # that write left behind.
            raise WikiVersionConflict(
                pagename, expected_version,
                _current_version(WikiPage(self.env, pagename)))
        return True
