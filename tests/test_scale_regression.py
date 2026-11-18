"""End-to-end guard against the production incident.

The reported channel had 14,498 files and was left with 7.44 GiB RSS, 46 threads
and a scan that ran for more than ten hours. This module rebuilds that shape
offline: 15,000 episode files, a single channel folder walked once, 500 uploads
served through the uploads playlist, 14,500 ``.info.json`` sidecars, 300 local
``.jpg`` thumbnails and 14,200 sidecars that only carry a remote thumbnail.

The size is what makes it a regression test: the old code walked the whole
channel folder once per unmatched episode and re-scanned every API item for
every episode, so it cannot get through 15,000 episodes at all. A capped
``os.walk`` spy turns that pathological run into an immediate, deterministic
failure instead of a ten-hour hang; the fixed code walks once, so the cap never
fires.
"""

import datetime
import json
import os
import threading
import time

import fixtures
import plex_stubs
import pytest

CHANNEL_ID = "UC0123456789abcdefghijkl"
UPLOADS_ID = "UU0123456789abcdefghijkl"
API_KEY = "pref-youtube-api-key"
API = "https://www.googleapis.com/youtube/v3/"

CHANNELS_URL = (
    API + "channels?part=snippet%2CcontentDetails%2Cstatistics%2CbrandingSettings&id={}&key={}"
)
PLAYLIST_ITEMS_URL = (
    API + "playlistItems?part=snippet,contentDetails&maxResults=50&playlistId={}&key={}"
)

TOTAL_EPISODES = 15000
API_EPISODES = 500
LOCAL_EPISODES = 300
INFO_EPISODES = TOTAL_EPISODES - API_EPISODES
REMOTE_EPISODES = INFO_EPISODES - LOCAL_EPISODES
REMOTE_REPRESENTATIVES = (API_EPISODES + LOCAL_EPISODES, API_EPISODES + LOCAL_EPISODES + 1)
SEASON_SIZE = 100
PAGE_SIZE = 50
UPLOADS_PAGES = API_EPISODES // PAGE_SIZE
EPISODE_KEY = "e%05d"
WALK_CAP = 10

BANNER = "https://yt3.ggpht.com/banner-low"
POSTER = "https://yt3.ggpht.com/poster-medium"
BULK_THUMB = "https://i.ytimg.com/vi/bulkbulkbul/hqdefault.jpg"

# Captured before any fixture patches os.walk, so the cap always wraps the real
# implementation instead of another spy.
_REAL_WALK = os.walk

BULK_INFO = json.dumps(
    {
        "id": "bulkbulkbul",
        "title": "Bulk Episode",
        "description": "Bulk summary",
        "upload_date": "20240101",
        "duration": 300,
        "categories": [],
        "tags": [],
        "thumbnails": [{"url": BULK_THUMB}],
    }
)


def video_id(index):
    prefix = "a" if index < API_EPISODES else "l" if index < API_EPISODES + LOCAL_EPISODES else "r"
    return "%s%010d" % (prefix, index)


def episode_title(index):
    if index < API_EPISODES:
        return "API Episode %d" % index
    if index < API_EPISODES + LOCAL_EPISODES:
        return "Local Episode %d" % index
    if index in REMOTE_REPRESENTATIVES:
        return "Remote Episode %d" % index
    return "Bulk Episode"


class ScaleChannel(object):
    """The 15,000-episode channel on disk, plus what each episode should get."""

    def __init__(self, root, episodes, api_items):
        self.root = root
        self.channel_dir = root / "Scale Channel"
        self.episodes = episodes
        self.api_items = api_items

    def media(self):
        return plex_stubs.tv_media(
            "Scale Channel",
            self.episodes[0]["path"],
            {"1": {episode["key"]: episode["path"] for episode in self.episodes}},
        )


def build_channel(root):
    channel_dir = root / "Scale Channel"
    channel_dir.mkdir(parents=True)
    season_dirs = {}
    episodes = []
    api_items = []
    for index in range(TOTAL_EPISODES):
        season_number = index // SEASON_SIZE + 1
        if season_number not in season_dirs:
            folder = channel_dir / ("Season %03d" % season_number)
            folder.mkdir()
            season_dirs[season_number] = folder
        folder = season_dirs[season_number]
        identifier = video_id(index)
        title = episode_title(index)
        base = "%s [%s]" % (title, identifier)
        path = folder / (base + ".mp4")
        path.write_bytes(fixtures.VIDEO_BYTES)
        episode = {
            "index": index,
            "key": EPISODE_KEY % (index + 1),
            "video_id": identifier,
            "title": title,
            "path": str(path),
        }
        if index < API_EPISODES:
            api_items.append(
                fixtures.playlist_item(
                    identifier,
                    title=title,
                    description="API summary %d" % index,
                    published_at="2024-03-15T10:00:00Z",
                )
            )
        else:
            info_path = folder / (base + ".info.json")
            if index < API_EPISODES + LOCAL_EPISODES:
                info = fixtures.youtube_info(
                    identifier,
                    title,
                    description="Local summary %d" % index,
                    upload_date="20240115",
                    duration=600,
                )
                image_path = channel_dir / (base + ".jpg")
                image_path.write_bytes(fixtures.thumbnail_bytes("jpg"))
                episode["thumb"] = str(image_path)
            elif index in REMOTE_REPRESENTATIVES:
                thumb = "https://i.ytimg.com/vi/%s/hqdefault.jpg" % identifier
                info = fixtures.youtube_info(
                    identifier,
                    title,
                    description="Remote summary %d" % index,
                    upload_date="20240216",
                    duration=700,
                    thumbnails=[{"url": thumb}],
                )
                episode["thumb"] = thumb
            else:
                info = None
                episode["thumb"] = BULK_THUMB
            info_path.write_text(BULK_INFO if info is None else json.dumps(info), encoding="utf-8")
            episode["info"] = str(info_path)
        episodes.append(episode)
    return ScaleChannel(root, episodes, api_items)


class CappedWalk(plex_stubs.CallSpy):
    """os.walk spy that fails the run once the cap is exceeded.

    The pre-fix plugin walked the channel folder once per unmatched episode, so
    without the cap a pre-fix verification would run for hours. The fixed code
    does exactly one walk, which is asserted below, so the cap only guards the
    pathological path.
    """

    def __init__(self, limit):
        super(CappedWalk, self).__init__(_REAL_WALK)
        self.limit = limit

    def __call__(self, *args, **kwargs):
        if self.count >= self.limit:
            raise RuntimeError("scale guard: more than %d os.walk calls" % self.limit)
        return super(CappedWalk, self).__call__(*args, **kwargs)


def install_walk_cap(monkeypatch, limit=WALK_CAP):
    spy = CappedWalk(limit)
    monkeypatch.setattr(os, "walk", spy)
    return spy


@pytest.fixture(scope="module")
def scale_channel(tmp_path_factory):
    return build_channel(tmp_path_factory.mktemp("scale-library"))


def serve_api(scenario, channel):
    scenario.api.add_channel_details(
        CHANNEL_ID,
        fixtures.channel_body(
            CHANNEL_ID,
            title="Scale Channel",
            description="Scale channel",
            banner=BANNER,
            poster=POSTER,
            uploads_playlist=UPLOADS_ID,
        ),
    )
    scenario.api.add_playlist_videos(UPLOADS_ID, channel.api_items)
    # Sidecar thumbnails are written straight to disk, so register the remote
    # ones the same way the API payload builder registers its images.
    for episode in channel.episodes:
        thumb = episode.get("thumb", "")
        if episode.get("info") and thumb.startswith("http"):
            scenario.stubs.http.responses.setdefault(thumb, fixtures.image_bytes(thumb))


def counters(scenario, walk):
    return {
        "json": scenario.stubs.json.calls.count,
        "http": scenario.stubs.http.calls.count,
        "storage": scenario.stubs.data.calls.count,
        "walk": walk.count,
        "listdir": scenario.spies.listdir.count,
    }


def differences(before, after):
    return {key: after[key] - before[key] for key in before}


def info_loads(paths):
    return [path for path in paths if path.endswith(".info.json")]


def image_loads(paths):
    return [path for path in paths if not path.endswith(".info.json")]


def expected_json_urls():
    urls = [CHANNELS_URL.format(CHANNEL_ID, API_KEY)]
    base = PLAYLIST_ITEMS_URL.format(UPLOADS_ID, API_KEY)
    urls.append(base)
    urls.extend(base + "&pageToken=NEXT_PAGE_%d" % page for page in range(1, UPLOADS_PAGES))
    return tuple(urls)


def expected_http_count():
    # Series art, series banners and series poster, plus one request per API
    # episode and per sidecar that only has a remote thumbnail.
    return 3 + API_EPISODES + REMOTE_EPISODES


def test_scale_update_reads_each_info_file_and_image_once(scenario, scale_channel, monkeypatch):
    walk = install_walk_cap(monkeypatch)
    serve_api(scenario, scale_channel)
    scenario.register_library(path=scale_channel.root)
    metadata = scenario.metadata(CHANNEL_ID, series_folder="Scale Channel")
    media = scale_channel.media()
    stubs = scenario.stubs

    before = counters(scenario, walk)
    started = time.perf_counter()
    scenario.update(metadata, media)
    elapsed = time.perf_counter() - started
    first = differences(before, counters(scenario, walk))
    first_paths = list(stubs.data.calls.args(0))[before["storage"] :]
    first_urls = list(stubs.json.calls.args())[before["json"] :]
    print("scale: first Update() %.2fs" % elapsed)

    assert first == {
        "json": 1 + UPLOADS_PAGES,
        "http": expected_http_count(),
        "storage": INFO_EPISODES + LOCAL_EPISODES,
        "walk": 1,
        "listdir": 1,
    }
    assert tuple(first_urls) == expected_json_urls()
    assert len(info_loads(first_paths)) == INFO_EPISODES
    assert len(image_loads(first_paths)) == LOCAL_EPISODES
    assert stubs.http.calls.args()[:3] == (BANNER, BANNER, POSTER)
    assert elapsed < 60

    episodes = metadata.seasons["1"].episodes
    assert len(episodes) == TOTAL_EPISODES
    assert all(episode.title for episode in episodes.values())

    api_episode = episodes[EPISODE_KEY % 1]
    api_thumb = "https://i.ytimg.com/vi/%s/mqdefault.jpg" % video_id(0)
    assert api_episode.title == "API Episode 0"
    assert api_episode.summary == "API summary 0"
    assert api_episode.originally_available_at == datetime.date(2024, 3, 15)
    assert api_episode.thumbs[api_thumb].data == fixtures.image_bytes(api_thumb)

    local = scale_channel.episodes[API_EPISODES]
    local_episode = episodes[local["key"]]
    assert local_episode.title == "Local Episode 500"
    assert local_episode.summary == "Local summary 500"
    assert local_episode.originally_available_at == datetime.date(2024, 1, 15)
    assert local_episode.duration == 600
    assert local_episode.thumbs[local["thumb"]].data == fixtures.thumbnail_bytes("jpg")

    remote = scale_channel.episodes[API_EPISODES + LOCAL_EPISODES]
    remote_episode = episodes[remote["key"]]
    assert remote_episode.title == "Remote Episode 800"
    assert remote_episode.summary == "Remote summary 800"
    assert remote_episode.originally_available_at == datetime.date(2024, 2, 16)
    assert remote_episode.duration == 700
    assert remote_episode.thumbs[remote["thumb"]].data == fixtures.image_bytes(remote["thumb"])

    assert stubs.log.errors == []

    # Second pass on the same metadata tree: every image is already in its
    # container, so only the .info.json files may be read again.
    before_second = counters(scenario, walk)
    started_second = time.perf_counter()
    scenario.update(metadata, media)
    elapsed_second = time.perf_counter() - started_second
    second = differences(before_second, counters(scenario, walk))
    second_paths = list(stubs.data.calls.args(0))[before_second["storage"] :]
    print("scale: second Update() %.2fs" % elapsed_second)

    assert second == {
        "json": 1 + UPLOADS_PAGES,
        "http": 0,
        "storage": INFO_EPISODES,
        "walk": 1,
        "listdir": 1,
    }
    assert image_loads(second_paths) == []
    assert len(info_loads(second_paths)) == INFO_EPISODES
    assert len(episodes) == TOTAL_EPISODES
    assert elapsed_second < 60
    assert stubs.log.errors == []


def test_concurrent_duplicate_update_on_scale_channel_is_skipped(
    scenario, scale_channel, monkeypatch
):
    walk = install_walk_cap(monkeypatch)
    serve_api(scenario, scale_channel)
    scenario.register_library(path=scale_channel.root)
    stubs = scenario.stubs

    metadata_a = scenario.metadata(CHANNEL_ID, series_folder="Scale Channel")
    metadata_b = scenario.metadata(CHANNEL_ID, series_folder="Scale Channel")
    media_a = scale_channel.media()
    media_b = scale_channel.media()

    hook = plex_stubs.BlockingHook("channels?part=")
    stubs.json.before_response = hook
    result = {}

    def run_a():
        try:
            scenario.update(metadata_a, media_a)
        except BaseException as error:  # noqa: BLE001 - reported through result
            result["a_error"] = error

    worker_a = threading.Thread(target=run_a)
    worker_a.start()
    try:
        assert hook.entered.wait(timeout=10) is True
        assert hook.matched_urls == [CHANNELS_URL.format(CHANNEL_ID, API_KEY)]
        # Parked in the first API call: the prelude ran, no walk or image yet.
        before_b = counters(scenario, walk)
        assert before_b == {"json": 1, "http": 0, "storage": 0, "walk": 0, "listdir": 1}

        def run_b():
            started = time.perf_counter()
            scenario.update(metadata_b, media_b)
            result["b_elapsed"] = time.perf_counter() - started

        worker_b = threading.Thread(target=run_b)
        worker_b.start()
        worker_b.join(timeout=2)
        try:
            assert worker_b.is_alive() is False
            assert result["b_elapsed"] < 1.0
            assert counters(scenario, walk) == before_b
        finally:
            hook.release.set()
            worker_a.join(timeout=120)
            worker_b.join(timeout=120)
    finally:
        hook.release.set()
        worker_a.join(timeout=120)

    assert "a_error" not in result
    assert counters(scenario, walk) == {
        "json": 1 + UPLOADS_PAGES,
        "http": expected_http_count(),
        "storage": INFO_EPISODES + LOCAL_EPISODES,
        "walk": 1,
        "listdir": 1,
    }
    assert len(metadata_a.seasons["1"].episodes) == TOTAL_EPISODES
    assert len(metadata_b.seasons) == 0
    assert any("skipping concurrent update" in message for message in stubs.log.infos)
    assert stubs.log.errors == []
