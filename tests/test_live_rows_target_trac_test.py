"""Offline guard: no live row that writes may use the default instance.

Trac /trac_mcp_server #173. Three live rows built their client (or ran
the CLI) from the bare `TRAC_URL`. In this repo's `.env` that is
`/trac_mcp_server`, a real ticket store, so they wrote a page and an
attachment into it. Every live writer must target `/trac_test`.

The check is static and needs no network. It parses every
`tests/**/*.py` and finds the live bodies: a function or class
decorated with `pytest.mark.live`, or the whole module when a
module-level `pytestmark` carries it. A live body is flagged when ALL
of these hold:

  1. it reads the default instance's URL from the environment:
     `os.environ["TRAC_URL"]`, `os.environ.get("TRAC_URL")` or
     `os.getenv("TRAC_URL")`;
  2. it writes: some name, attribute or string in it contains a write
     verb (put, create, delete, update, push, write, to-wiki). A row
     that only reads the default instance (the scrape and the ad-hoc
     addressing rows) is not what this guards against;
  3. it never names `/trac_test` (a resolve, a URL, an `--instance`).

Docstrings and comments do not count toward 2 or 3, so a docstring
that merely says "/trac_test" cannot satisfy the guard. The heuristic
is deliberately crude: a false positive is fixed by naming
`/trac_test` in the row, which is the thing wanted anyway.
"""

import ast
import re
from pathlib import Path

import pytest

TESTS = Path(__file__).parent
LIVE_MARK = "live"
URL_VAR = "TRAC_URL"
WRITE_VERB = re.compile(
    r"put|create|delete|update|push|write|to[-_]wiki", re.IGNORECASE
)
TEST_INSTANCE = "/trac_test"


def _is_live_mark(node):
    """True for `pytest.mark.live`, bare or called."""
    if isinstance(node, ast.Call):
        node = node.func
    return (
        isinstance(node, ast.Attribute)
        and node.attr == LIVE_MARK
        and isinstance(node.value, ast.Attribute)
        and node.value.attr == "mark"
    )


def _module_is_live(tree):
    """A module-level `pytestmark` holding `pytest.mark.live`."""
    for stmt in tree.body:
        if isinstance(stmt, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "pytestmark"
            for t in stmt.targets
        ):
            return any(_is_live_mark(n) for n in ast.walk(stmt.value))
    return False


def _live_nodes(tree):
    """The functions and classes marked live (outermost only)."""
    found = []

    def visit(node):
        for child in ast.iter_child_nodes(node):
            if isinstance(
                child,
                (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef),
            ) and any(_is_live_mark(d) for d in child.decorator_list):
                found.append(child)
            else:
                visit(child)

    visit(tree)
    return found


def _is_docstring(node):
    return (
        isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    )


def _tokens(node):
    """Names, attributes and string constants, docstrings excluded."""
    out = []

    def visit(n):
        if isinstance(n, ast.Name):
            out.append(n.id)
        elif isinstance(n, ast.Attribute):
            out.append(n.attr)
        elif isinstance(n, ast.Constant) and isinstance(n.value, str):
            out.append(n.value)
        elif isinstance(n, ast.arg):
            out.append(n.arg)
        body = getattr(n, "body", None)
        for child in ast.iter_child_nodes(n):
            if (
                isinstance(body, list)
                and child in body[:1]
                and _is_docstring(child)
            ):
                continue
            visit(child)

    visit(node)
    return out


def _reads_default_url(node):
    """First line where the body reads `TRAC_URL` from the env."""
    for n in ast.walk(node):
        if not isinstance(n, (ast.Subscript, ast.Call)):
            continue
        if isinstance(n, ast.Subscript):
            key = n.slice
            target = n.value
        else:
            if not n.args:
                continue
            key = n.args[0]
            target = n.func
        if not (isinstance(key, ast.Constant) and key.value == URL_VAR):
            continue
        text = ast.unparse(target)
        if "environ" in text or "getenv" in text:
            return n.lineno
    return None


def find_default_instance_writers(source, filename="<src>"):
    """`file:line` of each live body that reads TRAC_URL, writes, and
    never names /trac_test. Pure function over source text."""
    tree = ast.parse(source, filename=filename)
    if _module_is_live(tree):
        bodies = [tree]
    else:
        bodies = _live_nodes(tree)
    hits = []
    for body in bodies:
        line = _reads_default_url(body)
        if line is None:
            continue
        tokens = _tokens(body)
        if not any(WRITE_VERB.search(t) for t in tokens):
            continue
        if any(TEST_INSTANCE in t for t in tokens):
            continue
        hits.append(f"{filename}:{line}")
    return hits


def test_no_live_writer_targets_the_default_instance():
    offenders = []
    for path in sorted(TESTS.rglob("*.py")):
        rel = path.relative_to(TESTS.parent).as_posix()
        if rel == Path(__file__).relative_to(TESTS.parent).as_posix():
            continue
        offenders += find_default_instance_writers(
            path.read_text(encoding="utf-8"), rel
        )
    assert not offenders, (
        "live rows that write through the default TRAC_URL (the real "
        "store, not /trac_test); target /trac_test instead:\n  "
        + "\n  ".join(offenders)
    )


@pytest.mark.parametrize(
    "source",
    [
        # Function reading TRAC_URL and writing.
        "import pytest\n@pytest.mark.live\ndef t():\n"
        "    c = TracClient(Config(trac_url=os.environ['TRAC_URL']))\n"
        "    c.put_wiki_page('x', 'y', 'z')\n",
        # Same through .get, in a class.
        "import pytest\n@pytest.mark.live\nclass T:\n"
        "    def t(self):\n"
        "        u = os.environ.get('TRAC_URL')\n"
        "        run('--to-wiki', u)\n",
        # Module-level pytestmark.
        "import pytest\npytestmark = pytest.mark.live\n"
        "def t():\n    u = os.environ['TRAC_URL']\n    delete(u)\n",
        # /trac_test only in the docstring does not count.
        "import pytest\n@pytest.mark.live\ndef t():\n"
        "    '''Uses /trac_test.'''\n"
        "    u = os.environ['TRAC_URL']\n    create(u)\n",
    ],
)
def test_the_guard_flags_a_seeded_default_instance_writer(source):
    assert find_default_instance_writers(source, "seed.py")


@pytest.mark.parametrize(
    "source",
    [
        # Names /trac_test.
        "import pytest\n@pytest.mark.live\ndef t():\n"
        "    u = os.environ['TRAC_URL'] + '/trac_test'\n    put(u)\n",
        # Reads only.
        "import pytest\n@pytest.mark.live\ndef t():\n"
        "    scrape(os.environ['TRAC_URL'])\n",
        # Not live.
        "def t():\n    u = os.environ['TRAC_URL']\n    put(u)\n",
    ],
)
def test_the_guard_passes_what_it_should(source):
    assert not find_default_instance_writers(source, "ok.py")
