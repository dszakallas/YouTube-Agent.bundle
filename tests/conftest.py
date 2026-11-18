"""Fixtures for the offline YouTube-Agent test suite.

``Contents/Code/__init__.py`` is Python 2.7 source that Plex executes with its
own framework API (Log, Prefs, Proxy, Regex, ...) injected as module globals.
The tests execute that file unchanged on CPython 3 with the framework stubbed and
Python 2 builtins aliased (``basestring = unicode = str``).
Fidelity gap: Python 2-only idioms the plugin relies on, such as
``media.seasons.keys()[0]``, are reproduced by the test doubles (``Py2Dict``
returns lists from ``keys()``), not by editing the plugin. The real Python 2.7
syntax gate is the ``py27-compat`` git hook, which compiles ``Contents/`` with
pypy27.
"""

import plex_stubs
import pytest


@pytest.fixture(name="load_agent_module")
def load_agent_module_fixture():
    """Return a loader that executes the plugin against fresh, isolated stubs."""
    with plex_stubs.running_agent() as load:
        yield load
