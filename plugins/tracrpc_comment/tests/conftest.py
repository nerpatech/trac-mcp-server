# -*- coding: utf-8 -*-
"""Offline test harness for the tracrpc_comment Trac plugin.

Runs against REAL Trac -- a real ``Ticket`` model, a real SQLite database,
real permission policies -- because the behaviour under test (particularly
``delete_change``'s field-reverting semantics) is a property of Trac's model
that a mock could only reproduce if its author already knew about it, which is
exactly the bug this plugin exists to guard against.

The ONE thing stubbed is the ``tracrpc`` import surface, and only because
TracXMLRPC's Python 3 build is not on PyPI (1.1.9, the newest release there,
is Python 2 only -- ``except Exception, e``). The Trac host runs 1.2.0.dev0
from the trac-hacks svn trunk. The plugin touches exactly two names from it:

* ``tracrpc.api.IXMLRPCHandler`` -- a bare ``trac.core.Interface`` with no
  methods or behaviour; component registration treats the stub identically.
* ``tracrpc.util.web_context`` -- which upstream ``tracrpc/util.py`` obtains as
  ``from trac.web.chrome import web_context``. The stub re-exports the same
  function object, so it is not an approximation.

Anything beyond those two names (the dispatcher, permission enforcement in
``Method.__call__``, XML-RPC serialization) is NOT covered here and is proven
by live acceptance against ``/trac_test``.
"""

import sys
import types

from trac.core import Interface
from trac.web.chrome import web_context as _real_web_context


def _install_tracrpc_stub():
    if 'tracrpc.api' in sys.modules:  # pragma: no cover - real one present
        return

    class IXMLRPCHandler(Interface):
        """Stub of tracrpc.api.IXMLRPCHandler (a marker Interface)."""

        def xmlrpc_namespace():
            """Namespace these methods live in."""

        def xmlrpc_methods():
            """Yield (permission, signatures, callable) tuples."""

    tracrpc = types.ModuleType('tracrpc')
    api = types.ModuleType('tracrpc.api')
    util = types.ModuleType('tracrpc.util')

    api.IXMLRPCHandler = IXMLRPCHandler
    # Same object upstream re-exports -- not a reimplementation.
    util.web_context = _real_web_context
    tracrpc.api = api
    tracrpc.util = util

    sys.modules['tracrpc'] = tracrpc
    sys.modules['tracrpc.api'] = api
    sys.modules['tracrpc.util'] = util


_install_tracrpc_stub()
