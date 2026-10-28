# -*- coding: utf-8 -*-
"""The Trac timeline over XML-RPC.

Nothing in XmlRpcPlugin exposes the timeline. The closest substitutes,
``ticket.getRecentChanges`` and ``wiki.getRecentChanges``, each cover a single
realm, return no author or title, and cannot be ordered against one another --
so "what happened on this Trac between X and Y" is not answerable today.

This module aggregates the ``ITimelineEventProvider`` extension point, which
is the same source the /timeline page and its RSS feed are built from.
"""

from trac.core import Component, ExtensionPoint, implements
from trac.timeline.api import ITimelineEventProvider
from trac.util.html import plaintext

from tracrpc.api import IXMLRPCHandler
from tracrpc.util import web_context

__all__ = ['TimelineRPC']

# Refuse to build a response larger than this even if asked, so a wide
# date range cannot turn into an unbounded XML payload.
MAX_EVENTS_CAP = 1000

DEFAULT_MAX_EVENTS = 100


class TimelineRPC(Component):
    """Read the Trac timeline: what changed, when, and who changed it."""

    implements(IXMLRPCHandler)

    event_providers = ExtensionPoint(ITimelineEventProvider)

    # IXMLRPCHandler methods

    def xmlrpc_namespace(self):
        return 'timeline'

    def xmlrpc_methods(self):
        yield ('TIMELINE_VIEW', ((list,),), self.getFilters)
        yield ('TIMELINE_VIEW',
               ((list, str, str),
                (list, str, str, list),
                (list, str, str, list, int)),
               self.getEvents)

    # Exported methods

    def getFilters(self, req):
        """Return the available timeline filters as `(name, label)` pairs.

        These are the names accepted by `timeline.getEvents`. Which filters
        exist depends on the enabled components, so this is worth reading
        rather than assuming.
        """
        filters = []
        for provider in self.event_providers:
            try:
                for entry in provider.get_timeline_filters(req) or []:
                    filters.append([entry[0], entry[1]])
            except Exception:
                self._log_provider_failure(provider, 'get_timeline_filters')
        return filters

    def getEvents(self, req, start, stop, filters=None,
                  max_results=DEFAULT_MAX_EVENTS):
        """Return timeline events between `start` and `stop`, newest first.

        `start` and `stop` are datetimes. `filters` is a list of filter names
        from `timeline.getFilters`; omit it for all of them. `max_results`
        is capped at 1000.

        Each event is a struct with `kind`, `date`, `author`, `title`,
        `description` and `url`. `title` and `description` are plain text --
        the provider's own rendering with markup stripped -- and `url` is
        absolute.

        The raw `data` member of a Trac timeline event is deliberately NOT
        returned: it is private to the provider that emitted it, its shape is
        undocumented and provider-specific, and it is frequently not
        serializable as XML-RPC at all. It is only meaningful when passed
        back to that provider's `render_timeline_event`, which is what this
        method does on the caller's behalf.
        """
        if filters is not None and not filters:
            # An explicitly empty filter list selects nothing. Honour it
            # rather than silently widening to "everything", which would be
            # the opposite of what the caller asked for.
            return []

        limit = min(max(int(max_results), 1), MAX_EVENTS_CAP)
        context = web_context(req, absurls=True)

        collected = []
        for provider in self.event_providers:
            # Deliberately NOT trac.web.chrome.component_guard: that re-raises
            # after logging, so a single misbehaving provider would blank the
            # whole response. A timeline missing one source is far more useful
            # to the caller than no timeline, and the failure is logged.
            try:
                events = provider.get_timeline_events(
                    req, start, stop, filters or self._all_filter_names(req))
            except Exception:
                self._log_provider_failure(provider, 'get_timeline_events')
                continue
            for event in events or []:
                try:
                    collected.append(self._render(context, provider, event))
                except Exception:
                    self._log_provider_failure(
                        provider, 'render_timeline_event')

        collected.sort(key=lambda item: item['date'], reverse=True)
        return collected[:limit]

    # Internal methods

    def _all_filter_names(self, req):
        return [name for name, _label in self.getFilters(req)]

    def _render(self, context, provider, event):
        """Turn one provider event tuple into a serializable struct.

        A provider may return either a 4-tuple or a 5-tuple whose last member
        names a DIFFERENT component to render with -- `AttachmentModule` does
        exactly this when it emits events on another realm's behalf. Ignoring
        the 5th member renders the event against the wrong component.
        """
        if len(event) == 5:
            kind, date, author, _data, provider = event
        else:
            kind, date, author, _data = event

        def field(name):
            value = provider.render_timeline_event(context, name, event)
            if value is None:
                return ''
            return plaintext(value, keeplinebreaks=False).strip()

        return {
            'kind': kind or '',
            'date': date,
            'author': author or '',
            'title': field('title'),
            'description': field('description'),
            'url': str(provider.render_timeline_event(context, 'url', event)
                       or ''),
        }

    def _log_provider_failure(self, provider, method):
        self.log.warning("timeline RPC: %s.%s failed, skipping",
                         provider.__class__.__name__, method, exc_info=True)
