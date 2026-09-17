# -*- coding: utf-8 -*-
"""Additive XML-RPC methods for Trac, shipped alongside XmlRpcPlugin.

This package does NOT modify or replace ``tracxmlrpc``. Trac's RPC
dispatcher (``tracrpc.api.XMLRPCSystem.get_method``) iterates every
``IXMLRPCHandler`` provider and matches on the full dotted method name, so a
second component may claim an existing namespace as long as it yields method
names the incumbent does not. ``tracrpc.ticket.TicketRPC`` never yields
``editComment``, ``deleteComment`` or ``getCommentHistory``, and nothing
yields a ``timeline`` namespace at all.

The practical consequence is that enabling or disabling this plugin cannot
break the stock RPC surface, and rollback is one ``trac-admin ... config set
components tracrpc_comment.* disabled`` plus a uWSGI reload.
"""

from tracrpc_comment.ticket_comment import TicketCommentRPC
from tracrpc_comment.timeline import TimelineRPC

__all__ = ['TicketCommentRPC', 'TimelineRPC']

__version__ = '1.0.0'
