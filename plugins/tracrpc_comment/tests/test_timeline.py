# -*- coding: utf-8 -*-
"""Tests for TimelineRPC against a real Trac environment."""

import unittest
from datetime import datetime, timedelta

from trac.core import Component, implements
from trac.perm import PermissionSystem
from trac.test import EnvironmentStub, MockRequest
from trac.timeline.api import ITimelineEventProvider
# Imported for its side effect: EnvironmentStub only sees components that have
# already been imported, and TimelineModule is what registers TIMELINE_VIEW.
import trac.timeline.web_ui  # noqa: F401
from trac.util.datefmt import utc

from tracrpc_comment.timeline import MAX_EVENTS_CAP, TimelineRPC

WHEN = datetime(2026, 9, 17, 12, 0, 0, tzinfo=utc)


class FakeProvider(Component):
    """Emits 4-tuple events and renders markup, like a real provider."""

    implements(ITimelineEventProvider)

    def get_timeline_filters(self, req):
        yield ('fake', 'Fake events')

    def get_timeline_events(self, req, start, stop, filters):
        if 'fake' not in (filters or []):
            return
        for offset in range(3):
            yield ('fake', WHEN + timedelta(minutes=offset), 'alice',
                   {'n': offset})

    def render_timeline_event(self, context, field, event):
        n = event[3]['n']
        if field == 'url':
            return '/fake/%d' % n
        if field == 'title':
            return '<b>Fake %d</b> happened' % n
        return 'body &lt; %d' % n


class DelegatingProvider(Component):
    """Emits 5-tuple events naming another component as the renderer.

    AttachmentModule does exactly this. A consumer that ignores the fifth
    member renders the event against the wrong component.
    """

    implements(ITimelineEventProvider)

    def get_timeline_filters(self, req):
        yield ('delegated', 'Delegated events')

    def get_timeline_events(self, req, start, stop, filters):
        if 'delegated' not in (filters or []):
            return
        yield ('delegated', WHEN + timedelta(hours=1), 'bob', {'n': 99},
               RealRenderer(self.env))

    def render_timeline_event(self, context, field, event):
        return 'WRONG RENDERER'


class RealRenderer(Component):
    """Not an event provider -- only ever reached via the 5th tuple member."""

    def render_timeline_event(self, context, field, event):
        if field == 'url':
            return '/delegated/99'
        if field == 'title':
            return 'Delegated title'
        return 'Delegated body'


class BrokenProvider(Component):

    implements(ITimelineEventProvider)

    def get_timeline_filters(self, req):
        yield ('broken', 'Broken events')

    def get_timeline_events(self, req, start, stop, filters):
        raise RuntimeError('this provider is broken')

    def render_timeline_event(self, context, field, event):
        raise RuntimeError('this provider is broken')


class TimelineRPCTestCase(unittest.TestCase):

    def setUp(self):
        self.env = EnvironmentStub(
            default_data=True,
            # Derived, not hardcoded: pytest imports this module as
            # `test_timeline`, unittest as `__main__`, and a wrong pattern
            # silently yields zero providers and zero events -- which reads
            # exactly like a working call that found nothing.
            enable=['trac.*', 'tracrpc_comment.*', '%s.*' % __name__])
        PermissionSystem(self.env).grant_permission('reader', 'TIMELINE_VIEW')
        self.rpc = TimelineRPC(self.env)
        self.start = WHEN - timedelta(days=1)
        self.stop = WHEN + timedelta(days=1)

    def tearDown(self):
        self.env.reset_db()

    def _req(self):
        return MockRequest(self.env, authname='reader')

    def _events(self, **kw):
        return self.rpc.getEvents(self._req(), self.start, self.stop, **kw)

    def test_filters_include_the_registered_providers(self):
        names = [name for name, _label in self.getFilters()]
        self.assertIn('fake', names)
        self.assertIn('delegated', names)

    def getFilters(self):
        return self.rpc.getFilters(self._req())

    def test_events_are_rendered_to_plain_text(self):
        events = self._events(filters=['fake'])
        self.assertEqual(3, len(events))
        titles = sorted(e['title'] for e in events)
        self.assertEqual(['Fake 0 happened', 'Fake 1 happened',
                          'Fake 2 happened'], titles)
        # Markup stripped, entities decoded -- not raw HTML.
        self.assertTrue(all(e['description'].startswith('body <')
                            for e in events))
        self.assertTrue(all('<b>' not in e['title'] for e in events))

    def test_events_carry_kind_author_date_and_url(self):
        event = self._events(filters=['fake'])[0]
        self.assertEqual('fake', event['kind'])
        self.assertEqual('alice', event['author'])
        self.assertEqual('/fake/2', event['url'])
        self.assertEqual(WHEN + timedelta(minutes=2), event['date'])

    def test_events_are_newest_first(self):
        dates = [e['date'] for e in self._events(filters=['fake'])]
        self.assertEqual(sorted(dates, reverse=True), dates)

    def test_five_tuple_event_renders_via_the_named_provider(self):
        events = self._events(filters=['delegated'])
        self.assertEqual(1, len(events))
        self.assertEqual('Delegated title', events[0]['title'])
        self.assertEqual('/delegated/99', events[0]['url'])

    def test_a_broken_provider_is_skipped_not_fatal(self):
        events = self._events(filters=['fake', 'broken'])
        self.assertEqual(3, len(events))

    def test_max_results_truncates_after_sorting(self):
        events = self._events(filters=['fake'], max_results=1)
        self.assertEqual(1, len(events))
        self.assertEqual('Fake 2 happened', events[0]['title'])

    def test_max_results_is_capped(self):
        events = self._events(filters=['fake'], max_results=10 ** 9)
        self.assertLessEqual(len(events), MAX_EVENTS_CAP)

    def test_explicitly_empty_filters_select_nothing(self):
        """An empty list means "none", never "all".

        Widening it to everything would hand back a full timeline to a caller
        that asked for no sources at all.
        """
        self.assertEqual([], self._events(filters=[]))

    def test_omitted_filters_select_everything(self):
        events = self._events()
        kinds = {e['kind'] for e in events}
        self.assertIn('fake', kinds)
        self.assertIn('delegated', kinds)

    def test_raw_provider_data_is_not_returned(self):
        """`data` is provider-private and often not serializable."""
        for event in self._events(filters=['fake']):
            self.assertNotIn('data', event)


if __name__ == '__main__':
    unittest.main()
