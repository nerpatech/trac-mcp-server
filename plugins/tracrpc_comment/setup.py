# -*- coding: utf-8 -*-
"""Packaging for the tracrpc_comment Trac plugin.

Installed into the TRAC server's virtualenv (on the Trac host), NOT into the
trac-mcp-server venv on the workstation -- the two run on different machines.
See plugins/README.md.
"""

from setuptools import setup

setup(
    name='TracRpcComment',
    version='1.0.0',
    author='Oleg Mazurov',
    author_email='admin@nerpa.tech',
    description='Ticket comment edit/delete/history and timeline access '
                'over Trac XML-RPC.',
    license='MIT',
    packages=['tracrpc_comment'],
    # Declares the Trac components this egg provides. Trac reads this to
    # discover them; they still have to be enabled per environment with
    #   trac-admin <env> config set components tracrpc_comment.* enabled
    entry_points={
        'trac.plugins': [
            'tracrpc_comment.ticket_comment = '
            'tracrpc_comment.ticket_comment',
            'tracrpc_comment.timeline = tracrpc_comment.timeline',
        ],
    },
    # Not declared as an install_requires: TracXMLRPC is a plugin of the Trac
    # environment this installs into, not a PyPI dependency this egg should
    # be allowed to resolve and install on its own.
    install_requires=[],
)
