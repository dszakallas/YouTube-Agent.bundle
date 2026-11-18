"""Regression tests for the shared-state fixes (D3, D4, D5).

DeArrow used to mutate the process-global ``HTTP.CacheTime`` and was also called
with playlist and channel ids, Search() crashed on a ``youtube.id`` folder whose
parent has no playlist id and on media without seasons, and the import-time Plex
library lookup had no timeout. These tests pin the fixed behaviour.
"""

import hashlib

import fixtures
import plex_stubs

CHANNEL_ID = "UC0123456789abcdefghijkl"
PLAYLIST_ID = "PL0123456789abcdef"
API_KEY = "pref-youtube-api-key"
API = "https://www.googleapis.com/youtube/v3/"

CHANNELS_URL = (
    API + "channels?part=snippet%2CcontentDetails%2Cstatistics%2CbrandingSettings&id={}&key={}"
)
CHANNEL_ITEMS_URL = (
    API + "search?order=date&part=snippet&type=video&maxResults=50&channelId={}&key={}"
)
VIDEOS_URL = API + "videos?part=snippet,contentDetails,statistics&id={}&key={}"
PLAYLIST_DETAILS_URL = API + "playlists?part=snippet,contentDetails&id={}&key={}"
PLAYLIST_ITEMS_URL = (
    API + "playlistItems?part=snippet,contentDetails&maxResults=50&playlistId={}&key={}"
)

VIDEO_ID = "ddddddddddd"

# Arbitrary sentinel: any value the plugin writes over it is a regression.
CACHE_TIME_SENTINEL = 12345


def dearrow_url(video_id):
    digest = hashlib.sha256(video_id.encode("ascii")).hexdigest()
    return fixtures.DEARROW_API + "/api/branding/" + digest[:4]


def dearrow_requests(stubs):
    return [url for url in stubs.json.calls.args() if url.startswith(fixtures.DEARROW_API)]


def movie_media(name, file):
    return plex_stubs.movie_media(name=name, file=file)


def test_dearrow_leaves_http_cache_time_unchanged(scenario):
    scenario.api.add_dearrow(VIDEO_ID, fixtures.dearrow_body(VIDEO_ID, "Crowd Title"))
    scenario.stubs.http.CacheTime = CACHE_TIME_SENTINEL

    title = scenario.module.DeArrow(VIDEO_ID)

    assert title == "Crowd Title"
    assert scenario.stubs.http.CacheTime == CACHE_TIME_SENTINEL
    assert scenario.stubs.json.calls.entries == [
        ((dearrow_url(VIDEO_ID),), {"cacheTime": 0}),
    ]
    assert scenario.stubs.log.errors == []


def test_movie_update_uses_dearrow_without_mutating_cache_time(scenario):
    scenario.stubs.prefs["use_crowd_sourced_titles"] = True
    scenario.stubs.http.CacheTime = CACHE_TIME_SENTINEL
    scenario.api.add_video(
        VIDEO_ID,
        fixtures.video_body(VIDEO_ID, title="Movie Title", description="Movie summary"),
    )
    scenario.api.add_dearrow(VIDEO_ID, fixtures.dearrow_body(VIDEO_ID, "Crowd Title"))
    media = movie_media("Movie", str(scenario.library.root / "movies" / "Movie.mp4"))
    metadata = scenario.metadata(VIDEO_ID)

    scenario.update(metadata, media, movie=True)

    assert metadata.title == "Crowd Title"
    assert metadata.original_title == "Movie Title"
    assert metadata.summary == "Original Title: Movie Title\r\n\r\nMovie summary"
    assert scenario.stubs.http.CacheTime == CACHE_TIME_SENTINEL
    assert scenario.stubs.json.calls.entries == [
        ((VIDEOS_URL.format(VIDEO_ID, API_KEY),), {}),
        ((dearrow_url(VIDEO_ID),), {"cacheTime": 0}),
    ]
    assert scenario.stubs.log.errors == []


def test_channel_update_with_crowd_titles_makes_no_dearrow_request(scenario):
    scenario.stubs.prefs["use_crowd_sourced_titles"] = True
    scenario.api.add_channel_details(
        CHANNEL_ID,
        fixtures.channel_body(CHANNEL_ID, title="Test Channel", description="Channel description"),
    )
    scenario.api.add_channel_videos(CHANNEL_ID, [])

    series = scenario.library.add_series("Test Channel", layout="flat")
    series.add_episode(
        "Only Video",
        "fffffffffff",
        info_json=fixtures.youtube_info("fffffffffff", "Only Video"),
    )
    scenario.register_library()
    metadata = scenario.metadata(CHANNEL_ID, series)

    scenario.update(metadata, series.media())

    assert metadata.title == "Test Channel"
    assert metadata.original_title == ""
    assert dearrow_requests(scenario.stubs) == []
    assert scenario.stubs.json.calls.args() == (
        CHANNELS_URL.format(CHANNEL_ID, API_KEY),
        CHANNEL_ITEMS_URL.format(CHANNEL_ID, API_KEY),
    )
    assert scenario.stubs.log.errors == []


def test_playlist_update_with_crowd_titles_makes_no_dearrow_request(scenario):
    scenario.stubs.prefs["use_crowd_sourced_titles"] = True
    scenario.api.add_playlist_details(
        PLAYLIST_ID,
        fixtures.playlist_body(
            PLAYLIST_ID,
            title="Playlist Title",
            description="Playlist summary",
            channel_id=CHANNEL_ID,
        ),
    )
    scenario.api.add_playlist_videos(
        PLAYLIST_ID,
        [fixtures.playlist_item("eeeeeeeeeee", title="From Playlist")],
    )
    scenario.api.add_channel_details(
        CHANNEL_ID, fixtures.channel_body(CHANNEL_ID, title="Test Channel")
    )
    scenario.api.add_channel_videos(CHANNEL_ID, [])

    series = scenario.library.add_series("Playlist Channel", layout="seasons")
    series.add_episode("Playlist Episode", "eeeeeeeeeee", season=1)
    scenario.register_library()
    metadata = scenario.metadata(PLAYLIST_ID, series)

    scenario.update(metadata, series.media())

    assert metadata.title == "Playlist Title"
    assert metadata.original_title == ""
    assert dearrow_requests(scenario.stubs) == []
    assert scenario.stubs.json.calls.args() == (
        PLAYLIST_DETAILS_URL.format(PLAYLIST_ID, API_KEY),
        PLAYLIST_ITEMS_URL.format(PLAYLIST_ID, API_KEY),
        CHANNELS_URL.format(CHANNEL_ID, API_KEY),
        CHANNEL_ITEMS_URL.format(CHANNEL_ID, API_KEY),
    )
    assert scenario.stubs.log.errors == []


def test_search_with_youtube_id_and_no_playlist_in_folder_name(scenario):
    series = scenario.library.add_series("Plain Show", layout="seasons")
    season_folder = series.directory / "Season 01"
    season_folder.mkdir(parents=True, exist_ok=True)
    (season_folder / "youtube.id").write_text(CHANNEL_ID, encoding="utf-8")
    episode_file = season_folder / "Plain Episode.mp4"
    episode_file.write_bytes(fixtures.VIDEO_BYTES)
    media = plex_stubs.tv_media("Plain Show", str(episode_file), {"1": {"e001": str(episode_file)}})
    scenario.register_library()

    results = scenario.search(media)

    assert len(results.items) == 1
    result = results.items[0]
    assert result.id == "youtube||{}".format(season_folder)
    assert result.name == "Plain Episode.mp4"
    assert result.year is None
    assert result.score == 100
    assert result.lang == "en"
    assert scenario.stubs.json.calls.count == 0


def test_search_with_no_seasons_falls_back_to_library_root(scenario):
    series = scenario.library.add_series("Empty Show", layout="flat")
    episode_file = series.directory / "Empty Show.mp4"
    episode_file.write_bytes(fixtures.VIDEO_BYTES)
    media = plex_stubs.tv_media("Empty Show", str(episode_file), {})
    scenario.register_library()

    results = scenario.search(media)

    assert len(results.items) == 1
    result = results.items[0]
    assert result.id == "youtube||{}".format(series.directory)
    assert result.name == "Empty Show.mp4"
    assert result.year is None
    assert result.score == 80
    assert result.lang == "en"
    # One video-title lookup, which the offline stub refuses; Search() catches it.
    assert scenario.stubs.json.calls.count == 1


def test_module_import_sets_urlopen_timeout(load_agent_module):
    loaded = load_agent_module()

    assert loaded.stubs.urlopen_calls == [plex_stubs.PLEX_LIBRARY_URL]
    assert loaded.stubs.urlopen_kwargs == [{"timeout": 10}]
