"""Offline stand-ins for the Plex Framework 2.x API.

``Contents/Code/__init__.py`` runs inside Plex, where the framework injects
``Log``, ``Prefs``, ``Core``, ``Proxy``, ``Regex`` and friends into the plugin's
module globals. This module rebuilds that surface so pytest can execute the real
plugin file without a Plex server, without touching ``Plug-in Support`` and
without any network access.

Every stub records what the plugin did to it (log lines, appended search
results, loaded files, HTTP requests) so tests can assert on observed values
instead of "it did not raise".
"""

import builtins
import contextlib
import datetime
import importlib.util
import json
import os
import re
import sys
import types
import urllib.parse
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
AGENT_PATH = REPO_ROOT / "Contents" / "Code" / "__init__.py"
MODULE_NAME = "plex_youtube_agent_under_test"

CACHE_1MONTH = 60 * 60 * 24 * 31
OFFLINE_ERROR = "offline test stub: network access is disabled"
PLEX_LIBRARY_URL = "http://127.0.0.1:32400/library/sections/"

_MISSING = object()


# ---------------------------------------------------------------------------
# Plex framework API
# ---------------------------------------------------------------------------


class Log(object):
    """Callable logger with the level helpers the plugin uses."""

    LEVELS = ("Debug", "Info", "Warn", "Error", "Critical")

    def __init__(self):
        self.entries = []

    def __call__(self, message, *args):
        text = message % args if args else message
        self.entries.append(("Log", text))
        return text

    def _record(self, level, message, *args):
        text = message % args if args else message
        self.entries.append((level, text))
        return text

    @property
    def messages(self):
        return [text for _, text in self.entries]

    @property
    def infos(self):
        return [text for level, text in self.entries if level == "Info"]

    @property
    def errors(self):
        return [text for level, text in self.entries if level == "Error"]


for _level in Log.LEVELS:

    def _make_helper(level):
        def helper(self, message, *args):
            return self._record(level, message, *args)

        helper.__name__ = level.lower()
        return helper

    setattr(Log, _level, _make_helper(_level))


class Prefs(dict):
    """Library preferences. Values default to the plugin's expected types."""

    DEFAULTS = {
        "YouTube-Agent_youtube_api_key": "pref-youtube-api-key",
        "use_crowd_sourced_titles": False,
        "add_user_as_director": False,
        "media_poster_source": "Channel",
    }

    def __init__(self, **overrides):
        super().__init__(self.DEFAULTS)
        self.update(overrides)


class FileStore(object):
    """Shared stand-in for ``Data.Load`` and ``Core.storage.load``."""

    def __init__(self, files=None):
        self.files = {} if files is None else files
        self.loaded = []

    def load(self, path):
        self.loaded.append(path)
        try:
            return self.files[path]
        except KeyError:
            raise IOError("offline test stub: no such file: %s" % (path,))

    Load = load


class CoreStorage(object):
    def __init__(self, files):
        self.files = files

    def load(self, path):
        return self.files.load(path)


class Core(object):
    def __init__(self, files):
        self.storage = CoreStorage(files)


class HTTPRequest(object):
    def __init__(self, url, content):
        self.url = url
        self.content = content


class HTTP(object):
    def __init__(self):
        self.CacheTime = CACHE_1MONTH
        self.Headers = {}
        self.requests = []
        self.responses = {}
        self.clear_cache_calls = 0

    def Request(self, url, **kwargs):
        self.requests.append(url)
        content = self.responses.get(url, b"stub-thumbnail:" + url.encode("utf-8"))
        return HTTPRequest(url, content)

    def ClearCache(self):
        self.clear_cache_calls += 1


class JSON(object):
    """Records API calls and refuses to reach the network unless pre-seeded."""

    def __init__(self):
        self.url_calls = []
        self.responses = {}
        self.string_calls = []

    def ObjectFromURL(self, url):
        self.url_calls.append(url)
        try:
            return self.responses[url]
        except KeyError:
            raise RuntimeError("%s (url: %s)" % (OFFLINE_ERROR, url))

    def ObjectFromString(self, data):
        self.string_calls.append(data)
        return json.loads(data)


class Datetime(object):
    @staticmethod
    def ParseDate(value):
        return datetime.datetime.strptime(str(value), "%Y%m%d")


class String(object):
    @staticmethod
    def Quote(value, usePlus=True):
        if usePlus:
            return urllib.parse.quote_plus(value)
        return urllib.parse.quote(value)


class Regex(object):
    """Plex's Regex object: a thin wrapper around a compiled pattern."""

    IGNORECASE = re.IGNORECASE
    DOTALL = re.DOTALL
    MULTILINE = re.MULTILINE
    UNICODE = re.UNICODE

    def __init__(self, pattern, flags=0):
        self.pattern = pattern
        self._compiled = re.compile(pattern, flags)

    def search(self, string, *args, **kwargs):
        return self._compiled.search(string, *args, **kwargs)

    def match(self, string, *args, **kwargs):
        return self._compiled.match(string, *args, **kwargs)

    def findall(self, string, *args, **kwargs):
        return self._compiled.findall(string, *args, **kwargs)

    def finditer(self, string, *args, **kwargs):
        return self._compiled.finditer(string, *args, **kwargs)

    def sub(self, replacement, string, *args, **kwargs):
        return self._compiled.sub(replacement, string, *args, **kwargs)


class ProxyMedia(object):
    def __init__(self, data, sort_order=None):
        self.data = data
        self.sort_order = sort_order


class Proxy(object):
    def __init__(self):
        self.media = []

    def Media(self, data, sort_order=None):
        media = ProxyMedia(data, sort_order)
        self.media.append(media)
        return media


class MetadataSearchResult(object):
    def __init__(self, id=None, name=None, year=None, score=None, lang=None, thumb=None):
        self.id = id
        self.name = name
        self.year = year
        self.score = score
        self.lang = lang
        self.thumb = thumb

    def __repr__(self):
        return "MetadataSearchResult(id=%r, name=%r, year=%r, score=%r, lang=%r)" % (
            self.id,
            self.name,
            self.year,
            self.score,
            self.lang,
        )


class SearchResults(object):
    """The ``results`` object handed to Agent.search()."""

    def __init__(self):
        self.items = []

    def Append(self, result):
        self.items.append(result)


class Locale(object):
    class Language(object):
        NoLanguage = "en"


class _AgentBase(object):
    name = None


class Agent(object):
    class TV_Shows(_AgentBase):
        pass

    class Movies(_AgentBase):
        pass


# ---------------------------------------------------------------------------
# Media / metadata object graph
# ---------------------------------------------------------------------------


class Py2Dict(dict):
    """dict whose keys()/values()/items() return lists, as in Python 2.

    The plugin is Python 2 source and indexes those results, e.g.
    ``media.seasons.keys()[0]``. Tests keep the plugin unchanged and reproduce
    Python 2 semantics here instead.
    """

    def keys(self):
        return list(super().keys())

    def values(self):
        return list(super().values())

    def items(self):
        return list(super().items())

    def validate_keys(self, keys):
        """Drop keys that are not listed, mirroring Plex's container pruning."""
        for key in list(self):
            if key not in keys:
                del self[key]


class Py2AutoDict(Py2Dict):
    """Py2Dict that creates missing entries, like Plex's metadata containers."""

    def __init__(self, factory):
        super().__init__()
        self._factory = factory

    def __missing__(self, key):
        value = self._factory()
        self[key] = value
        return value


class MediaPart(object):
    def __init__(self, file):
        self.file = file


class MediaItem(object):
    def __init__(self, parts):
        self.parts = list(parts)


class Episode(object):
    def __init__(self, file):
        self.items = [MediaItem([MediaPart(file)])]
        self.thumbs = Py2Dict()
        self.directors = People()
        self.title = ""
        self.summary = ""
        self.duration = 0
        self.rating = 0.0
        self.originally_available_at = None


class Season(object):
    def __init__(self, episodes=None):
        self.episodes = Py2AutoDict(lambda: Episode(""))
        self.episodes.update(episodes or {})


class MediaContainer(object):
    """Movie-ish or show-ish media object as produced by Plex scanners."""

    def __init__(self, name=None, filename=None, file=None, show=None, seasons=None):
        self.name = name
        self.filename = filename
        self.show = show
        self.items = [MediaItem([MediaPart(file)])] if file is not None else []
        self.seasons = Py2AutoDict(Season)
        self.seasons.update(seasons or {})


class Person(object):
    def __init__(self):
        self.name = None
        self.role = None
        self.photo = None


class People(object):
    def __init__(self):
        self.items = []

    def new(self):
        person = Person()
        self.items.append(person)
        return person

    def clear(self):
        self.items = []

    def __iter__(self):
        return iter(self.items)

    def __len__(self):
        return len(self.items)


class MetadataEpisode(object):
    def __init__(self):
        self.title = ""
        self.summary = ""
        self.duration = 0
        self.rating = 0.0
        self.originally_available_at = None
        self.thumbs = Py2Dict()
        self.directors = People()


class MetadataSeason(object):
    def __init__(self):
        self.episodes = Py2AutoDict(MetadataEpisode)


class Metadata(object):
    """The metadata object handed to Agent.update()."""

    def __init__(self):
        self.id = ""
        self.title = ""
        self.original_title = ""
        self.summary = ""
        self.studio = ""
        self.year = None
        self.duration = 0
        self.rating = 0.0
        self.originally_available_at = None
        self.posters = Py2Dict()
        self.art = Py2Dict()
        self.banners = Py2Dict()
        self.thumbs = Py2Dict()
        self.seasons = Py2AutoDict(MetadataSeason)
        self.roles = People()
        self.directors = People()
        self.countries = set()
        self.genres = set()
        self.collections = []


def movie_media(name, file):
    """Media container for a movie library item."""
    return MediaContainer(name=name, file=file)


def tv_media(show, filename, seasons):
    """Media container for a TV library item.

    ``seasons`` maps season numbers to ``{episode number: absolute file path}``.
    """
    season_objects = {}
    for season_number, episodes in seasons.items():
        season_objects[season_number] = Season(
            {episode_number: Episode(file) for episode_number, file in episodes.items()}
        )
    return MediaContainer(show=show, filename=filename, seasons=season_objects)


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------


class LoadedAgent(object):
    """A freshly executed plugin module plus the stubs it ran against."""

    def __init__(self, module, stubs):
        self.module = module
        self.stubs = stubs


class Stubs(object):
    """One isolated set of Plex framework doubles."""

    def __init__(self, **prefs):
        self.log = Log()
        self.prefs = Prefs(**prefs)
        self.files = FileStore()
        self.data = self.files
        self.core = Core(self.files)
        self.http = HTTP()
        self.json = JSON()
        self.datetime = Datetime()
        self.string = String()
        self.regex = Regex
        self.proxy = Proxy()
        self.results = SearchResults()
        self.metadata = Metadata()
        self.urlopen_calls = []
        self.isfile_calls = []
        self.urllib2 = self._build_urllib2()

    def _build_urllib2(self):
        stubs = self
        module = types.ModuleType("urllib2")
        module.unquote = urllib.parse.unquote
        module.quote = urllib.parse.quote
        module.urlencode = urllib.parse.urlencode

        def urlopen(url, *args, **kwargs):
            stubs.urlopen_calls.append(url)
            raise RuntimeError(OFFLINE_ERROR)

        module.urlopen = urlopen
        return module

    def isfile(self, path):
        """Pretend no Plex support files exist on the host."""
        self.isfile_calls.append(path)
        return False

    def framework_globals(self):
        return {
            "Agent": Agent,
            "CACHE_1MONTH": CACHE_1MONTH,
            "Core": self.core,
            "Data": self.data,
            "Datetime": self.datetime,
            "HTTP": self.http,
            "JSON": self.json,
            "Locale": Locale,
            "Log": self.log,
            "MetadataSearchResult": MetadataSearchResult,
            "Prefs": self.prefs,
            "Proxy": self.proxy,
            "Regex": self.regex,
            "String": self.string,
        }


def load_agent_module(stubs):
    """Execute Contents/Code/__init__.py against ``stubs`` and return the module."""
    spec = importlib.util.spec_from_file_location(MODULE_NAME, str(AGENT_PATH))
    module = importlib.util.module_from_spec(spec)
    module.__dict__.update(stubs.framework_globals())

    previous_isfile = os.path.isfile
    previous_dont_write_bytecode = sys.dont_write_bytecode
    os.path.isfile = stubs.isfile
    # Never leave a CPython 3 __pycache__ inside the plugin bundle.
    sys.dont_write_bytecode = True
    sys.modules[MODULE_NAME] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(MODULE_NAME, None)
        raise
    finally:
        os.path.isfile = previous_isfile
        sys.dont_write_bytecode = previous_dont_write_bytecode
    return LoadedAgent(module, stubs)


@contextlib.contextmanager
def running_agent():
    """Install the Python 2 runtime shims for the duration of a test.

    Yields a loader (``load_agent_module`` bound to fresh stubs) so the plugin
    can be executed more than once per test if needed. ``sys.modules``,
    ``builtins`` and the urllib2 stub are always restored afterwards.
    """
    stubs = Stubs()
    saved_urllib2 = sys.modules.get("urllib2", _MISSING)
    saved_builtins = {name: getattr(builtins, name, _MISSING) for name in ("basestring", "unicode")}

    sys.modules["urllib2"] = stubs.urllib2
    builtins.basestring = str
    builtins.unicode = str
    try:
        yield lambda: load_agent_module(stubs)
    finally:
        if saved_urllib2 is _MISSING:
            sys.modules.pop("urllib2", None)
        else:
            sys.modules["urllib2"] = saved_urllib2
        for name, value in saved_builtins.items():
            if value is _MISSING:
                delattr(builtins, name)
            else:
                setattr(builtins, name, value)
        sys.modules.pop(MODULE_NAME, None)
