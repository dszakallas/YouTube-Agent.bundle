"""Regression tests for the per-series in-flight guard (root cause RC-B).

Plex re-issues ``update(force=True)`` for a series while an earlier ``Update()``
is still running. Without a guard every one of those threads scans the same
series again, so these tests hold one Update() inside an API call and check what
a second caller does.
"""

import threading

import fixtures
import plex_stubs
import pytest

CHANNEL_X = "UC0123456789abcdefghijkl"
CHANNEL_Y = "UCabcdefghijklmnopqrstuv"
MOVIE_VIDEO_ID = "jjjjjjjjjjj"


def build_series(scenario, name, channel_id, video_id, *, title):
    """A channel series with one episode that is described by an .info.json."""
    scenario.api.add_channel_details(
        channel_id,
        fixtures.channel_body(channel_id, title=name, description=name + " description"),
    )
    scenario.api.add_channel_videos(channel_id, [])
    series = scenario.library.add_series(name, layout="flat")
    series.add_episode(title, video_id, info_json=fixtures.youtube_info(video_id, title))
    scenario.register_library()
    return series


def parked_title(series):
    """Title the prelude derives from the media folder before the channel branch."""
    return str(series.directory)


def start_update(scenario, metadata, media, errors, *, movie=False):
    """Run Update() in a thread, collecting any exception for the caller to check."""

    def target():
        try:
            scenario.update(metadata, media, movie=movie)
        except BaseException as error:  # noqa: BLE001 - surfaced through `errors`
            errors.append(error)

    worker = threading.Thread(target=target)
    worker.start()
    return worker


def test_duplicate_concurrent_update_is_skipped(scenario):
    series = build_series(scenario, "Test Channel", CHANNEL_X, "aaaaaaaaaaa", title="First Video")
    hook = plex_stubs.BlockingHook("id=" + CHANNEL_X)
    scenario.stubs.json.before_response = hook

    metadata_a = scenario.metadata(CHANNEL_X, series)
    metadata_b = scenario.metadata(CHANNEL_X, series)
    errors = []

    worker_a = start_update(scenario, metadata_a, series.media(), errors)
    try:
        assert hook.entered.wait(timeout=10) is True
        parked_counts = {
            "json": 1,
            "http": 0,
            "storage": 0,
            "os.walk": 0,
            "os.listdir": 1,
        }
        assert scenario.call_counts() == parked_counts

        worker_b = start_update(scenario, metadata_b, series.media(), errors)
        worker_b.join(timeout=1)
        assert worker_b.is_alive() is False
        assert errors == []

        # The duplicate did not call the API, the filesystem or the network.
        assert scenario.call_counts() == parked_counts
        assert metadata_b.title == ""
        assert metadata_b.summary == ""
        assert metadata_b.posters == {}
        assert metadata_b.roles.items == []
        assert metadata_b.seasons["1"].episodes["e001"].title == ""
        assert 'update() - skipping concurrent update for: "%s"' % metadata_b.id in (
            scenario.stubs.log.messages
        )
        assert scenario.module.UPDATES_IN_FLIGHT == {(False, metadata_a.id)}
    finally:
        hook.release.set()
        worker_a.join(timeout=10)

    assert worker_a.is_alive() is False
    assert errors == []
    assert scenario.module.UPDATES_IN_FLIGHT == set()
    assert metadata_a.title == "Test Channel"
    assert metadata_a.summary == "Test Channel description"
    assert metadata_a.seasons["1"].episodes["e001"].title == "First Video"


def test_different_series_run_concurrently(scenario):
    series_x = build_series(scenario, "Channel X", CHANNEL_X, "aaaaaaaaaaa", title="X Video")
    series_y = build_series(scenario, "Channel Y", CHANNEL_Y, "bbbbbbbbbbb", title="Y Video")
    hook = plex_stubs.BlockingHook("id=" + CHANNEL_X)
    scenario.stubs.json.before_response = hook

    metadata_x = scenario.metadata(CHANNEL_X, series_x)
    metadata_y = scenario.metadata(CHANNEL_Y, series_y)
    errors = []

    worker_x = start_update(scenario, metadata_x, series_x.media(), errors)
    try:
        assert hook.entered.wait(timeout=10) is True

        worker_y = start_update(scenario, metadata_y, series_y.media(), errors)
        worker_y.join(timeout=10)
        assert worker_y.is_alive() is False
        assert errors == []

        # Y was not mistaken for a duplicate of X and finished while X was parked.
        assert metadata_y.title == "Channel Y"
        assert metadata_y.seasons["1"].episodes["e001"].title == "Y Video"
        assert metadata_x.title == parked_title(series_x)
        assert scenario.module.UPDATES_IN_FLIGHT == {(False, metadata_x.id)}
    finally:
        hook.release.set()
        worker_x.join(timeout=10)

    assert worker_x.is_alive() is False
    assert errors == []
    assert metadata_x.title == "Channel X"
    assert metadata_x.seasons["1"].episodes["e001"].title == "X Video"
    assert scenario.module.UPDATES_IN_FLIGHT == set()
    assert scenario.call_counts() == {
        "json": 4,
        "http": 2,  # one remote .info.json thumbnail per series
        "storage": 2,
        "os.walk": 2,
        "os.listdir": 2,
    }


def test_exception_releases_the_guard(scenario):
    scenario.api.add_video(
        MOVIE_VIDEO_ID,
        fixtures.video_body(MOVIE_VIDEO_ID, title="Movie Title", description="Movie summary"),
    )
    series = scenario.library.add_series("Movie Folder", layout="flat")
    series.add_episode("Movie", MOVIE_VIDEO_ID)
    scenario.register_library()

    metadata = scenario.metadata(MOVIE_VIDEO_ID, series)
    broken_media = plex_stubs.MediaContainer(name="Broken")

    # A media object with no items raises IndexError inside perform_update().
    with pytest.raises(IndexError):
        scenario.update(metadata, broken_media, movie=True)

    assert scenario.module.UPDATES_IN_FLIGHT == set()

    retry = scenario.metadata(MOVIE_VIDEO_ID, series)
    movie_media = plex_stubs.movie_media(name="Movie", file=str(series.episodes[0].path))
    scenario.update(retry, movie_media, movie=True)

    assert retry.title == "Movie Title"
    assert retry.summary == "Movie summary"
    assert retry.duration == 754000
    assert retry.year == 2024
    assert retry.genres == ["Music"]
    assert scenario.module.UPDATES_IN_FLIGHT == set()
    assert scenario.stubs.json.calls.count == 1


def test_movie_and_tv_with_the_same_id_do_not_collide(scenario):
    series = build_series(scenario, "Test Channel", CHANNEL_X, "aaaaaaaaaaa", title="TV Episode")
    scenario.api.add_video(
        CHANNEL_X,
        fixtures.video_body(CHANNEL_X, title="Movie Title", description="Movie summary"),
    )
    hook = plex_stubs.BlockingHook("channels?part=")
    scenario.stubs.json.before_response = hook

    metadata_tv = scenario.metadata(CHANNEL_X, series)
    metadata_movie = scenario.metadata(CHANNEL_X, series)
    assert metadata_tv.id == metadata_movie.id

    movie_media = plex_stubs.movie_media(
        name="Movie",
        file=str(scenario.library.root / "movie.mp4"),
    )
    errors = []

    worker_tv = start_update(scenario, metadata_tv, series.media(), errors)
    try:
        assert hook.entered.wait(timeout=10) is True

        worker_movie = start_update(scenario, metadata_movie, movie_media, errors, movie=True)
        worker_movie.join(timeout=10)
        assert worker_movie.is_alive() is False
        assert errors == []

        # Same id string, different key: the movie update ran and was not skipped.
        assert metadata_movie.title == "Movie Title"
        assert metadata_movie.summary == "Movie summary"
        assert metadata_tv.title == parked_title(series)
        assert scenario.module.UPDATES_IN_FLIGHT == {(False, metadata_tv.id)}
    finally:
        hook.release.set()
        worker_tv.join(timeout=10)

    assert worker_tv.is_alive() is False
    assert errors == []
    assert metadata_tv.title == "Test Channel"
    assert metadata_tv.seasons["1"].episodes["e001"].title == "TV Episode"
    assert scenario.module.UPDATES_IN_FLIGHT == set()
    assert scenario.stubs.json.calls.count == 3
