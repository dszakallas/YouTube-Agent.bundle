"""Regression tests for fetching channel videos from the uploads playlist (D8).

A channel's uploads can be listed with ``playlistItems`` on
``contentDetails.relatedPlaylists.uploads`` (one quota unit per page, no 500
result cap) instead of ``search.list`` (100 units per page, ~500 cap). The
fallback to ``search.list`` must stay for channels whose details do not expose
an uploads playlist.
"""

import datetime

import fixtures

CHANNEL_ID = "UC0123456789abcdefghijkl"
UPLOADS_ID = "UU0123456789abcdefghijkl"
API_KEY = "pref-youtube-api-key"
API = "https://www.googleapis.com/youtube/v3/"

CHANNELS_URL = (
    API + "channels?part=snippet%2CcontentDetails%2Cstatistics%2CbrandingSettings&id={}&key={}"
)
CHANNEL_ITEMS_URL = (
    API + "search?order=date&part=snippet&type=video&maxResults=50&channelId={}&key={}"
)
PLAYLIST_ITEMS_URL = (
    API + "playlistItems?part=snippet,contentDetails&maxResults=50&playlistId={}&key={}"
)

UPLOAD_THUMB = "https://i.ytimg.com/vi/ccccccccccc/mqdefault.jpg"


def prepare_series(scenario, episodes):
    series = scenario.library.add_series("Test Channel", layout="flat")
    for title, video_id in episodes:
        series.add_episode(title, video_id)
    scenario.register_library()
    return series


def episode_titles(metadata):
    return [episode.title for episode in metadata.seasons["1"].episodes.values()]


def test_channel_update_uses_uploads_playlist(scenario):
    scenario.api.add_channel_details(
        CHANNEL_ID,
        fixtures.channel_body(CHANNEL_ID, title="Test Channel", uploads_playlist=UPLOADS_ID),
    )
    scenario.api.add_playlist_videos(
        UPLOADS_ID,
        [
            fixtures.playlist_item("aaaaaaaaaaa", title="First Upload"),
            fixtures.playlist_item("bbbbbbbbbbb", title="Second Upload"),
            fixtures.playlist_item("ggggggggggg", title="Third Upload"),
        ],
        page_size=2,
    )
    series = prepare_series(
        scenario,
        [
            ("Local First", "aaaaaaaaaaa"),
            ("Local Second", "bbbbbbbbbbb"),
            ("Local Third", "ggggggggggg"),
        ],
    )
    metadata = scenario.metadata(CHANNEL_ID, series)

    scenario.update(metadata, series.media())
    stubs = scenario.stubs

    assert stubs.json.calls.args() == (
        CHANNELS_URL.format(CHANNEL_ID, API_KEY),
        PLAYLIST_ITEMS_URL.format(UPLOADS_ID, API_KEY),
        PLAYLIST_ITEMS_URL.format(UPLOADS_ID, API_KEY) + "&pageToken=NEXT_PAGE_1",
    )
    assert [url for url in stubs.json.calls.args() if "search?" in url] == []
    assert episode_titles(metadata) == ["First Upload", "Second Upload", "Third Upload"]
    assert scenario.call_counts() == {
        "json": 3,
        "http": 3,
        "storage": 0,
        "os.walk": 0,
        "os.listdir": 1,
    }
    assert stubs.log.errors == []


def test_channel_without_uploads_playlist_falls_back_to_search(scenario):
    scenario.api.add_channel_details(
        CHANNEL_ID,
        fixtures.channel_body(CHANNEL_ID, title="Test Channel"),
    )
    scenario.api.add_channel_videos(
        CHANNEL_ID,
        [
            fixtures.search_video(
                "ddddddddddd",
                title="Search Upload",
                description="Search summary",
                published_at="2024-06-07T08:09:10Z",
            )
        ],
    )
    series = prepare_series(scenario, [("Local Upload", "ddddddddddd")])
    metadata = scenario.metadata(CHANNEL_ID, series)

    scenario.update(metadata, series.media())
    stubs = scenario.stubs

    assert stubs.json.calls.args() == (
        CHANNELS_URL.format(CHANNEL_ID, API_KEY),
        CHANNEL_ITEMS_URL.format(CHANNEL_ID, API_KEY),
    )
    assert [url for url in stubs.json.calls.args() if "playlistItems" in url] == []
    assert episode_titles(metadata) == ["Search Upload"]
    assert scenario.call_counts() == {
        "json": 2,
        "http": 1,
        "storage": 0,
        "os.walk": 0,
        "os.listdir": 1,
    }
    assert stubs.log.errors == []


def test_channel_with_empty_uploads_playlist_falls_back_to_search(scenario):
    scenario.api.add_channel_details(
        CHANNEL_ID,
        fixtures.channel_body(CHANNEL_ID, title="Test Channel", uploads_playlist=""),
    )
    scenario.api.add_channel_videos(
        CHANNEL_ID,
        [fixtures.search_video("eeeeeeeeeee", title="Search Upload")],
    )
    series = prepare_series(scenario, [("Local Upload", "eeeeeeeeeee")])
    metadata = scenario.metadata(CHANNEL_ID, series)

    scenario.update(metadata, series.media())
    stubs = scenario.stubs

    assert stubs.json.calls.args() == (
        CHANNELS_URL.format(CHANNEL_ID, API_KEY),
        CHANNEL_ITEMS_URL.format(CHANNEL_ID, API_KEY),
    )
    assert episode_titles(metadata) == ["Search Upload"]
    assert stubs.log.errors == []


def test_episode_populated_from_uploads_playlist_items(scenario):
    scenario.api.add_channel_details(
        CHANNEL_ID,
        fixtures.channel_body(CHANNEL_ID, title="Test Channel", uploads_playlist=UPLOADS_ID),
    )
    scenario.api.add_playlist_videos(
        UPLOADS_ID,
        [
            fixtures.playlist_item(
                "ccccccccccc",
                title="Playlist Upload",
                description="Upload summary",
                published_at="2024-05-06T07:08:09Z",
                thumbnail=UPLOAD_THUMB,
            )
        ],
    )
    series = prepare_series(scenario, [("Local Name", "ccccccccccc")])
    metadata = scenario.metadata(CHANNEL_ID, series)

    scenario.update(metadata, series.media())
    stubs = scenario.stubs

    assert stubs.json.calls.args() == (
        CHANNELS_URL.format(CHANNEL_ID, API_KEY),
        PLAYLIST_ITEMS_URL.format(UPLOADS_ID, API_KEY),
    )
    episode = metadata.seasons["1"].episodes["e001"]
    assert episode.title == "Playlist Upload"
    assert episode.summary == "Upload summary"
    assert episode.originally_available_at == datetime.date(2024, 5, 6)
    assert episode.thumbs[UPLOAD_THUMB].data == fixtures.image_bytes(UPLOAD_THUMB)
    assert episode.duration == 0
    assert scenario.call_counts() == {
        "json": 2,
        "http": 1,
        "storage": 0,
        "os.walk": 0,
        "os.listdir": 1,
    }
    assert stubs.log.errors == []


def test_uploads_playlist_payload_looks_like_a_playlist_item(scenario):
    """The uploads playlist returns playlistItems, not search results."""
    scenario.api.add_channel_details(
        CHANNEL_ID,
        fixtures.channel_body(CHANNEL_ID, title="Test Channel", uploads_playlist=UPLOADS_ID),
    )
    payload = fixtures.playlist_item("fffffffffff", title="Resource Id Upload")
    # A playlistItem has no top-level videoId: the id lives in snippet.resourceId.
    assert "videoId" not in payload
    assert payload["snippet"]["resourceId"]["videoId"] == "fffffffffff"
    scenario.api.add_playlist_videos(UPLOADS_ID, [payload])

    series = prepare_series(scenario, [("Local Name", "fffffffffff")])
    metadata = scenario.metadata(CHANNEL_ID, series)

    scenario.update(metadata, series.media())

    assert episode_titles(metadata) == ["Resource Id Upload"]
    assert scenario.stubs.log.errors == []
