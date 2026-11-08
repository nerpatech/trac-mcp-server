"""Reading XML-RPC faults: what a Trac fault says went wrong.

Pure helpers over ``xmlrpc.client.Fault``. The module itself imports only
the standard library, so the client, the MCP tool handlers and the CLI
can all share one reading of a fault without importing one another.
Importing it still runs ``trac_mcp_server.core/__init__.py``, which loads
``core.client``; what it avoids is ``mcp.tools`` (and ``mcp.types``).
"""

import re
import xmlrpc.client

# TracXMLRPC's MethodNotFound fault code (JSON-RPC's "method not found").
_METHOD_NOT_FOUND_FAULT_CODE = -32601

# TracXMLRPC wraps every fault from a plain TracError as
# "'<message>' while executing '<method>()'".
_TRACRPC_FAULT_WRAPPER = re.compile(
    r"^'(?P<message>.*)' while executing '[\w.]+\(\)'$", re.DOTALL
)


def is_missing_method_fault(
    err: xmlrpc.client.Fault, method: str
) -> bool:
    """True when ``err`` says the server has no RPC method ``method``.

    TracXMLRPC raises ``MethodNotFound('RPC method "<name>" not found')``
    with fault code -32601. The text fallback is the "not found" with
    "method" heuristic, narrowed to the method name so that a "page not
    found" fault can never pass for it. "No such method" (some servers'
    wording) counts on the same terms: the text must name ``method``.
    Pass the bare name (``getActions``), not ``ticket.getActions``: a
    server whose text omits the service prefix still matches, and a
    dotted text contains the bare name.
    """
    if err.faultCode == _METHOD_NOT_FOUND_FAULT_CODE:
        return True
    text = err.faultString.lower()
    if method.lower() not in text:
        return False
    return ("not found" in text and "method" in text) or (
        "no such method" in text
    )


def unwrap_tracrpc_fault(
    err: xmlrpc.client.Fault,
) -> xmlrpc.client.Fault:
    """``err`` with TracXMLRPC's "while executing" wrapper removed.

    Unwrapped, a fault from the plugin path reads the same as the same
    fault from ``wiki.putPage`` would, and callers can show its message
    without TracXMLRPC's method wrapper. A fault that is not wrapped is
    returned as is (the same object). Public: the client uses it for
    the faults it re-raises, and ``classify_fault`` for the text it
    classifies.
    """
    match = _TRACRPC_FAULT_WRAPPER.match(err.faultString)
    if match is None:
        return err
    return xmlrpc.client.Fault(err.faultCode, match.group("message"))


# A quoted span in a fault's text is a NAME the fault mentions (Trac
# quotes page, milestone and ticket names: 'X' or "X"), not evidence of
# what went wrong. Single quotes count only where the quote is not inside
# a word, so "don't" is left alone. A single-quoted span may itself hold
# apostrophes (``'Bob's/Notes'``): it ends at the first quote not
# followed by a word character. Typographic quote pairs count too.
_DOUBLE_QUOTED = re.compile(r'"[^"]*"')
_SINGLE_QUOTED = re.compile(r"(?<!\w)'.*?'(?!\w)")
_TYPOGRAPHIC_SINGLE = re.compile("\u2018[^\u2019]*\u2019")
_TYPOGRAPHIC_DOUBLE = re.compile("\u201c[^\u201d]*\u201d")


def fault_text(fault: xmlrpc.client.Fault) -> str:
    """A fault's text for keyword tests: TracXMLRPC's "while executing"
    wrapper removed, one outer pair of quotes around the whole message
    removed, every quoted name replaced by a placeholder, then
    lower-cased."""
    text = unwrap_tracrpc_fault(fault).faultString
    stripped = text.strip()
    if (
        len(stripped) >= 2
        and stripped[0] in "'\""
        and stripped[-1] == stripped[0]
    ):
        text = stripped[1:-1]
    text = _DOUBLE_QUOTED.sub("<name>", text)
    text = _SINGLE_QUOTED.sub("<name>", text)
    text = _TYPOGRAPHIC_SINGLE.sub("<name>", text)
    text = _TYPOGRAPHIC_DOUBLE.sub("<name>", text)
    return text.lower()


def classify_fault(
    fault: xmlrpc.client.Fault, domain: str | None = None
) -> str | None:
    """Classify an XML-RPC fault: ``"version_conflict"``, ``"not_found"``,
    ``"permission_denied"``, ``"already_exists"`` or ``None``.

    Fault codes classify first, in this order: 409 (the conflict code of
    the ``wiki.putPageIfVersion`` plugin), 404 (TracXMLRPC's
    ``ResourceNotFound``) and 403 (its ``PermissionError``). Any other
    code is classified by its text, with TracXMLRPC's wrapper removed and
    every quoted name replaced first, because a quoted name is not
    evidence (Trac #188): ``'Invalid Wiki page name 'PermissionNotes'``
    is no permission fault. Then ``not found``/``does not exist`` is
    ``not_found``, ``permission``/``denied`` is ``permission_denied``,
    and, in the ``milestone`` domain only, ``exists``/``already`` is
    ``already_exists``. The text tests are still needed for the code-1
    faults TracXMLRPC sends for the plugin's ``TracError`` and Trac's
    ``ResourceExistsError``.

    An UNQUOTED name in a fault with none of the codes above can still
    match a token.
    """
    match fault.faultCode:
        case 409:
            return "version_conflict"
        case 404:
            return "not_found"
        case 403:
            return "permission_denied"

    text = fault_text(fault)
    if "not found" in text or "does not exist" in text:
        return "not_found"
    if "permission" in text or "denied" in text:
        return "permission_denied"
    if domain == "milestone" and (
        "exists" in text or "already" in text
    ):
        return "already_exists"
    return None
