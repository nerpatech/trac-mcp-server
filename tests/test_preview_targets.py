"""Tests for preview.targets -- the capped live probe for cross-instance
InterTrac wiki targets (ticket #56).

Calibrated against a known-missing AND a known-existing page (the ticket's
own requirement, since Trac returns HTTP 200 for both), using canned
response bodies rather than the live daemon -- ``test_mcp.tools.
test_convert_preview.TestConvertPreviewLive`` covers the real substrate.
"""

import unittest
from unittest.mock import MagicMock

from trac_mcp_server.preview.targets import (
    ERROR,
    EXISTS,
    MISSING,
    SKIPPED,
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

    client = MagicMock()
    client.session.get.side_effect = fake_get
    return client


class TestIsProbeableWikiHref(unittest.TestCase):
    def test_intertrac_wiki_href_is_probeable(self):
        self.assertTrue(is_probeable_wiki_href(EXISTING_HREF))

    def test_intertrac_ticket_href_is_not_probeable(self):
        """Ticket-realm InterTrac (`auto_pm:#87`) is out of scope --
        different not-found template, not covered by this probe."""
        self.assertFalse(
            is_probeable_wiki_href(
                "http://192.168.10.4:8000/auto_pm/intertrac/%2387"
            )
        )

    def test_none_href_is_not_probeable(self):
        self.assertFalse(is_probeable_wiki_href(None))


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
            client, ["http://host/auto_pm/intertrac/%2387"]
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


if __name__ == "__main__":
    unittest.main()
