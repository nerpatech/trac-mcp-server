"""Tests for preview.targets -- the capped live probe for cross-instance
InterTrac targets (tickets #56 and #82).

Calibrated against a known-missing AND a known-existing page (the ticket's
own requirement, since Trac returns HTTP 200 for both), using canned
response bodies rather than the live daemon -- ``test_mcp.tools.
test_convert_preview.TestConvertPreviewLive`` covers the real substrate.
"""

import threading
import unittest
from unittest.mock import MagicMock

from trac_mcp_server.preview.targets import (
    ERROR,
    EXISTS,
    MISSING,
    SKIPPED,
    is_probeable_href,
    is_probeable_ticket_href,
    is_probeable_wiki_href,
    probe_targets,
)

EXISTING_PAGE_STUB = (
    '<div id="content" class="wiki view narrow">'
    "<h1>RenderVerify</h1></div>"
)
MISSING_PAGE_STUB = (
    '<div id="content" class="wiki create narrow">'
    "<strong>DoesNotExist</strong> does not exist. "
    "You can create it here.</div>"
)

EXISTING_HREF = (
    "http://192.168.10.4:8000/auto_pm/intertrac/wiki%3ARenderVerify"
)
MISSING_HREF = (
    "http://192.168.10.4:8000/auto_pm/intertrac/wiki%3ADoesNotExist"
)

# Ticket realm, both dispatcher shapes. Measured against the live
# dispatcher 2026-09-04: a ticket that exists answers 200 and redirects
# to /ticket/N; one that does not answers a bare 500. Same on
# trac.edgewall.org, whose body differs (9 KB error page vs empty) --
# which is why the classification is on STATUS.
TICKET_HREF = "http://192.168.10.4:8000/auto_pm/intertrac/%2387"
REALM_TICKET_HREF = (
    "http://192.168.10.4:8000/auto_pm/intertrac/ticket%3A87"
)
DEAD_TICKET_HREF = (
    "http://192.168.10.4:8000/auto_pm/intertrac/%23999999"
)
CONTROL_URL = (
    "http://192.168.10.4:8000/auto_pm"
    "/intertrac/wiki%3ATracMcpServerProbeLivenessControl"
)


def _mock_client(
    responses: dict[str, tuple[int, str]],
    resolved: dict[str, str] | None = None,
) -> MagicMock:
    def fake_get(url, timeout=None, allow_redirects=None):
        status, body = responses[url]
        resp = MagicMock()
        resp.status_code = status
        resp.text = body
        resp.url = (resolved or {}).get(url, url)
        return resp

    # `session` is a PROPERTY on the real client, returning the
    # calling thread's own session -- so the mock counts accesses
    # rather than exposing a plain attribute, which is what lets
    # `test_each_worker_uses_its_own_thread_local_session` see whether
    # the implementation hoisted it out of the loop.
    session = MagicMock()
    session.get.side_effect = fake_get

    class _Client:
        def __init__(self):
            self.session_access_count = 0
            self._lock = threading.Lock()

        @property
        def session(self):
            with self._lock:
                self.session_access_count += 1
            return session

    return _Client()


class TestIsProbeableWikiHref(unittest.TestCase):
    def test_intertrac_wiki_href_is_probeable(self):
        self.assertTrue(is_probeable_wiki_href(EXISTING_HREF))

    def test_intertrac_ticket_href_is_probeable(self):
        """This test used to assert the defect.

        It read `test_intertrac_ticket_href_is_not_probeable` and
        justified it as "out of scope -- different not-found template".
        Ticket #82 measured that claim and it was false twice over:
        there is no template, a missing ticket answers a bare 500, and
        the realm being out of scope is exactly why 695 cross-instance
        ticket references across two stores were never checked at all.

        Inverted rather than deleted, so the row stays a witness.
        """
        self.assertTrue(is_probeable_href(TICKET_HREF))
        self.assertTrue(is_probeable_ticket_href(TICKET_HREF))
        # The wiki predicate itself stays narrow: it decides how a
        # target is CLASSIFIED, and the two realms classify by opposite
        # rules.
        self.assertFalse(is_probeable_wiki_href(TICKET_HREF))

    def test_realm_form_ticket_href_is_probeable(self):
        """`prefix:ticket:N` renders as /intertrac/ticket%3AN, not
        /intertrac/%23N. Ten references in the corpus take this shape,
        and a fix keyed on the short link alone would leave every one of
        them invisible."""
        self.assertTrue(is_probeable_href(REALM_TICKET_HREF))
        self.assertTrue(is_probeable_ticket_href(REALM_TICKET_HREF))

    def test_non_intertrac_href_is_not_probeable(self):
        """A plain local ticket link is not a dispatcher href at all."""
        self.assertFalse(
            is_probeable_href("http://host/auto_pm/ticket/87")
        )

    def test_none_href_is_not_probeable(self):
        self.assertFalse(is_probeable_wiki_href(None))
        self.assertFalse(is_probeable_ticket_href(None))
        self.assertFalse(is_probeable_href(None))


class TestProbeTargets(unittest.TestCase):
    def test_existing_page_classified_exists(self):
        client = _mock_client(
            {EXISTING_HREF: (200, EXISTING_PAGE_STUB)}
        )
        results = probe_targets(client, [EXISTING_HREF])
        self.assertEqual(results[EXISTING_HREF]["status"], EXISTS)

    def test_missing_page_classified_missing(self):
        """The calibration case the ticket calls out: HTTP 200 for a
        page that does not exist, distinguished only by the 'create
        this page' stub in the body."""
        client = _mock_client({MISSING_HREF: (200, MISSING_PAGE_STUB)})
        results = probe_targets(client, [MISSING_HREF])
        self.assertEqual(results[MISSING_HREF]["status"], MISSING)

    def test_non_200_classified_error(self):
        client = _mock_client({EXISTING_HREF: (500, "boom")})
        results = probe_targets(client, [EXISTING_HREF])
        self.assertEqual(results[EXISTING_HREF]["status"], ERROR)
        self.assertIsNone(results[EXISTING_HREF]["resolved_url"])

    def test_request_exception_classified_error(self):
        client = MagicMock()
        client.session.get.side_effect = OSError("connection refused")
        results = probe_targets(client, [EXISTING_HREF])
        self.assertEqual(results[EXISTING_HREF]["status"], ERROR)
        self.assertIsNone(results[EXISTING_HREF]["resolved_url"])

    def test_duplicates_deduplicated(self):
        client = _mock_client(
            {EXISTING_HREF: (200, EXISTING_PAGE_STUB)}
        )
        results = probe_targets(
            client, [EXISTING_HREF, EXISTING_HREF, EXISTING_HREF]
        )
        self.assertEqual(client.session.get.call_count, 1)
        self.assertEqual(
            results,
            {
                EXISTING_HREF: {
                    "status": EXISTS,
                    "resolved_url": EXISTING_HREF,
                }
            },
        )

    def test_resolved_url_reports_redirect_target(self):
        """The `/intertrac/%23N`-shaped dispatcher trap: the caller must
        be able to report the target it was actually redirected to, not
        just the dispatcher href it started from."""
        real_target = (
            "http://192.168.10.4:8000/auto_pm/wiki/RenderVerify"
        )
        client = _mock_client(
            {EXISTING_HREF: (200, EXISTING_PAGE_STUB)},
            resolved={EXISTING_HREF: real_target},
        )
        results = probe_targets(client, [EXISTING_HREF])
        self.assertEqual(
            results[EXISTING_HREF]["resolved_url"], real_target
        )

    def test_non_probeable_hrefs_are_dropped(self):
        client = _mock_client({})
        results = probe_targets(
            client, ["http://host/auto_pm/ticket/87"]
        )
        self.assertEqual(results, {})
        client.session.get.assert_not_called()

    def test_cap_marks_excess_as_skipped_not_silent(self):
        """A capped check must never look like a clean pass: anything
        beyond the cap is reported SKIPPED, not simply dropped."""
        hrefs = [f"{EXISTING_HREF}{i}" for i in range(5)]
        responses = {h: (200, EXISTING_PAGE_STUB) for h in hrefs}
        client = _mock_client(responses)
        results = probe_targets(client, hrefs, cap=2)
        statuses = [v["status"] for v in results.values()]
        self.assertEqual(statuses.count(EXISTS), 2)
        self.assertEqual(statuses.count(SKIPPED), 3)
        self.assertEqual(client.session.get.call_count, 2)


class TestCapPosition(unittest.TestCase):
    """Ticket #80: which targets the cap keeps, not that it caps.

    The cap takes the FIRST N in document order, so the unchecked
    region is always the END of the document -- which is exactly where
    an agent appends. Under #64's blocking gate a link written last is
    the one least likely to be checked, which inverts what a write-time
    gate is for.
    """

    def _fifteen_with_one_dead(self, dead_index: int):
        hrefs = [f"{EXISTING_HREF}{i}" for i in range(15)]
        responses = {h: (200, EXISTING_PAGE_STUB) for h in hrefs}
        responses[hrefs[dead_index]] = (200, MISSING_PAGE_STUB)
        return hrefs, responses, hrefs[dead_index]

    def test_dead_target_is_found_wherever_it_sits(self):
        """The seed, and it is a PAIR on purpose.

        Watch the second half RED at `0c50e14`, where the cap is 10:
        the dead target in position 11 is reported SKIPPED and the
        document reports no `missing_cross_instance_target` at all.
        Position 1 was always found.

        Asserting only the appended position would pass just as well
        for a probe that stopped capping altogether, which is the
        opposite defect -- `test_cap_still_caps_at_the_new_default`
        below is the other side of that.
        """
        for label, index in (("first", 0), ("appended", 10)):
            with self.subTest(position=label):
                hrefs, responses, dead = self._fifteen_with_one_dead(
                    index
                )
                results = probe_targets(_mock_client(responses), hrefs)
                self.assertEqual(
                    results[dead]["status"],
                    MISSING,
                    f"a dead target in the {label} position must be "
                    f"reported, got {results[dead]['status']}",
                )

    def test_cap_still_caps_at_the_new_default(self):
        """Raising a cap is precisely where a bound quietly becomes no
        bound. 51 targets must still leave one SKIPPED and named."""
        hrefs = [f"{EXISTING_HREF}{i}" for i in range(51)]
        responses = {h: (200, EXISTING_PAGE_STUB) for h in hrefs}
        client = _mock_client(responses)
        results = probe_targets(client, hrefs)
        statuses = [v["status"] for v in results.values()]
        self.assertEqual(statuses.count(SKIPPED), 1)
        self.assertEqual(client.session.get.call_count, 50)
        self.assertEqual(results[hrefs[50]]["status"], SKIPPED)


class TestConcurrentProbe(unittest.TestCase):
    """The cap is small because the probe is sequential; ticket #80
    fixes the loop rather than the number."""

    def test_every_target_is_classified_exactly_once(self):
        """Completeness under concurrency: one result per unique href,
        each fetched once, none lost and none duplicated."""
        hrefs = [f"{EXISTING_HREF}{i}" for i in range(40)]
        responses = {h: (200, EXISTING_PAGE_STUB) for h in hrefs}
        responses[hrefs[7]] = (200, MISSING_PAGE_STUB)
        responses[hrefs[31]] = (500, "boom")
        client = _mock_client(responses)
        results = probe_targets(client, hrefs)

        self.assertEqual(set(results), set(hrefs))
        self.assertEqual(client.session.get.call_count, 40)
        self.assertEqual(results[hrefs[7]]["status"], MISSING)
        self.assertEqual(results[hrefs[31]]["status"], ERROR)
        self.assertEqual(
            [v["status"] for v in results.values()].count(EXISTS), 38
        )

    def test_each_worker_uses_its_own_thread_local_session(self):
        """`TracClient` hands out a THREAD-LOCAL session, so a worker
        has to ask for it from inside its own thread.

        Hoisting `client.session` once outside the loop -- which is
        what the sequential implementation did, harmlessly -- would
        give every worker the dispatching thread's session, quietly
        defeating that design. Asserted on the property that matters:
        the session is fetched per worker, not once for the batch.
        """
        hrefs = [f"{EXISTING_HREF}{i}" for i in range(12)]
        responses = {h: (200, EXISTING_PAGE_STUB) for h in hrefs}
        client = _mock_client(responses)
        probe_targets(client, hrefs)
        self.assertGreaterEqual(client.session_access_count, len(hrefs))

    def test_probe_is_actually_concurrent(self):
        """Sequential probing is the reason the cap was 10 (50 targets
        at the 5s timeout is 250s of wall clock), so "it is concurrent"
        is the load-bearing claim and gets measured rather than
        assumed: with a blocking fetch, N targets must overlap rather
        than serialise."""
        barrier = threading.Barrier(4, timeout=5)
        hrefs = [f"{EXISTING_HREF}{i}" for i in range(4)]

        def fake_get(url, timeout=None, allow_redirects=None):
            # Deadlocks and trips the barrier's timeout unless at
            # least 4 fetches are genuinely in flight together.
            barrier.wait()
            resp = MagicMock()
            resp.status_code = 200
            resp.text = EXISTING_PAGE_STUB
            resp.url = url
            return resp

        client = _mock_client({})
        client.session.get.side_effect = fake_get
        results = probe_targets(client, hrefs)
        self.assertEqual(
            [v["status"] for v in results.values()], [EXISTS] * 4
        )


class TestTicketRealmProbe(unittest.TestCase):
    """Ticket #82. The ticket realm classifies by the OPPOSITE rule from
    the wiki realm: a 200 is positive proof a ticket exists, while a 500
    is only evidence once the instance has answered a control."""

    def test_existing_ticket_classified_exists(self):
        client = _mock_client(
            {TICKET_HREF: (200, "<html>a ticket page</html>")},
            resolved={
                TICKET_HREF: "http://192.168.10.4:8000/auto_pm/ticket/87"
            },
        )
        results = probe_targets(client, [TICKET_HREF])
        self.assertEqual(results[TICKET_HREF]["status"], EXISTS)
        self.assertEqual(
            results[TICKET_HREF]["resolved_url"],
            "http://192.168.10.4:8000/auto_pm/ticket/87",
        )

    def test_realm_form_classified_like_the_short_link(self):
        client = _mock_client(
            {REALM_TICKET_HREF: (200, "<html>a ticket page</html>")}
        )
        results = probe_targets(client, [REALM_TICKET_HREF])
        self.assertEqual(results[REALM_TICKET_HREF]["status"], EXISTS)

    def test_dead_ticket_reported_missing_when_instance_answers(self):
        """The seed: a 500 with a healthy control is a dead link."""
        client = _mock_client(
            {
                DEAD_TICKET_HREF: (500, ""),
                CONTROL_URL: (200, "<html>the control stub</html>"),
            }
        )
        results = probe_targets(client, [DEAD_TICKET_HREF])
        self.assertEqual(results[DEAD_TICKET_HREF]["status"], MISSING)
        self.assertNotIn("needs_control", results[DEAD_TICKET_HREF])

    def test_dead_and_live_in_one_document(self):
        """The pair, not the row: a check that called every ticket
        reference dead would pass a dead-only test just as well."""
        client = _mock_client(
            {
                TICKET_HREF: (200, "<html>a ticket page</html>"),
                DEAD_TICKET_HREF: (500, ""),
                CONTROL_URL: (200, "<html>the control stub</html>"),
            }
        )
        results = probe_targets(client, [TICKET_HREF, DEAD_TICKET_HREF])
        self.assertEqual(results[TICKET_HREF]["status"], EXISTS)
        self.assertEqual(results[DEAD_TICKET_HREF]["status"], MISSING)

    def test_instance_down_is_uncertainty_not_a_broken_link(self):
        """The row that matters. With the control failing, a 500 is
        indistinguishable from the instance being unwell, and reporting
        it as a dead link would refuse a write under ticket #64 while
        telling the author to fix links that are fine."""
        client = _mock_client(
            {
                DEAD_TICKET_HREF: (500, ""),
                CONTROL_URL: (503, ""),
            }
        )
        results = probe_targets(client, [DEAD_TICKET_HREF])
        self.assertEqual(results[DEAD_TICKET_HREF]["status"], ERROR)

    def test_control_unreachable_is_uncertainty(self):
        """Same row, by exception rather than by status code."""

        def fake_get(url, timeout=None, allow_redirects=None):
            if url == CONTROL_URL:
                raise OSError("no route to host")
            resp = MagicMock()
            resp.status_code = 500
            resp.text = ""
            resp.url = url
            return resp

        client = _mock_client({})
        client.session.get.side_effect = fake_get
        results = probe_targets(client, [DEAD_TICKET_HREF])
        self.assertEqual(results[DEAD_TICKET_HREF]["status"], ERROR)

    def test_no_control_fetched_when_nothing_looks_dead(self):
        """The common document pays nothing: the control is fetched only
        when a candidate came back 500."""
        client = _mock_client(
            {TICKET_HREF: (200, "<html>a ticket page</html>")}
        )
        probe_targets(client, [TICKET_HREF])
        urls = [
            call.args[0] for call in client.session.get.call_args_list
        ]
        self.assertEqual(urls, [TICKET_HREF])

    def test_one_control_per_instance_not_per_target(self):
        dead_b = "http://192.168.10.4:8000/auto_pm/intertrac/%23999998"
        client = _mock_client(
            {
                DEAD_TICKET_HREF: (500, ""),
                dead_b: (500, ""),
                CONTROL_URL: (200, "<html>the control stub</html>"),
            }
        )
        results = probe_targets(client, [DEAD_TICKET_HREF, dead_b])
        self.assertEqual(
            [v["status"] for v in results.values()], [MISSING, MISSING]
        )
        controls = [
            call.args[0]
            for call in client.session.get.call_args_list
            if call.args[0] == CONTROL_URL
        ]
        self.assertEqual(len(controls), 1)

    def test_other_status_codes_are_never_missing(self):
        """404 (no such instance), 401/403 (credentials): the probe
        learned nothing, and nothing is not a broken link."""
        for code in (401, 403, 404, 302):
            with self.subTest(code=code):
                client = _mock_client({DEAD_TICKET_HREF: (code, "")})
                results = probe_targets(client, [DEAD_TICKET_HREF])
                self.assertEqual(
                    results[DEAD_TICKET_HREF]["status"], ERROR
                )

    def test_wiki_realm_classification_is_unchanged(self):
        """The recall side: widening a probe is where the existing path
        quietly changes shape."""
        client = _mock_client(
            {
                EXISTING_HREF: (200, EXISTING_PAGE_STUB),
                MISSING_HREF: (200, MISSING_PAGE_STUB),
            }
        )
        results = probe_targets(client, [EXISTING_HREF, MISSING_HREF])
        self.assertEqual(results[EXISTING_HREF]["status"], EXISTS)
        self.assertEqual(results[MISSING_HREF]["status"], MISSING)


if __name__ == "__main__":
    unittest.main()
