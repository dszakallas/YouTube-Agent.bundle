"""Baseline smoke tests for Contents/Code/__init__.py.

These lock in the behaviour the dev environment depends on: the plugin imports
offline, its pure helpers return real values, its regular expressions extract the
YouTube ids it is documented to extract, and Search() still appends a
MetadataSearchResult for a movie whose filename carries a video id.
"""

import os

import plex_stubs

VIDEO_ID = "dQw4w9WgXcQ"
PLAYLIST_ID = "PL0123456789abcdef"
CHANNEL_ID = "UC0123456789abcdefghijkl"


def test_module_executes_fully_offline(load_agent_module):
    loaded = load_agent_module()
    module, stubs = loaded.module, loaded.stubs

    # Exactly one network attempt: the localhost Plex library lookup, which the
    # urllib2 stub refuses. Nothing else may reach out.
    assert stubs.urlopen_calls == ["http://127.0.0.1:32400/library/sections/"]
    assert stubs.urlopen_calls == [module.PLEX_LIBRARY_URL]
    assert module.PLEX_LIBRARY == {}
    assert stubs.json.url_calls == []
    assert stubs.http.requests == []
    assert stubs.data.loaded == []

    # The plugin locates itself from its real path, so no temp copy is needed.
    assert module.PluginDir == str(plex_stubs.REPO_ROOT)
    assert module.PlexRoot == os.path.abspath(os.path.join(str(plex_stubs.REPO_ROOT), "..", ".."))
    assert module.CachePath == os.path.join(
        module.PlexRoot,
        "Plug-in Support",
        "Data",
        "com.plexapp.agents.hama",
        "DataItems",
    )

    # No Plex token file is visible, so the token lookup is skipped and the
    # failure to read the library is logged verbatim.
    token_file = os.path.join(module.PlexRoot, "X-Plex-Token.id")
    assert stubs.isfile_calls == [token_file]
    assert stubs.log.messages == [
        "Library: " + module.PlexRoot,
        (
            "Place correct Plex token in {} file or in PLEX_LIBRARY_URL variable in "
            "Code/__init__.py to have a log per library - "
            "https://support.plex.tv/hc/en-us/articles/204059436-Finding-your-account-token"
            "-X-Plex-Token, Error: {}"
        ).format(token_file, plex_stubs.OFFLINE_ERROR),
    ]


def test_module_globals_are_declared(load_agent_module):
    module = load_agent_module().module

    assert module.YOUTUBE_API_BASE_URL == "https://www.googleapis.com/youtube/v3/"
    assert module.YOUTUBE_CATEGORY_ID["10"] == "Music"
    assert module.YOUTUBE_CATEGORY_ID["27"] == "Education"
    assert module.PLEX_LIBRARY_URL == "http://127.0.0.1:32400/library/sections/"
    assert module.PlexRoot != module.PluginDir


def test_dict_reads_nested_values_and_defaults(load_agent_module):
    module = load_agent_module().module
    payload = {
        "nested": {"field": 1},
        "items": ["zero", "one"],
        "nothing": None,
        "not_available": "N/A",
        "empty": "",
        "zero": 0,
    }

    assert module.Dict(payload, "nested", "field") == 1
    assert module.Dict(payload, "items", 1) == "one"
    assert module.Dict(payload, "zero") == 0

    assert module.Dict(payload, "missing") == ""
    assert module.Dict(payload, "nested", "missing") == ""
    assert module.Dict(payload, "items", 5) == ""
    assert module.Dict(payload, "items", "1") == ""

    assert module.Dict(payload, "missing", default=7) == 7
    assert module.Dict(payload, "nothing", default=7) == 7
    assert module.Dict(payload, "not_available", default=7) == 7
    assert module.Dict(payload, "empty", default=7) == 7
    assert module.Dict(payload, "nested", "field", default=7) == 1


def test_iso8601_duration_to_seconds(load_agent_module):
    convert = load_agent_module().module.ISO8601DurationToSeconds

    assert convert("PT1H2M3S") == 3723
    assert convert("PT1H") == 3600
    assert convert("PT2M") == 120
    assert convert("PT45S") == 45
    assert convert("PT") == 0
    assert convert("not-a-duration") == 0


def test_natural_sort_key_orders_numerically(load_agent_module):
    key = load_agent_module().module.natural_sort_key

    assert key("Season 12") == ["season ", 12, ""]
    assert sorted(["Episode 10", "Episode 2", "episode 1"], key=key) == [
        "episode 1",
        "Episode 2",
        "Episode 10",
    ]


def test_small_helpers(load_agent_module):
    module = load_agent_module().module

    assert module.js_int("123abc45") == 12345
    assert module.js_int(None) == 0
    assert module.js_int("") == 0

    assert module.titlecase("hello world of youtube") == "Hello World Of Youtube"
    assert module.titlecase("") == ""


def test_sanitize_xml_string(load_agent_module):
    sanitize = load_agent_module().module.sanitize_xml_string

    assert sanitize(None) == ""
    assert sanitize("plain text") == "plain text"
    assert sanitize("a\x01b\x1fc") == "abc"
    assert sanitize("a\u2028b\u2029c") == "abc"
    assert sanitize("\ufffe\uffffok") == "ok"
    assert sanitize(42) == "42"


def test_youtube_api_key_falls_back_to_prefs(load_agent_module, monkeypatch):
    loaded = load_agent_module()
    monkeypatch.setattr(os.path, "isfile", lambda path: False)

    assert loaded.module.youtube_api_key() == "pref-youtube-api-key"
    assert loaded.stubs.data.loaded == []


def test_youtube_api_key_prefers_local_key_file(load_agent_module, monkeypatch):
    loaded = load_agent_module()
    key_file = os.path.join(loaded.module.PluginDir, "youtube-key.txt")
    monkeypatch.setattr(os.path, "isfile", lambda path: path == key_file)
    loaded.stubs.data.files[key_file] = "  local-api-key\n"

    assert loaded.module.youtube_api_key() == "local-api-key"
    assert loaded.stubs.data.loaded == [key_file]


def test_video_regex_extracts_id(load_agent_module):
    regex = load_agent_module().module.YOUTUBE_VIDEO_REGEX

    assert regex.search("Holiday [{0}].mp4".format(VIDEO_ID)).group("id") == VIDEO_ID
    assert regex.search("[youtube-{0}] Holiday.mp4".format(VIDEO_ID)).group("id") == VIDEO_ID
    assert regex.search("20140101_{0}_holiday.mp4".format(VIDEO_ID)).group("id") == VIDEO_ID
    assert regex.search("holiday.mp4") is None


def test_playlist_regex_extracts_id(load_agent_module):
    regex = load_agent_module().module.YOUTUBE_PLAYLIST_REGEX

    assert regex.search("[{0}] Series".format(PLAYLIST_ID)).group("id") == PLAYLIST_ID
    uploads_id = "UU0123456789abcdefghijkl"
    assert regex.search("[youtube3-{0}] Uploads".format(uploads_id)).group("id") == uploads_id
    assert regex.search("plain folder") is None


def test_channel_regex_extracts_id(load_agent_module):
    regex = load_agent_module().module.YOUTUBE_CHANNEL_REGEX

    assert regex.search("[{0}] Channel".format(CHANNEL_ID)).group("id") == CHANNEL_ID
    assert regex.search("[youtube-{0}] Channel".format(CHANNEL_ID)).group("id") == CHANNEL_ID
    assert regex.search("plain folder") is None


def test_search_movie_with_video_id_in_filename(load_agent_module):
    loaded = load_agent_module()
    module, stubs = loaded.module, loaded.stubs
    media = plex_stubs.movie_media(
        name="Holiday Clip",
        file="/library/movies/Holiday Clip [{0}].mp4".format(VIDEO_ID),
    )
    results = plex_stubs.SearchResults()

    module.Search(results, media, "en", False, True)

    assert len(results.items) == 1
    result = results.items[0]
    assert result.id == "youtube|{0}|movies".format(VIDEO_ID)
    assert result.name == "Holiday Clip"
    assert result.year is None
    assert result.score == 100
    assert result.lang == "en"

    assert 'search() - YouTube ID found - regex: VIDEO, youtube ID: "{0}"'.format(VIDEO_ID) in (
        stubs.log.messages
    )
    assert stubs.json.url_calls == []


def test_movie_agent_search_uses_movie_mode(load_agent_module):
    module = load_agent_module().module
    media = plex_stubs.movie_media(
        name="Holiday Clip",
        file="/library/movies/Holiday Clip [{0}].mp4".format(VIDEO_ID),
    )
    results = plex_stubs.SearchResults()

    module.YouTubeMovieAgent().search(results, media, "en", False)

    assert [result.id for result in results.items] == ["youtube|{0}|movies".format(VIDEO_ID)]


def test_agent_declarations(load_agent_module):
    module = load_agent_module().module

    assert module.YouTubeSeriesAgent.name == "YouTubeSeries"
    assert module.YouTubeMovieAgent.name == "YouTubeMovie"
    assert issubclass(module.YouTubeSeriesAgent, plex_stubs.Agent.TV_Shows)
    assert issubclass(module.YouTubeMovieAgent, plex_stubs.Agent.Movies)
    assert module.YouTubeSeriesAgent.primary_provider is True
    assert module.YouTubeMovieAgent.primary_provider is True
    assert module.YouTubeSeriesAgent.accepts_from == ["com.plexapp.agents.localmedia"]
    assert module.YouTubeMovieAgent.accepts_from == ["com.plexapp.agents.localmedia"]
