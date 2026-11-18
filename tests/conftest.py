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

import os

import fixtures
import plex_stubs
import pytest


@pytest.fixture(name="load_agent_module")
def load_agent_module_fixture():
    """Return a loader that executes the plugin against fresh, isolated stubs."""
    with plex_stubs.running_agent() as load:
        yield load


@pytest.fixture(name="library")
def library_fixture(tmp_path):
    """An empty fake media library root under tmp_path."""
    return fixtures.FakeLibrary(tmp_path / "library")


@pytest.fixture(name="scenario")
def scenario_fixture(load_agent_module, library, monkeypatch):
    """A freshly loaded plugin wired to the fake library and a canned API.

    ``os.walk`` and ``os.listdir`` are replaced with counting spies, and the
    API key is forced to come from Prefs so a stray ``youtube-key.txt`` in the
    checkout cannot change a test's URLs.
    """
    loaded = load_agent_module()
    real_isfile = os.path.isfile

    def isfile(path):
        if str(path).endswith("youtube-key.txt"):
            return False
        return real_isfile(path)

    monkeypatch.setattr(os.path, "isfile", isfile)

    scenario = fixtures.Scenario(loaded, library)
    scenario.spies.install(monkeypatch)
    yield scenario
