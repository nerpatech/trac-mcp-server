"""Tests for wiki_diff (ticket #119).

Offline: a fake client holding a page's versions answers the two XML-RPC
calls the handler makes, with the shapes probed on the real substrate on
2026-10-25 -- ``getPageVersion`` faults 404 on a missing page or version,
while ``getPageInfo``/``getPageInfoVersion`` answer ``0`` instead of
faulting.

Live (``@pytest.mark.live``): a scratch page on ``/trac_test`` written as
two real Trac accounts, so ``from_author`` resolves against authors Trac
itself recorded (Rules/testing/RealSubstrateNotMocks).
"""

import asyncio
import os
import re
import uuid
import xmlrpc.client

import pytest

from trac_mcp_server.mcp.tools import WIKI_READ_SPECS, WIKI_TOOLS
from trac_mcp_server.mcp.tools.wiki_read import _handle_diff

PAGE = "Scratch/DiffPage"


class FakeWikiClient:
    """One page; ``versions[i]`` is version ``i + 1`` as (text, author, comment)."""

    def __init__(self, versions, page=PAGE):
        self.versions = versions
        self.page = page

    def _version(self, version):
        return len(self.versions) if version is None else version

    def _exists(self, name, v):
        return name == self.page and 1 <= v <= len(self.versions)

    def get_wiki_page(self, name, version=None):
        v = self._version(version)
        if not self._exists(name, v):
            suffix = "" if version is None else f" at version {v}"
            raise xmlrpc.client.Fault(
                404, f'Wiki page "{name}" does not exist{suffix}'
            )
        return self.versions[v - 1][0]

    def get_wiki_page_info(self, name, version=None):
        v = self._version(version)
        if not self._exists(name, v):
            return 0
        _text, author, comment = self.versions[v - 1]
        return {
            "name": name,
            "version": v,
            "author": author,
            "comment": comment,
            "lastModified": 1792900000 + v * 60,
        }


def _diff(versions, **args):
    args.setdefault("page_name", PAGE)
    return asyncio.run(_handle_diff(FakeWikiClient(versions), args))


def _ok(result):
    assert not result.isError, result.content[0].text
    return result.structuredContent


def _error_text(result):
    assert result.isError
    return result.content[0].text


THREE = [
    ("alpha\nbeta\ngamma\n", "agent_rpc", "create"),
    ("alpha\nBETA\ngamma\n", "nerpa_admin", "why upper case?"),
    ("alpha\nBETA\ngamma\ndelta\n", "nerpa_admin", "add delta"),
]


class TestDefaultRange:
    def test_latest_against_the_version_before(self):
        out = _ok(_diff(THREE))
        assert (out["from_version"], out["to_version"]) == (2, 3)
        assert out["latest_version"] == 3
        assert out["identical"] is False
        assert (out["added"], out["removed"]) == (1, 0)
        assert out["diff"].startswith(
            f"--- {PAGE}@v2\n+++ {PAGE}@v3\n@@ "
        )
        assert "+delta\n" in out["diff"]
        assert [r["version"] for r in out["revisions"]] == [3]
        assert out["revisions"][0]["comment"] == "add delta"
        assert out["revisions"][0]["author"] == "nerpa_admin"

    def test_explicit_range_lists_every_revision_after_from(self):
        out = _ok(_diff(THREE, from_version=1, to_version=3))
        assert (out["from_version"], out["to_version"]) == (1, 3)
        assert (out["added"], out["removed"]) == (2, 1)
        assert "-beta\n+BETA\n" in out["diff"]
        # Oldest first, and the start version itself is not in the range:
        # its comment describes the change INTO it, not out of it.
        assert [r["version"] for r in out["revisions"]] == [2, 3]
        assert [r["comment"] for r in out["revisions"]] == [
            "why upper case?",
            "add delta",
        ]

    def test_to_version_alone_defaults_from_to_the_one_before(self):
        out = _ok(_diff(THREE, to_version=2))
        assert (out["from_version"], out["to_version"]) == (1, 2)
        assert (out["added"], out["removed"]) == (1, 1)

    def test_text_carries_revisions_and_diff(self):
        result = _diff(THREE, from_version=1)
        text = result.content[0].text
        assert "v2 by nerpa_admin" in text
        assert "why upper case?" in text
        assert "-beta\n+BETA" in text


class TestVersionOne:
    def test_version_one_diffs_against_the_empty_page(self):
        out = _ok(_diff(THREE, to_version=1))
        assert out["from_version"] is None
        assert out["to_version"] == 1
        assert (out["added"], out["removed"]) == (3, 0)
        assert out["diff"].startswith(
            f"--- {PAGE} (empty)\n+++ {PAGE}@v1\n"
        )
        assert [r["version"] for r in out["revisions"]] == [1]

    def test_single_version_page_defaults_to_the_empty_page(self):
        out = _ok(_diff(THREE[:1]))
        assert out["from_version"] is None
        assert out["added"] == 3


class TestFromAuthor:
    VERSIONS = [
        ("one\n", "agent_rpc", "v1"),
        ("one\ntwo\n", "agent_rpc", "v2"),
        ("one\nTWO\n", "nerpa_admin", "question on two"),
        ("one\nTWO\nthree\n", "nerpa_admin", "and three"),
    ]

    def test_resolves_to_the_newest_version_by_that_author(self):
        out = _ok(_diff(self.VERSIONS, from_author="agent_rpc"))
        # v2 is the newest agent_rpc version; v1 (one too far back) or
        # v3 (one too far forward) is the off-by-one this pins.
        assert (out["from_version"], out["to_version"]) == (2, 4)
        assert out["from_author"] == "agent_rpc"
        assert [r["version"] for r in out["revisions"]] == [3, 4]
        assert (out["added"], out["removed"]) == (2, 1)

    def test_resolution_is_bounded_by_to_version(self):
        out = _ok(
            _diff(self.VERSIONS, from_author="agent_rpc", to_version=3)
        )
        assert (out["from_version"], out["to_version"]) == (2, 3)

    def test_author_wrote_to_version_itself_means_nothing_since(self):
        # "Everything since my last write", when the last write IS the end
        # of the range, is nothing -- not that author's previous change.
        out = _ok(_diff(self.VERSIONS, from_author="nerpa_admin"))
        assert (out["from_version"], out["to_version"]) == (4, 4)
        assert out["identical"] is True
        assert out["diff"] == ""
        assert out["revisions"] == []

    def test_unknown_author_is_an_error_not_version_one(self):
        text = _error_text(_diff(self.VERSIONS, from_author="nobody"))
        assert "nobody" in text
        assert "validation_error" in text or "not_found" in text

    def test_author_only_after_to_version_is_an_error(self):
        text = _error_text(
            _diff(
                self.VERSIONS, from_author="nerpa_admin", to_version=2
            )
        )
        assert "nerpa_admin" in text

    def test_from_author_and_from_version_are_exclusive(self):
        text = _error_text(
            _diff(
                self.VERSIONS, from_author="agent_rpc", from_version=1
            )
        )
        assert "from_version" in text and "from_author" in text

    def test_empty_author_is_rejected(self):
        _error_text(_diff(self.VERSIONS, from_author=""))


class TestIdentical:
    def test_a_reverted_page_is_identical_across_the_revert(self):
        versions = [
            ("same\n", "agent_rpc", "v1"),
            ("changed\n", "nerpa_admin", "v2"),
            ("same\n", "agent_rpc", "revert"),
        ]
        out = _ok(_diff(versions, from_version=1, to_version=3))
        assert out["identical"] is True
        assert out["diff"] == ""
        assert (out["added"], out["removed"]) == (0, 0)
        assert out["hunks"] == 0
        # The range still lists what happened in between.
        assert [r["version"] for r in out["revisions"]] == [2, 3]
        assert (
            "identical"
            in _diff(versions, from_version=1, to_version=3)
            .content[0]
            .text.lower()
        )


class TestErrors:
    def test_from_version_not_below_to_version(self):
        for from_version in (3, 4):
            text = _error_text(
                _diff(THREE, from_version=from_version, to_version=3)
            )
            assert "from_version" in text

    def test_from_version_at_latest_with_to_defaulted(self):
        _error_text(_diff(THREE, from_version=3))

    def test_unknown_page(self):
        text = _error_text(_diff(THREE, page_name="NoSuchPage"))
        assert "not_found" in text
        assert "NoSuchPage" in text

    def test_to_version_beyond_latest(self):
        text = _error_text(_diff(THREE, to_version=9))
        assert "not_found" in text
        assert "latest is 3" in text

    def test_missing_page_name(self):
        result = asyncio.run(_handle_diff(FakeWikiClient(THREE), {}))
        assert "page_name" in _error_text(result)


class TestDiffShape:
    def test_context_zero_shows_only_changed_lines(self):
        out = _ok(_diff(THREE, from_version=1, to_version=2, context=0))
        body = out["diff"].split("\n", 2)[2]
        assert "alpha" not in body and "gamma" not in body

    def test_missing_final_newline_is_marked(self):
        versions = [("a\nb", "x", "v1"), ("a\nc", "x", "v2")]
        out = _ok(_diff(versions))
        assert "-b\n\\ No newline at end of file\n+c\n" in out["diff"]
        assert out["diff"].endswith("\\ No newline at end of file\n")

    def test_crlf_against_lf_diffs_only_the_changed_line(self):
        versions = [
            ("one\r\ntwo\r\nthree\r\n", "nerpa_admin", "gui save"),
            ("one\ntwo\nTHREE\n", "agent_rpc", "lf save"),
        ]
        out = _ok(_diff(versions))
        assert (out["added"], out["removed"]) == (1, 1)
        assert out["line_endings_normalized"] is True

    def test_only_line_endings_differ(self):
        versions = [("a\r\nb\r\n", "x", "v1"), ("a\nb\n", "x", "v2")]
        out = _ok(_diff(versions))
        assert out["identical"] is False
        assert out["diff"] == ""
        assert out["line_endings_normalized"] is True

    def test_unicode_line_separators_do_not_split_lines(self):
        # str.splitlines() would split on \x0c and   and report
        # changes on lines that do not exist in the stored text.
        versions = [
            ("a\x0cb c\nkeep\n", "x", "v1"),
            ("a\x0cb c\nKEEP\n", "x", "v2"),
        ]
        out = _ok(_diff(versions))
        assert (out["added"], out["removed"]) == (1, 1)


class TestTruncation:
    @staticmethod
    def _many_hunks(n):
        before = "".join(f"line {i}\n" + "pad\n" * 10 for i in range(n))
        after = "".join(f"LINE {i}\n" + "pad\n" * 10 for i in range(n))
        return [(before, "x", "v1"), (after, "x", "v2")]

    def test_cut_at_a_hunk_boundary_with_full_counts(self):
        out = _ok(_diff(self._many_hunks(200), max_chars=2000))
        assert out["truncated"] is True
        assert out["hunks"] == 200
        assert 0 < out["hunks_shown"] < 200
        assert len(out["diff"]) <= 2000
        assert out["diff"].count("\n@@ ") == out["hunks_shown"]
        # Counts describe the whole diff, not the part returned.
        assert (out["added"], out["removed"]) == (200, 200)

    def test_repeated_lines_do_not_merge_separate_edits(self):
        # difflib.unified_diff's autojunk treats a line making up 1% of a
        # 200+ line page as junk; here that merged every edit into one
        # hunk. It must also stay fast (autojunk=False alone took 33 s).
        out = _ok(_diff(self._many_hunks(200)))
        assert out["hunks"] == 200
        assert (out["added"], out["removed"]) == (200, 200)

    def test_untruncated_reports_every_hunk(self):
        out = _ok(_diff(self._many_hunks(5)))
        assert out["truncated"] is False
        assert out["hunks"] == out["hunks_shown"] == 5

    def test_a_single_oversized_hunk_is_cut_at_a_line(self):
        versions = [
            ("".join(f"old {i}\n" for i in range(500)), "x", "v1"),
            ("".join(f"new {i}\n" for i in range(500)), "x", "v2"),
        ]
        out = _ok(_diff(versions, max_chars=1000))
        assert out["truncated"] is True
        assert out["hunks"] == out["hunks_shown"] == 1
        assert 0 < len(out["diff"]) <= 1000
        assert out["diff"].endswith("\n")


def _keepends(text):
    return re.findall(r"[^\n]*\n|[^\n]+\Z", text)


def _apply(from_text, diff):
    """Apply a unified diff to ``from_text``; the inverse the diff promises."""
    old = _keepends(from_text)
    lines = _keepends(diff)[2:]
    out, pos, idx = [], 0, 0
    while idx < len(lines):
        header = lines[idx].split()[1][1:].split(",")
        start = int(header[0])
        # An empty range names the line BEFORE it, as in GNU diff.
        if len(header) == 2 and int(header[1]) == 0:
            start += 1
        out.extend(old[pos : start - 1])
        pos = start - 1
        idx += 1
        while idx < len(lines) and not lines[idx].startswith("@@ "):
            tag, content = lines[idx][:1], lines[idx][1:]
            idx += 1
            if idx < len(lines) and lines[idx].startswith("\\"):
                content = content[:-1]
                idx += 1
            if tag in (" ", "-"):
                assert old[pos] == content, (old[pos], content)
                pos += 1
            if tag in (" ", "+"):
                out.append(content)
    out.extend(old[pos:])
    return "".join(out)


class TestRoundTrip:
    """The diff applied to the start body must give the end body back.

    Pins correctness independent of any one expected diff: an off-by-one
    in the matcher's blocks or a hunk range shows up as a mismatch here.
    """

    CASES = [
        ("", "a\nb\n"),
        ("a\nb\n", ""),
        ("a\nb\nc\n", "a\nc\n"),
        ("a\nb\nc\n", "x\na\nb\nc\ny\n"),
        ("\n\n\na\n\n\nb\n\n\n", "\n\na\n\n\n\nB\n\n\nc\n"),
        ("a\nb", "a\nb\n"),
        (
            "|| x || y ||\n" * 50,
            "|| x || y ||\n" * 20 + "new\n" + "|| x || y ||\n" * 31,
        ),
    ]

    @pytest.mark.parametrize("before,after", CASES)
    def test_fixed_cases(self, before, after):
        self._check(before, after)

    def test_random_edits(self):
        import random

        rng = random.Random(119)
        vocab = ["", "", "|| a ||", "text", "== H ==", " * item", "x"]
        for _ in range(200):
            before = [
                rng.choice(vocab) for _ in range(rng.randint(0, 40))
            ]
            after = list(before)
            for _ in range(rng.randint(0, 8)):
                op = rng.random()
                pos = rng.randint(0, len(after))
                if op < 0.4:
                    after.insert(pos, rng.choice(vocab))
                elif after and op < 0.8:
                    del after[min(pos, len(after) - 1)]
                elif after:
                    after[min(pos, len(after) - 1)] = rng.choice(vocab)
            self._check(
                "\n".join(before) + "\n", "\n".join(after) + "\n"
            )

    def test_edit_cap_fallback_is_still_a_correct_diff(
        self, monkeypatch
    ):
        from trac_mcp_server.mcp.tools import wiki_read

        monkeypatch.setattr(wiki_read, "_MYERS_MAX_EDITS", 2)
        before = (
            "keep\n" + "".join(f"a{i}\n" for i in range(10)) + "end\n"
        )
        after = (
            "keep\n" + "".join(f"b{i}\n" for i in range(10)) + "end\n"
        )
        out = self._check(before, after)
        assert (out["added"], out["removed"]) == (10, 10)

    @staticmethod
    def _check(before, after):
        versions = [(before, "x", "v1"), (after, "x", "v2")]
        out = _ok(_diff(versions, context=1))
        if before != after:
            assert _apply(before, out["diff"]) == after
        return out


class TestToolDefinition:
    def test_registered_read_only_with_wiki_view(self):
        tool = next(t for t in WIKI_TOOLS if t.name == "wiki_diff")
        assert tool.annotations.readOnlyHint is True
        assert tool.annotations.destructiveHint is False
        assert tool.inputSchema["required"] == ["page_name"]
        for name in (
            "from_version",
            "to_version",
            "from_author",
            "context",
            "max_chars",
        ):
            assert name in tool.inputSchema["properties"]
        spec = next(
            s for s in WIKI_READ_SPECS if s.tool.name == "wiki_diff"
        )
        assert spec.permissions == frozenset({"WIKI_VIEW"})
        assert spec.handler is _handle_diff


# ---------------------------------------------------------------------------
# Live: /trac_test, two real Trac accounts
# ---------------------------------------------------------------------------


@pytest.mark.live
class TestWikiDiffLive:
    """Requires --run-live and TRAC_URL/USERNAME/PASSWORD plus the second
    account TRAC_USERNAME_2/PASSWORD_2 (ci-live.sh checks both pairs).
    Writes one Scratch/ page on /trac_test and deletes it afterwards."""

    def test_versions_written_by_two_identities(self):
        from trac_mcp_server.config import Config
        from trac_mcp_server.core.client import TracClient
        from trac_mcp_server.instances import Identity, InstanceRegistry

        default_config = Config(
            trac_url=os.environ["TRAC_URL"],
            username=os.environ["TRAC_USERNAME"],
            password=os.environ["TRAC_PASSWORD"],
            insecure=os.environ.get("TRAC_INSECURE", "").lower()
            in ("1", "true", "yes"),
        )
        registry = InstanceRegistry(default_config, {})
        user_a = os.environ["TRAC_USERNAME"]
        user_b = os.environ["TRAC_USERNAME_2"]
        client_a = TracClient(registry.resolve("/trac_test"))
        client_b = TracClient(
            registry.resolve(
                "/trac_test",
                Identity(
                    name="second",
                    username=user_b,
                    password=os.environ["TRAC_PASSWORD_2"],
                ),
            )
        )

        page = f"Scratch/Ticket119WikiDiff{uuid.uuid4().hex[:8]}"
        v1 = (
            "Ticket 119 live diff scratch.\n\nfirst line\nsecond line\n"
        )
        v2 = v1.replace("second line", "second line, edited")
        v3 = v2 + "third line\n"
        client_a.put_wiki_page(page, v1, "v1 by A")
        try:
            client_b.put_wiki_page(
                page, v2, "B asks: is this wording right?"
            )
            client_b.put_wiki_page(page, v3, "B adds a line")

            def diff(**args):
                return _ok(
                    asyncio.run(
                        _handle_diff(
                            client_a, {"page_name": page, **args}
                        )
                    )
                )

            latest = diff()
            assert (latest["from_version"], latest["to_version"]) == (
                2,
                3,
            )
            assert (latest["added"], latest["removed"]) == (1, 0)
            assert latest["revisions"][0]["author"] == user_b

            since_a = diff(from_author=user_a)
            assert (since_a["from_version"], since_a["to_version"]) == (
                1,
                3,
            )
            assert [
                (r["version"], r["author"], r["comment"])
                for r in since_a["revisions"]
            ] == [
                (2, user_b, "B asks: is this wording right?"),
                (3, user_b, "B adds a line"),
            ]
            assert (
                "-second line\n+second line, edited\n"
                in since_a["diff"]
            )
            assert since_a["line_endings_normalized"] is False

            first = diff(to_version=1)
            assert first["from_version"] is None
            assert first["added"] == v1.count("\n")

            since_b = diff(from_author=user_b)
            assert since_b["identical"] is True
            assert since_b["revisions"] == []

            explicit = diff(from_version=1, to_version=2)
            assert (explicit["added"], explicit["removed"]) == (1, 1)
        finally:
            client_a.delete_wiki_page(page)
