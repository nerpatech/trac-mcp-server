"""Exceptions shared by ``mcp/server.py`` and ``mcp/lifespan.py``.

A module of its own so both can import it: ``server.py`` imports the
lifespan, so the class cannot live in ``server.py``.
"""


class StartupError(RuntimeError):
    """The server cannot start, and the reason has already been printed
    to stderr.

    ``run()`` catches this and nothing else RuntimeError-shaped: it exits
    with status 1 and prints no traceback, since the message above is all
    the operator needs. Any other RuntimeError is a bug, and keeps its
    traceback.
    """
