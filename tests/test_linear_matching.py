"""Regression tests for linear episode matching (root causes RC-A, RC-A2, D6/D7).

Before this change every episode re-walked the series folder for its
``.info.json`` and re-scanned the whole API item list, which is O(E x N) on disk
and O(E x I) over the network payload. These tests pin the O(1) behaviour, the
exact matching precedence, and the NFC/NFD filename handling.
"""

import os
import unicodedata

import fixtures
import plex_stubs

CHANNEL_X = "UC0123456789abcdefghijkl"


def build_channel(scenario, name, *, description=None):
    scenario.api.add_channel_details(
        CHANNEL_X,
        fixtures.channel_body(CHANNEL_X, title=name, description=description or ""),
    )
    return scenario


def channel_items_payload(scenario):
    """The canned channel uploads payload (the URL is built from the template)."""
    url = next(url for url in scenario.api.responses if "channelId=" + CHANNEL_X in url)
    return scenario.api.responses[url]


class CountingList(list):
    """A list that counts how many times something iterates it."""

    def __init__(self, items):
        list.__init__(self, items)
        self.iterations = 0

    def __iter__(self):
        self.iterations += 1
        return list.__iter__(self)


def snapshot(metadata):
    """Every episode field the plugin can set, as plain values."""
    result = {}
    for season_key in sorted(metadata.seasons):
        for episode_key in sorted(metadata.seasons[season_key].episodes):
            episode = metadata.seasons[season_key].episodes[episode_key]
            result[(season_key, episode_key)] = {
                "title": episode.title,
                "summary": episode.summary,
                "duration": episode.duration,
                "rating": episode.rating,
                "date": episode.originally_available_at,
                "thumbs": sorted(episode.thumbs.keys()),
                "directors": [director.name for director in episode.directors],
            }
    return result


def test_walks_the_series_folder_once_for_200_info_json_episodes(scenario):
    build_channel(scenario, "Bulk Channel")
    scenario.api.add_channel_videos(CHANNEL_X, [])

    series = scenario.library.add_series("Bulk Channel", layout="seasons")
    expected_titles = {}
    for index in range(200):
        video_id = "v%010d" % index
        title = "Episode %03d" % index
        episode = series.add_episode(
            title,
            video_id,
            season=1 + index % 2,
            info_json=fixtures.youtube_info(video_id, title, duration=100 + index),
        )
        expected_titles[episode.episode_key] = title

    scenario.register_library()
    metadata = scenario.metadata(CHANNEL_X, series)

    scenario.update(metadata, series.media())

    assert scenario.spies.walk.count == 1
    assert scenario.spies.walk.args() == (str(series.directory),)
    assert scenario.stubs.data.calls.count == 200

    titles = {
        episode_key: episode.title
        for season in metadata.seasons.values()
        for episode_key, episode in season.episodes.items()
    }
    assert titles == expected_titles
    assert metadata.seasons["1"].episodes["e001"].duration == 100
    assert metadata.seasons["2"].episodes["e200"].duration == 299


def test_iterates_the_api_items_once_for_50_episodes(scenario):
    build_channel(scenario, "Counting Channel")
    videos = [
        fixtures.search_video("v%010d" % index, title="Item %03d" % index) for index in range(50)
    ]
    scenario.api.add_channel_videos(CHANNEL_X, videos, page_size=50)

    payload = channel_items_payload(scenario)
    counting = CountingList(payload["items"])
    payload["items"] = counting

    series = scenario.library.add_series("Counting Channel", layout="flat")
    for index in range(50):
        series.add_episode("Item %03d" % index, "v%010d" % index)

    scenario.register_library()
    metadata = scenario.metadata(CHANNEL_X, series)

    scenario.update(metadata, series.media())

    assert counting.iterations == 1
    assert scenario.spies.walk.count == 0
    titles = {
        episode_key: episode.title
        for season in metadata.seasons.values()
        for episode_key, episode in season.episodes.items()
    }
    assert titles == {"e%03d" % (index + 1): "Item %03d" % index for index in range(50)}


def test_matching_parity_with_the_pre_fix_linear_scan(scenario):
    module = scenario.module

    def oracle(items, filename):
        """The pre-fix rule: first item in list order whose videoId is in filename."""
        for position, video in enumerate(items):
            video_id = module.Dict(video, "id", "videoId") or module.Dict(
                video, "snippet", "resourceId", "videoId"
            )
            if video_id and video_id in filename:
                return (position, video)
        return None

    def video(video_id, title):
        return {"id": {"videoId": video_id}, "snippet": {"title": title}}

    def playlist_video(video_id, title):
        return {
            "id": "playlist-item-" + video_id,
            "snippet": {"resourceId": {"videoId": video_id}, "title": title},
        }

    both = [video("bbbbbbbbbbb", "b"), video("aaaaaaaaaaa", "a")]
    cases = [
        # Two ids in the filename; list order wins over filename order.
        ("Clip [aaaaaaaaaaa] extras [bbbbbbbbbbb].mp4", both),
        ("Clip [bbbbbbbbbbb] extras [aaaaaaaaaaa].mp4", both),
        # Id at the start and at the end of the name.
        ("[aaaaaaaaaaa] title.mp4", [video("aaaaaaaaaaa", "a")]),
        ("title [aaaaaaaaaaa].mp4", [video("aaaaaaaaaaa", "a")]),
        # Overlapping windows: the id starts one character into the name.
        ("xaaaaaaaaaaay.mp4", [video("aaaaaaaaaaa", "a")]),
        # resourceId shape from playlistItems.
        ("title [aaaaaaaaaaa].mp4", [playlist_video("aaaaaaaaaaa", "from playlist")]),
        # Duplicate ids: the first occurrence wins.
        (
            "title [aaaaaaaaaaa].mp4",
            [video("aaaaaaaaaaa", "first"), video("aaaaaaaaaaa", "second")],
        ),
        # Non-11-character ids still match through the fallback probe.
        ("a short clip.mp4", [video("short", "short id")]),
        # A 12-character id is not matched by its 11-character prefix.
        ("abcdefghijk.mp4", [video("abcdefghijkl", "longer id")]),
        # No hit at all.
        ("no id here.mp4", [video("aaaaaaaaaaa", "a")]),
        ("no id here.mp4", []),
    ]

    for filename, items in cases:
        expected = oracle(items, filename)
        assert module.build_video_index(items) is not None
        assert module.match_video(module.build_video_index(items), filename) == expected, filename


def test_walks_once_and_matches_nfd_info_json_names(scenario, monkeypatch):
    build_channel(scenario, "Cafe Channel")
    scenario.api.add_channel_videos(CHANNEL_X, [])

    nfc_title = unicodedata.normalize("NFC", "Café Video")
    series = scenario.library.add_series("Cafe Channel", layout="flat")
    episode = series.add_episode(
        nfc_title,
        "aaaaaaaaaaa",
        info_json=fixtures.youtube_info("aaaaaaaaaaa", nfc_title, description="Accented"),
    )

    def nfd_walk(path, *args, **kwargs):
        """Report every filename the way a macOS walker can: NFD on disk."""
        for walk_root, dirnames, filenames in scenario.spies.walk(path, *args, **kwargs):
            yield walk_root, dirnames, [unicodedata.normalize("NFD", name) for name in filenames]

    monkeypatch.setattr(os, "walk", nfd_walk)

    nfd_name = unicodedata.normalize("NFD", episode.info_json_path.name)
    nfd_path = str(series.directory / nfd_name)
    scenario.stubs.data.files[nfd_path] = episode.info_json_path.read_bytes()

    scenario.register_library()
    metadata = scenario.metadata(CHANNEL_X, series)

    scenario.update(metadata, series.media())

    assert scenario.spies.walk.count == 1
    episode_metadata = metadata.seasons["1"].episodes["e001"]
    assert episode_metadata.title == nfc_title
    assert episode_metadata.summary == "Accented"
    assert episode_metadata.duration == 600
    assert scenario.stubs.data.calls.args() == (nfd_path,)


def test_matches_an_nfc_info_json_when_the_media_name_is_nfd(scenario):
    """The other direction: disk gives NFC, the Plex part filename is NFD."""
    build_channel(scenario, "Cafe Channel")
    scenario.api.add_channel_videos(CHANNEL_X, [])

    nfc_title = unicodedata.normalize("NFC", "Café Video")
    series = scenario.library.add_series("Cafe Channel", layout="flat")
    episode = series.add_episode(
        nfc_title,
        "aaaaaaaaaaa",
        info_json=fixtures.youtube_info("aaaaaaaaaaa", nfc_title, description="Accented"),
    )

    nfd_name = unicodedata.normalize("NFD", episode.path.name)
    season = plex_stubs.Season()
    season.episodes["e001"] = plex_stubs.Episode(str(series.directory / nfd_name))
    media = plex_stubs.MediaContainer(
        show=series.name,
        filename=str(series.directory / nfd_name),
        seasons={"1": season},
    )

    scenario.register_library()
    metadata = scenario.metadata(CHANNEL_X, series)

    scenario.update(metadata, media)

    episode_metadata = metadata.seasons["1"].episodes["e001"]
    assert episode_metadata.title == nfc_title
    assert episode_metadata.summary == "Accented"
    assert scenario.stubs.data.calls.args() == (str(episode.info_json_path),)


def test_no_walk_when_every_episode_matches_the_api_index(scenario):
    build_channel(scenario, "API Channel")
    videos = [
        fixtures.search_video("v%010d" % index, title="Item %03d" % index) for index in range(20)
    ]
    scenario.api.add_channel_videos(CHANNEL_X, videos, page_size=20)

    series = scenario.library.add_series("API Channel", layout="seasons")
    for index in range(20):
        series.add_episode("Item %03d" % index, "v%010d" % index, season=1)

    scenario.register_library()
    metadata = scenario.metadata(CHANNEL_X, series)

    scenario.update(metadata, series.media())

    assert scenario.spies.walk.count == 0
    assert scenario.stubs.data.calls.count == 0
    assert snapshot(metadata)[("1", "e007")] == {
        "title": "Item 006",
        "summary": "",
        "duration": 0,
        "rating": 0.0,
        "date": plex_stubs.datetime.date(2024, 1, 15),
        "thumbs": ["https://i.ytimg.com/vi/v0000000006/mqdefault.jpg"],
        "directors": [],
    }


def test_mixed_series_metadata_matches_golden(scenario):
    build_channel(scenario, "Mixed Channel", description="Mixed description")
    scenario.api.add_channel_videos(
        CHANNEL_X,
        [
            fixtures.search_video(
                "aaaaaaaaaaa",
                title="From API A",
                description="A summary",
                published_at="2024-01-15T12:34:56Z",
                thumbnail="https://i.ytimg.com/vi/aaaaaaaaaaa/mqdefault.jpg",
            ),
            fixtures.search_video(
                "ccccccccccc",
                title="From API C",
                description="C summary",
                published_at="2024-01-17T12:00:00Z",
                thumbnail="https://i.ytimg.com/vi/ccccccccccc/mqdefault.jpg",
            ),
        ],
    )

    series = scenario.library.add_series("Mixed Channel", layout="seasons")
    series.add_episode("API One", "aaaaaaaaaaa", season=1, episode_key="e001")
    second = series.add_episode(
        "Local Two",
        "bbbbbbbbbbb",
        season=1,
        episode_key="e002",
        info_json=fixtures.youtube_info(
            "bbbbbbbbbbb",
            "Local Two",
            description="B summary",
            upload_date="20240116",
            duration=300,
            categories=["Music"],
            tags=["tag-b"],
            like_count=50,
            dislike_count=50,
        ),
        thumbnails=("jpg",),
    )
    series.add_episode("API Three", "ccccccccccc", season=1, episode_key="e003")

    scenario.register_library()
    metadata = scenario.metadata(CHANNEL_X, series)

    scenario.update(metadata, series.media())

    assert metadata.title == "Mixed Channel"
    assert metadata.summary == "Mixed description"
    assert metadata.genres == set()
    assert snapshot(metadata) == {
        ("1", "e001"): {
            "title": "From API A",
            "summary": "A summary",
            "duration": 0,
            "rating": 0.0,
            "date": plex_stubs.datetime.date(2024, 1, 15),
            "thumbs": ["https://i.ytimg.com/vi/aaaaaaaaaaa/mqdefault.jpg"],
            "directors": [],
        },
        ("1", "e002"): {
            "title": "Local Two",
            "summary": "B summary",
            "duration": 300,
            "rating": 5.0,
            "date": plex_stubs.datetime.date(2024, 1, 16),
            "thumbs": [str(second.thumbnail_paths["jpg"])],
            "directors": [],
        },
        ("1", "e003"): {
            "title": "From API C",
            "summary": "C summary",
            "duration": 0,
            "rating": 0.0,
            "date": plex_stubs.datetime.date(2024, 1, 17),
            "thumbs": ["https://i.ytimg.com/vi/ccccccccccc/mqdefault.jpg"],
            "directors": [],
        },
    }
