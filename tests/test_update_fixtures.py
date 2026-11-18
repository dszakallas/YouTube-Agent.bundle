"""Smoke tests that prove the update-path fixtures can drive the plugin offline.

Each test builds a real library under tmp_path, wires the PLEX_LIBRARY entry,
serves canned YouTube payloads and asserts exact metadata values plus exact
call counts. No network, no Plex support directory, no plugin changes.
"""

import datetime
import hashlib
import threading

import fixtures
import plex_stubs

CHANNEL_ID = "UC0123456789abcdefghijkl"
PLAYLIST_ID = "PL0123456789abcdef"
API_KEY = "pref-youtube-api-key"
API = "https://www.googleapis.com/youtube/v3/"

# Mirrors of the URL templates in Contents/Code/__init__.py: if a template
# changes, the URL assertions below fail on purpose.
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

BANNER = "https://yt3.ggpht.com/banner-low"
POSTER = "https://yt3.ggpht.com/poster-medium"
EPISODE_THUMB = "https://i.ytimg.com/vi/aaaaaaaaaaa/mqdefault.jpg"
PLAYLIST_THUMB = "https://i.ytimg.com/vi/eeeeeeeeeee/mqdefault.jpg"


def local_thumbnail(episode, extension):
    """Path where the builder wrote a local thumbnail for an episode."""
    return str(episode.thumbnail_paths[extension])


def dearrow_url(video_id):
    digest = hashlib.sha256(video_id.encode("ascii")).hexdigest()
    return fixtures.DEARROW_API + "/api/branding/" + digest[:4]


def test_update_channel_series_offline(scenario):
    scenario.api.add_channel_details(
        CHANNEL_ID,
        fixtures.channel_body(
            CHANNEL_ID,
            title="Test Channel",
            country="US",
            banner=BANNER,
            poster=POSTER,
            statistics={"videoCount": "42", "subscriberCount": "1234", "viewCount": "99999"},
        ),
    )
    scenario.api.add_channel_videos(
        CHANNEL_ID,
        [
            fixtures.search_video(
                "aaaaaaaaaaa",
                title="First Video (API)",
                description="First summary from the API",
                published_at="2024-01-15T12:34:56Z",
                thumbnail=EPISODE_THUMB,
            ),
            fixtures.search_video("zzzzzzzzzzz", title="Unrelated Upload"),
            fixtures.search_video("yyyyyyyyyyy", title="Another Unrelated Upload"),
        ],
        page_size=2,
    )

    series = scenario.library.add_series("Test Channel", layout="seasons")
    series.add_episode("First Video", "aaaaaaaaaaa", season=1)
    second = series.add_episode(
        "Second Video",
        "bbbbbbbbbbb",
        season=1,
        info_json=fixtures.youtube_info(
            "bbbbbbbbbbb",
            "Second Video",
            description="Second summary",
            upload_date="20240116",
            duration=754,
            categories=["Music"],
            tags=["tag-a"],
            like_count=100,
            dislike_count=25,
        ),
        thumbnails=("jpg",),
    )
    third = series.add_episode(
        "Third Video",
        "ccccccccccc",
        season=1,
        info_json=fixtures.youtube_info(
            "ccccccccccc",
            "Third Video",
            description="Third summary",
            upload_date="20240117",
            duration=1234,
            categories=["Music"],
            tags=["tag-a"],
        ),
        thumbnails=("png",),
    )

    root = scenario.register_library()
    metadata = scenario.metadata(CHANNEL_ID, series)
    media = series.media()

    scenario.update(metadata, media)
    stubs = scenario.stubs

    # Three API calls (details plus two paged uploads), four image downloads,
    # four file reads, one walk per episode that needs its .info.json.
    assert scenario.call_counts() == {
        "json": 3,
        "http": 4,
        "storage": 4,
        "os.walk": 2,
        "os.listdir": 1,
    }
    assert stubs.json.calls.args() == (
        CHANNELS_URL.format(CHANNEL_ID, API_KEY),
        CHANNEL_ITEMS_URL.format(CHANNEL_ID, API_KEY),
        CHANNEL_ITEMS_URL.format(CHANNEL_ID, API_KEY) + "&pageToken=NEXT_PAGE_1",
    )
    assert stubs.http.calls.args() == (BANNER, BANNER, POSTER, EPISODE_THUMB)
    assert stubs.data.calls.args() == (
        str(second.info_json_path),
        local_thumbnail(second, "jpg"),
        str(third.info_json_path),
        local_thumbnail(third, "png"),
    )
    assert scenario.spies.walk.args() == (str(series.directory), str(series.directory))
    assert scenario.spies.listdir.args() == (str(series.directory),)

    # Series level metadata.
    assert root == str(scenario.library.root)
    assert metadata.studio == "YouTube"
    assert metadata.title == "Test Channel"
    assert metadata.summary == "Channel with 42 videos, 1234 subscribers, 99999 views"
    assert metadata.countries == {"US"}
    assert metadata.collections == []
    assert metadata.genres == {"Music", "tag-a"}

    assert metadata.art[BANNER].data == fixtures.image_bytes(BANNER)
    assert metadata.art[BANNER].sort_order == 1
    assert metadata.banners[BANNER].data == fixtures.image_bytes(BANNER)
    assert metadata.posters[POSTER].data == fixtures.image_bytes(POSTER)
    assert metadata.posters[POSTER].sort_order == 1
    assert [role.name for role in metadata.roles] == ["Test Channel"]
    assert metadata.roles.items[0].role == "Test Channel"
    assert metadata.roles.items[0].photo == POSTER
    assert metadata.directors.items == []

    # Episode matched through the channel uploads list.
    matched = metadata.seasons["1"].episodes["e001"]
    assert matched.title == "First Video (API)"
    assert matched.summary == "First summary from the API"
    assert matched.duration == 0
    assert matched.rating == 0.0
    assert matched.originally_available_at == datetime.date(2024, 1, 15)
    assert matched.thumbs[EPISODE_THUMB].data == fixtures.image_bytes(EPISODE_THUMB)

    # Episodes populated from their .info.json sidecars.
    from_json = metadata.seasons["1"].episodes["e002"]
    assert from_json.title == "Second Video"
    assert from_json.summary == "Second summary"
    assert from_json.duration == 754
    assert from_json.rating == 8.0
    assert from_json.originally_available_at == datetime.date(2024, 1, 16)
    assert from_json.thumbs[local_thumbnail(second, "jpg")].data == (
        fixtures.thumbnail_bytes("jpg")
    )

    third_from_json = metadata.seasons["1"].episodes["e003"]
    assert third_from_json.title == "Third Video"
    assert third_from_json.duration == 1234
    assert third_from_json.rating == 0.0
    assert third_from_json.originally_available_at == datetime.date(2024, 1, 17)
    assert third_from_json.thumbs[local_thumbnail(third, "png")].data == (
        fixtures.thumbnail_bytes("png")
    )


def test_update_episode_through_videos_endpoint(scenario):
    scenario.api.add_channel_details(
        CHANNEL_ID,
        fixtures.channel_body(CHANNEL_ID, title="Test Channel", description="Channel description"),
    )
    scenario.api.add_channel_videos(CHANNEL_ID, [])
    scenario.api.add_video(
        "ddddddddddd",
        fixtures.video_body(
            "ddddddddddd",
            title="Solo Video",
            description="Solo summary",
            published_at="2024-03-01T10:00:00Z",
            duration="PT12M34S",
            category_id="10",
            tags=["solo"],
        ),
    )

    series = scenario.library.add_series("Test Channel", layout="flat")
    series.add_episode("Solo", "ddddddddddd")

    scenario.register_library()
    metadata = scenario.metadata(CHANNEL_ID, series)

    scenario.update(metadata, series.media())
    stubs = scenario.stubs

    assert scenario.call_counts() == {
        "json": 3,
        "http": 1,
        "storage": 0,
        "os.walk": 1,
        "os.listdir": 1,
    }
    assert stubs.json.calls.args() == (
        CHANNELS_URL.format(CHANNEL_ID, API_KEY),
        CHANNEL_ITEMS_URL.format(CHANNEL_ID, API_KEY),
        VIDEOS_URL.format("ddddddddddd", API_KEY),
    )

    assert metadata.title == "Test Channel"
    assert metadata.summary == "Channel description"
    assert metadata.genres == {"Music", "solo"}

    episode = metadata.seasons["1"].episodes["e001"]
    assert episode.title == "Solo Video"
    assert episode.summary == "Solo summary"
    assert episode.duration == 754000
    assert episode.rating == 8.0
    assert episode.originally_available_at == datetime.date(2024, 3, 1)
    assert [director.name for director in episode.directors] == ["Test Channel"]

    thumb_url = "https://i.ytimg.com/vi/ddddddddddd/maxresdefault.jpg"
    assert episode.thumbs[thumb_url].data == fixtures.image_bytes(thumb_url)


def test_update_playlist_series_offline(scenario):
    scenario.api.add_playlist_details(
        PLAYLIST_ID,
        fixtures.playlist_body(
            PLAYLIST_ID,
            title="Playlist Title",
            description="Playlist summary",
            published_at="2024-02-01T08:00:00Z",
            channel_id=CHANNEL_ID,
        ),
    )
    scenario.api.add_playlist_videos(
        PLAYLIST_ID,
        [
            fixtures.playlist_item(
                "eeeeeeeeeee",
                title="From Playlist",
                description="Playlist episode summary",
                published_at="2024-02-02T09:00:00Z",
                thumbnail=PLAYLIST_THUMB,
            )
        ],
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
    stubs = scenario.stubs

    assert scenario.call_counts() == {
        "json": 4,
        "http": 2,
        "storage": 0,
        "os.walk": 0,
        "os.listdir": 1,
    }
    assert stubs.json.calls.args() == (
        PLAYLIST_DETAILS_URL.format(PLAYLIST_ID, API_KEY),
        PLAYLIST_ITEMS_URL.format(PLAYLIST_ID, API_KEY),
        CHANNELS_URL.format(CHANNEL_ID, API_KEY),
        CHANNEL_ITEMS_URL.format(CHANNEL_ID, API_KEY),
    )

    assert metadata.title == "Playlist Title"
    assert metadata.summary == "Playlist summary"
    assert metadata.originally_available_at == datetime.date(2024, 2, 1)
    # The same thumbnail is downloaded twice: once as the series poster, once
    # as the episode image.
    assert stubs.http.calls.args() == (PLAYLIST_THUMB, PLAYLIST_THUMB)
    assert metadata.posters[PLAYLIST_THUMB].data == fixtures.image_bytes(PLAYLIST_THUMB)
    assert metadata.posters[PLAYLIST_THUMB].sort_order == 2

    episode = metadata.seasons["1"].episodes["e001"]
    assert episode.title == "From Playlist"
    assert episode.summary == "Playlist episode summary"
    assert episode.originally_available_at == datetime.date(2024, 2, 2)
    assert episode.thumbs[PLAYLIST_THUMB].data == fixtures.image_bytes(PLAYLIST_THUMB)


def test_update_uses_crowd_sourced_title(scenario):
    scenario.stubs.prefs["use_crowd_sourced_titles"] = True
    scenario.api.add_channel_details(
        CHANNEL_ID,
        fixtures.channel_body(CHANNEL_ID, title="Test Channel", description="Channel description"),
    )
    scenario.api.add_channel_videos(CHANNEL_ID, [])
    scenario.api.add_dearrow(CHANNEL_ID, fixtures.dearrow_body(CHANNEL_ID, "Crowd Title"))

    series = scenario.library.add_series("Test Channel", layout="flat")
    series.add_episode(
        "Only Video",
        "fffffffffff",
        info_json=fixtures.youtube_info("fffffffffff", "Only Video"),
    )

    scenario.register_library()
    metadata = scenario.metadata(CHANNEL_ID, series)

    scenario.update(metadata, series.media())
    stubs = scenario.stubs

    assert stubs.json.calls.args() == (
        CHANNELS_URL.format(CHANNEL_ID, API_KEY),
        CHANNEL_ITEMS_URL.format(CHANNEL_ID, API_KEY),
        dearrow_url(CHANNEL_ID),
    )
    assert metadata.title == "Crowd Title"
    assert metadata.summary == "Original Title: Test Channel\r\n\r\nChannel description"
    assert stubs.http.CacheTime == plex_stubs.CACHE_1MONTH
    assert metadata.seasons["1"].episodes["e001"].title == "Only Video"


def test_update_grouping_folder_becomes_collection(scenario):
    series = scenario.library.add_series("Test Channel", layout="flat", grouping="Test Group")
    scenario.library.add_series("Other Channel", layout="flat", grouping="Test Group")
    series.add_episode(
        "Grouped Video",
        "ggggggggggg",
        info_json=fixtures.youtube_info("ggggggggggg", "Grouped Video"),
    )

    scenario.register_library()
    metadata = scenario.metadata("local-folder", series, series_folder=series.name)

    scenario.update(metadata, series.media())

    assert metadata.collections == ["Test Group"]
    assert metadata.title == "Test Channel"
    assert metadata.studio == "YouTube"
    assert scenario.call_counts() == {
        "json": 0,
        "http": 0,
        "storage": 1,
        "os.walk": 1,
        "os.listdir": 1,
    }
    assert scenario.spies.listdir.args() == (str(scenario.library.root / "Test Group"),)
    assert metadata.seasons["1"].episodes["e001"].title == "Grouped Video"


def test_search_finds_episode_file_in_fake_library(scenario):
    series = scenario.library.add_series("Search Channel", layout="seasons")
    series.add_episode("Search Me", "hhhhhhhhhhh", season=1)

    scenario.register_library()

    results = scenario.search(series.media())

    assert len(results.items) == 1
    result = results.items[0]
    assert result.id == "youtube|hhhhhhhhhhh|Season 01"
    assert result.name == "Search Channel"
    assert result.year is None
    assert result.score == 100
    assert result.lang == "en"
    assert scenario.stubs.json.calls.count == 0


def test_update_can_be_held_inside_object_from_url(scenario):
    scenario.api.add_channel_details(
        CHANNEL_ID,
        fixtures.channel_body(CHANNEL_ID, title="Test Channel", description="Channel description"),
    )
    scenario.api.add_channel_videos(CHANNEL_ID, [])

    series = scenario.library.add_series("Test Channel", layout="flat")
    series.add_episode(
        "Held Video",
        "iiiiiiiiiii",
        info_json=fixtures.youtube_info("iiiiiiiiiii", "Held Video"),
    )

    scenario.register_library()
    metadata = scenario.metadata(CHANNEL_ID, series)
    media = series.media()

    hook = plex_stubs.BlockingHook("channels?part=")
    scenario.stubs.json.before_response = hook

    worker = threading.Thread(target=scenario.update, args=(metadata, media))
    worker.start()
    try:
        assert hook.entered.wait(timeout=10) is True
        # Parked inside the channel details call: prelude work is done, the
        # channel metadata and every episode are still untouched.
        assert worker.is_alive() is True
        assert scenario.stubs.json.calls.count == 1
        assert hook.matched_urls == [CHANNELS_URL.format(CHANNEL_ID, API_KEY)]
        assert metadata.studio == "YouTube"
        # Title still holds the media folder path the prelude derived from dir;
        # the channel branch replaces it once the API responds.
        assert metadata.title == str(series.directory)
        assert metadata.seasons["1"].episodes["e001"].title == ""
    finally:
        hook.release.set()
        worker.join(timeout=10)

    assert worker.is_alive() is False
    assert metadata.title == "Test Channel"
    assert metadata.summary == "Channel description"
    assert metadata.seasons["1"].episodes["e001"].title == "Held Video"
    assert scenario.stubs.json.calls.count == 2
