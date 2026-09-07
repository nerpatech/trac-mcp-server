"""The write-time link gate, wired into every write path (ticket #64).

`Rules/testing/SeededDefectFirst` is the whole shape of this file: a gate
that has never been observed refusing is indistinguishable from one that
always passes, and "the offline suite is green" is exactly what a gate
wired into nothing looks like. So **every write path gets both halves** --
a real handler call watched refusing a seeded broken link, and the
corrected content watched being allowed through to the store.

The seeded defect is a dead local wiki link, and the HTML below is what
the live daemon actually rendered for it (captured 2026-09-04), not
hand-written markup that merely resembles it. `missing_local_target` is
the right seed rather than a more exotic code because it is the one #79
spent a whole ticket narrowing: if the narrowing ever regresses, this
file fails loudly on a path that refuses real writes.

One thing worth stating because the ordinary suite hides it: with a bare
`MagicMock` client the gate cannot render, takes its fail-open path and
reports the content as UNCHECKED. That is correct behaviour and it is
asserted here directly -- but it also means the other write-path test
modules are no longer evidence that the gate does anything, and this one
is.
"""

import asyncio
import xmlrpc.client
from unittest.mock import MagicMock, patch

import pytest

from trac_mcp_server.mcp.tools.milestone import (
    _handle_create as _handle_milestone_create,
)
from trac_mcp_server.mcp.tools.milestone import (
    _handle_update as _handle_milestone_update,
)
from trac_mcp_server.mcp.tools.ticket_batch import (
    _handle_batch_create,
    _handle_batch_update,
)
from trac_mcp_server.mcp.tools.ticket_write import (
    _handle_create as _handle_ticket_create,
)
from trac_mcp_server.mcp.tools.ticket_write import (
    _handle_update as _handle_ticket_update,
)
from trac_mcp_server.mcp.tools.wiki_write import (
    _handle_create as _handle_wiki_create,
)
from trac_mcp_server.mcp.tools.wiki_write import (
    _handle_update as _handle_wiki_update,
)
from trac_mcp_server.mcp.tools.write_gate import (
    check_write,
    gate_enabled,
    gate_or_refuse,
)

# Captured from the live daemon: `See [[NoSuchPageHereAtAll]] for
# detail.` Trac marks a dead local target with `class="missing wiki"`,
# which is the only signal distinguishing it from a live link.
BROKEN_HTML = (
    '<p>\nSee <a class="missing wiki" '
    'href="http://192.168.10.4:8000/trac_mcp_server/wiki/'
    'NoSuchPageHereAtAll" rel="nofollow">NoSuchPageHereAtAll</a> '
    "for detail.\n</p>\n"
)
BROKEN_SOURCE = "See [[NoSuchPageHereAtAll]] for detail."

CLEAN_HTML = "<p>\nNothing to see here.\n</p>\n"
CLEAN_SOURCE = "Nothing to see here."


def _client(html=CLEAN_HTML):
    """A client that renders, so the gate can actually run.

    `wiki_to_html` is the one call the gate makes on its own behalf;
    everything else on this mock belongs to the handler under test.
    """
    client = MagicMock()
    client.config = MagicMock()
    client.config.write_gate = True
    client.config.max_batch_size = 500
    client.config.max_parallel_requests = 5
    client.wiki_to_html.return_value = html
    return client


def _text(result):
    return "\n".join(
        c.text for c in result.content if hasattr(c, "text")
    )


# ---------------------------------------------------------------------
# The unit: check_write.
# ---------------------------------------------------------------------


def test_check_write_refuses_a_broken_link():
    outcome = asyncio.run(
        check_write(
            _client(BROKEN_HTML),
            BROKEN_SOURCE,
            field="content",
            recheck_with="wiki_render_check",
        )
    )
    assert outcome.refused
    assert outcome.checked
    assert "missing_local_target" in outcome.refusal_text
    assert "Refusing to write content" in outcome.refusal_text


def test_check_write_allows_clean_content():
    outcome = asyncio.run(
        check_write(
            _client(),
            CLEAN_SOURCE,
            field="content",
            recheck_with="wiki_render_check",
        )
    )
    assert not outcome.refused
    assert outcome.checked


def test_empty_content_is_not_checked_and_not_a_finding():
    """A write that carries no text for this field has nothing to
    check. Distinct from clean content: `checked` is False, so nothing
    downstream can report it as verified."""
    outcome = asyncio.run(
        check_write(
            _client(),
            "",
            field="comment",
            recheck_with="ticket_render_check",
        )
    )
    assert not outcome.refused
    assert not outcome.checked


def test_a_render_failure_does_not_refuse_but_says_so_loudly():
    """The gate failing is not the author's fault -- refusing here would
    stop every write on the store whenever the renderer hiccups. But
    silence would read as "checked and clean", which is the failure
    ticket #64 section 3 exists to prevent, so the response must say
    UNCHECKED in as many words."""
    client = _client()
    client.wiki_to_html.side_effect = xmlrpc.client.Fault(1, "boom")

    outcome = asyncio.run(
        check_write(
            client,
            BROKEN_SOURCE,
            field="description",
            recheck_with="ticket_render_check",
        )
    )
    assert not outcome.refused
    assert not outcome.checked
    note = "\n".join(outcome.summary_lines())
    assert "UNCHECKED" in note
    assert "not verified clean" in note
    assert "Re-check it with ticket_render_check." in note


def test_the_unchecked_note_names_no_tool_when_none_covers_the_field():
    """Ticket #87. This note used to infer its tool from the field name
    -- `content` meant wiki, anything else meant ticket -- which was
    right for all five write paths that existed and wrong for the
    milestone description this ticket added, where NO render-check tool
    can see the content.

    Asserted as an absence as well as a presence, because the failure
    being guarded against is a confidently wrong instruction, not a
    missing one: naming `ticket_render_check` here sends the author to
    a tool that will not show them their milestone, on the one path
    whose entire purpose is to stop an unchecked write reading as a
    clean one."""
    client = _client()
    client.wiki_to_html.side_effect = xmlrpc.client.Fault(1, "boom")

    outcome = asyncio.run(
        check_write(
            client,
            BROKEN_SOURCE,
            field="description",
            recheck_with=None,
        )
    )
    note = "\n".join(outcome.summary_lines())
    assert "UNCHECKED" in note
    assert "render_check" not in note
    assert "read the rendered page" in note


def test_the_pragma_lets_deliberate_content_through():
    """Ticket #64 section 6: the #58 opt-out governs blocking too.
    Scoped to the code it names, never a document-wide mute."""
    source = (
        BROKEN_SOURCE + "\npreview-checks: allow missing_local_target\n"
    )
    outcome = asyncio.run(
        check_write(
            _client(BROKEN_HTML),
            source,
            field="content",
            recheck_with="wiki_render_check",
        )
    )
    assert not outcome.refused, outcome.refusal_text


def test_the_pragma_is_scoped_to_the_code_it_names():
    """Opting out of one code must not mute another. Without this the
    hatch quietly becomes the document-wide mute #58 refused to
    build."""
    source = (
        BROKEN_SOURCE + "\npreview-checks: allow escaped_link_target\n"
    )
    outcome = asyncio.run(
        check_write(
            _client(BROKEN_HTML),
            source,
            field="content",
            recheck_with="wiki_render_check",
        )
    )
    assert outcome.refused
    assert "missing_local_target" in outcome.refusal_text


def test_the_kill_switch_turns_the_gate_off():
    """Ruling 5. Default on; off only for an emergency on the shared
    daemon."""
    client = _client(BROKEN_HTML)
    assert gate_enabled(client)

    client.config.write_gate = False
    assert not gate_enabled(client)

    refusal, lines = asyncio.run(
        gate_or_refuse(
            client,
            {"content": BROKEN_SOURCE},
            {},
            recheck_with="wiki_render_check",
        )
    )
    assert refusal is None and lines == []
    client.wiki_to_html.assert_not_called()


def test_gate_defaults_on_for_a_config_without_the_field():
    """A Config built before this field existed must keep the gate,
    not silently lose it."""

    class OldConfig:
        pass

    client = MagicMock()
    client.config = OldConfig()
    assert gate_enabled(client)


def test_first_refusal_wins_across_fields():
    """Each field costs a render round trip, and an author who has to
    fix the description will re-send the comment with it anyway."""
    refusal, _ = asyncio.run(
        gate_or_refuse(
            _client(BROKEN_HTML),
            {"description": BROKEN_SOURCE, "comment": BROKEN_SOURCE},
            {},
            recheck_with="ticket_render_check",
        )
    )
    assert refusal is not None
    assert "Refusing to write description" in _text(refusal)


# ---------------------------------------------------------------------
# The wiring. One refusal and one allow per write path -- the half that
# proves the gate is actually connected to each handler.
# ---------------------------------------------------------------------


def _wiki_create(client, content):
    with patch(
        "trac_mcp_server.mcp.tools.wiki_write.run_sync"
    ) as run_sync:
        run_sync.side_effect = [
            xmlrpc.client.Fault(404, "Page not found"),
            {"name": "P", "version": 1},
        ]
        return asyncio.run(
            _handle_wiki_create(
                client, {"page_name": "P", "content": content}
            )
        )


def _wiki_update(client, content):
    with patch(
        "trac_mcp_server.mcp.tools.wiki_write.run_sync"
    ) as run_sync:
        run_sync.return_value = {"name": "P", "version": 2}
        return asyncio.run(
            _handle_wiki_update(
                client,
                {"page_name": "P", "content": content, "version": 1},
            )
        )


def _ticket_create(client, description):
    with patch(
        "trac_mcp_server.mcp.tools.ticket_write.run_sync"
    ) as run_sync:
        run_sync.return_value = 42
        return asyncio.run(
            _handle_ticket_create(
                client, {"summary": "S", "description": description}
            )
        )


def _ticket_update_comment(client, comment):
    with patch(
        "trac_mcp_server.mcp.tools.ticket_write.run_sync"
    ) as run_sync:
        run_sync.return_value = None
        return asyncio.run(
            _handle_ticket_update(
                client, {"ticket_id": 1, "comment": comment}
            )
        )


def _ticket_update_description(client, description):
    with patch(
        "trac_mcp_server.mcp.tools.ticket_write.run_sync"
    ) as run_sync:
        run_sync.return_value = None
        return asyncio.run(
            _handle_ticket_update(
                client, {"ticket_id": 1, "description": description}
            )
        )


def _milestone_create(client, description):
    with patch(
        "trac_mcp_server.mcp.tools.milestone.run_sync"
    ) as run_sync:
        run_sync.return_value = None
        return asyncio.run(
            _handle_milestone_create(
                client,
                {
                    "name": "M",
                    "attributes": {"description": description},
                },
            )
        )


def _milestone_update(client, description):
    with patch(
        "trac_mcp_server.mcp.tools.milestone.run_sync"
    ) as run_sync:
        run_sync.return_value = None
        return asyncio.run(
            _handle_milestone_update(
                client,
                {
                    "name": "M",
                    "attributes": {"description": description},
                },
            )
        )


WRITE_PATHS = [
    ("wiki_create", _wiki_create),
    ("wiki_update", _wiki_update),
    ("ticket_create", _ticket_create),
    ("ticket_update.comment", _ticket_update_comment),
    ("ticket_update.description", _ticket_update_description),
    # Ticket #87. A milestone description is TracWiki on the same
    # substrate as the five above, and was outside #64's enumerated
    # write paths -- deliberately, but that left it the one gap in an
    # otherwise blocking gate.
    ("milestone_create", _milestone_create),
    ("milestone_update", _milestone_update),
]


@pytest.mark.parametrize("name,call", WRITE_PATHS)
def test_every_write_path_refuses_a_broken_link(name, call):
    """The seeded defect, watched refusing on each path in turn.

    Parametrised rather than written once against a helper, because
    what is being tested is precisely that each HANDLER calls the gate
    -- a shared helper test would pass with the wiring missing from
    every one of them.
    """
    result = call(_client(BROKEN_HTML), BROKEN_SOURCE)
    assert result.isError, f"{name} did not refuse"
    assert "missing_local_target" in _text(result)


@pytest.mark.parametrize("name,call", WRITE_PATHS)
def test_every_write_path_allows_correct_content(name, call):
    """The other half. A gate that refuses everything passes the test
    above and is useless; this is what says the refusal was about the
    content."""
    result = call(_client(CLEAN_HTML), CLEAN_SOURCE)
    assert not result.isError, _text(result)


def test_reply_to_does_not_charge_the_author_for_the_quoted_comment():
    """A reply quotes an earlier comment verbatim. Refusing the reply
    because that OLDER comment carries a broken link would charge an
    author for text they did not write and cannot edit -- this host has
    no comment edit at all (#38).

    So the gate checks the author's own comment, not the assembled
    body. The quoted text here is broken; the reply is not.
    """
    client = _client(CLEAN_HTML)
    with patch(
        "trac_mcp_server.mcp.tools.ticket_write.run_sync"
    ) as run_sync:
        run_sync.side_effect = [
            # get_ticket_changelog: one earlier comment, with the
            # dead link in it.
            [
                (
                    "ts",
                    "alice",
                    "comment",
                    "1",
                    BROKEN_SOURCE,
                    1,
                )
            ],
            None,
        ]
        result = asyncio.run(
            _handle_ticket_update(
                client,
                {
                    "ticket_id": 1,
                    "comment": CLEAN_SOURCE,
                    "reply_to": 1,
                },
            )
        )
    assert not result.isError, _text(result)


# ---------------------------------------------------------------------
# The batch tools: per item, not per call.
# ---------------------------------------------------------------------


def test_batch_create_refuses_one_item_and_writes_the_rest():
    """Ticket #64's coverage note: a batch is not all-or-nothing today,
    and one broken link must not refuse nineteen good tickets. The
    refused item is reported in that item's own error field, which is
    how these tools already report per-item failure."""
    client = _client()

    def render(content):
        return BROKEN_HTML if "NoSuchPage" in content else CLEAN_HTML

    client.wiki_to_html.side_effect = render

    with patch(
        "trac_mcp_server.mcp.tools.ticket_batch.run_sync_limited"
    ) as run_sync:
        run_sync.return_value = 7
        result = asyncio.run(
            _handle_batch_create(
                client,
                {
                    "tickets": [
                        {
                            "summary": "good",
                            "description": CLEAN_SOURCE,
                        },
                        {
                            "summary": "bad",
                            "description": BROKEN_SOURCE,
                        },
                    ]
                },
            )
        )

    text = _text(result)
    assert "1/2 succeeded, 1 failed" in text
    assert "missing_local_target" in text


def test_batch_update_refuses_one_item_and_writes_the_rest():
    client = _client()

    def render(content):
        return BROKEN_HTML if "NoSuchPage" in content else CLEAN_HTML

    client.wiki_to_html.side_effect = render

    with patch(
        "trac_mcp_server.mcp.tools.ticket_batch.run_sync_limited"
    ) as run_sync:
        run_sync.return_value = None
        result = asyncio.run(
            _handle_batch_update(
                client,
                {
                    "updates": [
                        {"ticket_id": 1, "comment": CLEAN_SOURCE},
                        {"ticket_id": 2, "comment": BROKEN_SOURCE},
                    ]
                },
            )
        )

    text = _text(result)
    assert "1/2 succeeded, 1 failed" in text
    assert "missing_local_target" in text


# ---------------------------------------------------------------------
# Live acceptance (ticket #64 section 8).
#
# Everything above runs against a captured render. That is the right
# default -- deterministic, and the fixture came from the real daemon --
# but it cannot see the one thing that matters most here: whether a
# refusal actually PREVENTS a write against real Trac, or merely
# returns an error while the bytes land anyway.
#
# Ticket #81 is this project's standing evidence that the offline half
# does not catch this class: two defects landed green through ci.sh in
# one session, one of them a live test asserting a false positive #79
# had already removed. A blocking write gate is exactly the change
# where that gap costs the most.
# ---------------------------------------------------------------------


@pytest.mark.live
class TestWriteGateLive:
    """The gate watched refusing and allowing a REAL write.

    Writes go to the scratch page ticket #84 seeded for exactly this
    (`TRAC_TEST_WIKI_PAGE`, default `TracConvertLiveTest`), which is
    reset rather than appended to, so repeated runs cannot accumulate.
    """

    @staticmethod
    def _live():
        import os

        from trac_mcp_server.config_bootstrap import bootstrap_config
        from trac_mcp_server.core.client import TracClient

        config, _ = bootstrap_config()
        page = os.environ.get(
            "TRAC_TEST_WIKI_PAGE", "TracConvertLiveTest"
        )
        return TracClient(config), page

    def test_a_broken_link_is_refused_and_the_page_is_unchanged(self):
        """The half that matters: not just that an error came back, but
        that the store still holds what it held before. An error
        response over a completed write would be the worst of both."""
        from trac_mcp_server.mcp.tools.wiki_write import (
            _handle_update as handle_update,
        )

        client, page = self._live()
        info = client.get_wiki_page_info(page)
        before = client.get_wiki_page(page)

        result = asyncio.run(
            handle_update(
                client,
                {
                    "page_name": page,
                    "content": (
                        "Seeded for the #64 live gate row.\n\n"
                        + BROKEN_SOURCE
                    ),
                    "version": info["version"],
                },
            )
        )

        assert result.isError, _text(result)
        assert "missing_local_target" in _text(result)
        assert client.get_wiki_page(page) == before, (
            "the gate returned an error but the write still landed"
        )

    def test_correct_content_is_allowed_through_to_the_store(self):
        """The other half. Asserted by reading the page back, because
        a success response is not evidence that anything was stored --
        the same distinction `Rules/testing/VerificationIsAHandoff`
        draws."""
        from trac_mcp_server.mcp.tools.wiki_write import (
            _handle_update as handle_update,
        )

        client, page = self._live()
        info = client.get_wiki_page_info(page)
        body = (
            "Seeded for the #64 live gate row. This body is clean: "
            "it links to trac_mcp_server:#64 and nothing else.\n"
        )

        result = asyncio.run(
            handle_update(
                client,
                {
                    "page_name": page,
                    "content": body,
                    "version": info["version"],
                },
            )
        )

        assert not result.isError, _text(result)
        assert client.get_wiki_page(page) == body


# ---------------------------------------------------------------------
# Ticket #87: the milestone half, live.
#
# On `/trac_test` rather than the project's own instance, because these
# rows CREATE and DELETE a real milestone and there is no scratch
# milestone to reset the way #84 seeded a scratch wiki page. That
# instance exists for exactly this and carries standing authorisation
# (auto_pm `Reference/trac/TestingInstance`); cleaning up after is still
# expected, so every row does.
#
# Measured here before these tests were written, and the reason the
# ticket asked rather than assumed: Trac renders a milestone description
# through the wiki formatter and marks a dead local target
# `class="missing wiki"` -- the SAME classes `wiki_to_html` hands the
# gate. So what the gate refuses is what a reader of the milestone page
# would actually have seen.
# ---------------------------------------------------------------------


@pytest.mark.live
class TestMilestoneWriteGateLive:
    """The gate watched refusing and allowing a REAL milestone write."""

    NAME = "Ticket87WriteGateLive"

    @staticmethod
    def _live():
        from trac_mcp_server.config_bootstrap import bootstrap_config
        from trac_mcp_server.core.client import TracClient
        from trac_mcp_server.instances import InstanceRegistry

        config, _ = bootstrap_config()
        return TracClient(
            InstanceRegistry(config, {}).resolve("/trac_test")
        )

    @pytest.fixture(autouse=True)
    def _scratch(self):
        """Delete the scratch milestone before AND after each row.

        Before as well as after: a previous run interrupted between
        create and cleanup would otherwise leave a milestone behind,
        and its presence would make
        `test_a_broken_link_is_refused_and_no_milestone_is_created`
        pass for entirely the wrong reason.
        """
        client = self._live()
        self._remove(client)
        try:
            yield
        finally:
            self._remove(client)

    @classmethod
    def _remove(cls, client):
        try:
            client.delete_milestone(cls.NAME)
        except Exception:
            # Absent is the desired state; not existing is not an error.
            pass

    def test_a_broken_link_is_refused_and_no_milestone_is_created(self):
        """Not just that an error came back, but that the store did not
        gain a milestone anyway -- the case an offline test cannot see,
        and the one #64's live row exists for."""
        from trac_mcp_server.mcp.tools.milestone import (
            _handle_create as handle_create,
        )

        client = self._live()

        result = asyncio.run(
            handle_create(
                client,
                {
                    "name": self.NAME,
                    "attributes": {"description": BROKEN_SOURCE},
                },
            )
        )

        assert result.isError, _text(result)
        assert "missing_local_target" in _text(result)
        assert self.NAME not in client.get_all_milestones(), (
            "the gate returned an error but the milestone was created"
        )

    def test_correct_content_is_allowed_through_to_the_store(self):
        """The other half: a gate that refuses everything passes the
        row above and is useless. Asserted by reading the description
        back, not from the success response."""
        from trac_mcp_server.mcp.tools.milestone import (
            _handle_create as handle_create,
        )

        client = self._live()
        # Deliberately link-free. An earlier draft of this row said it
        # "links to trac_mcp_server:#87 and nothing else" and the gate
        # refused it -- correctly: the `trac_mcp_server` InterTrac
        # prefix is not configured on /trac_test, so that renders as a
        # bare ticket link resolved against /trac_test, where 87 does
        # not exist. The allow row has to be clean ON THE INSTANCE IT
        # WRITES TO, which is not the instance this ticket lives on.
        body = (
            "Seeded for the ticket 87 live gate row. Nothing to "
            "link to.\n"
        )

        result = asyncio.run(
            handle_create(
                client,
                {
                    "name": self.NAME,
                    "attributes": {"description": body},
                },
            )
        )

        assert not result.isError, _text(result)
        stored = client.get_milestone(self.NAME)
        assert stored["description"] == body

    def test_a_refused_update_leaves_the_old_description_intact(self):
        """The update path's own half of the "error returned but the
        bytes landed" question. Distinct from the create row: a create
        that half-lands leaves a milestone that was not there before,
        an update that half-lands destroys one that was."""
        from trac_mcp_server.mcp.tools.milestone import (
            _handle_update as handle_update,
        )

        client = self._live()
        original = (
            "Seeded for the ticket 87 live gate row. Original body.\n"
        )
        client.create_milestone(self.NAME, {"description": original})

        result = asyncio.run(
            handle_update(
                client,
                {
                    "name": self.NAME,
                    "attributes": {"description": BROKEN_SOURCE},
                },
            )
        )

        assert result.isError, _text(result)
        assert "missing_local_target" in _text(result)
        assert (
            client.get_milestone(self.NAME)["description"] == original
        ), "the gate returned an error but the update still landed"
