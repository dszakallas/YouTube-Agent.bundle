"""Regression tests for episode fault isolation and json_load() termination (D1, D2).

One malformed episode used to abort the whole series update, and one malformed
API response used to abort or hang the paging loop, so these tests pin that a bad
episode, a bad date, an unknown category, a page without items and a non-HTTP
failure are all survivable.
"""

import threading

import fixtures
import plex_stubs
import pytest

CHANNEL_X = "UC0123456789abcdefghijkl"
PAGE_TEMPLATE = "https://example.test/search?q={}&key={}"


def prepare_channel(scenario, name):
    scenario.api.add_channel_details(
        CHANNEL_X,
        fixtures.channel_body(CHANNEL_X, title=name, description=""),
    )
    scenario.api.add_channel_videos(CHANNEL_X, [])


def episode_titles(metadata):
    return {
        episode_key: episode.title
        for season in metadata.seasons.values()
        for episode_key, episode in season.episodes.items()
    }


def serve_pages(scenario, pages):
    """Answer ObjectFromURL with pages in order, repeating the last, with a hard call cap."""
    calls = []

    def responder(url):
        calls.append(url)
        if len(calls) > 60:
            raise RuntimeError("json_load made %d calls" % len(calls))
        return pages[min(len(calls) - 1, len(pages) - 1)]

    scenario.stubs.json.responders.append(responder)
    return calls


def call_json_load(scenario, timeout=5):
    """Run json_load in a thread so a runaway loop cannot hang pytest."""
    outcome = {}

    def target():
        try:
            outcome["value"] = scenario.module.json_load(PAGE_TEMPLATE, "query")
        except BaseException as error:  # noqa: BLE001 - asserted by the caller
            outcome["error"] = error

    worker = threading.Thread(target=target)
    worker.start()
    worker.join(timeout=timeout)
    return worker, outcome


def test_unknown_or_empty_category_does_not_abort_the_series(scenario):
    prepare_channel(scenario, "Category Channel")
    scenario.api.add_video(
        "aaaaaaaaaaa",
        fixtures.video_body("aaaaaaaaaaa", title="First", category_id="10", tags=["alpha"]),
    )
    scenario.api.add_video(
        "bbbbbbbbbbb",
        fixtures.video_body("bbbbbbbbbbb", title="Second", category_id="99", tags=["alpha"]),
    )
    scenario.api.add_video(
        "ccccccccccc",
        fixtures.video_body("ccccccccccc", title="Third", category_id="", tags=["alpha"]),
    )
    series = scenario.library.add_series("Category Channel", layout="flat")
    for index, video_id in enumerate(("aaaaaaaaaaa", "bbbbbbbbbbb", "ccccccccccc"), start=1):
        series.add_episode("Episode %d" % index, video_id, episode_key="e%03d" % index)

    scenario.register_library()
    metadata = scenario.metadata(CHANNEL_X, series)

    scenario.update(metadata, series.media())

    assert episode_titles(metadata) == {"e001": "First", "e002": "Second", "e003": "Third"}
    assert metadata.genres == {"Music", "alpha"}
    assert scenario.stubs.log.errors == []


def test_a_broken_episode_does_not_abort_the_series(scenario):
    prepare_channel(scenario, "Broken Channel")
    series = scenario.library.add_series("Broken Channel", layout="flat")
    for index, video_id in enumerate(("aaaaaaaaaaa", "bbbbbbbbbbb", "ccccccccccc"), start=1):
        series.add_episode(
            "Episode %d" % index,
            video_id,
            episode_key="e%03d" % index,
            info_json=fixtures.youtube_info(video_id, "Episode %d" % index),
        )
    scenario.register_library()

    media = series.media()
    media.seasons["1"].episodes["e002"].items = []

    metadata = scenario.metadata(CHANNEL_X, series)
    scenario.update(metadata, media)

    assert episode_titles(metadata) == {"e001": "Episode 1", "e003": "Episode 3"}
    assert "e002" not in metadata.seasons["1"].episodes
    assert scenario.stubs.log.errors == [
        "update() - error processing season 1 episode e002 (): list index out of range"
    ]


def test_empty_upload_date_and_duration_still_populate_the_episode(scenario):
    prepare_channel(scenario, "Sparse Channel")
    series = scenario.library.add_series("Sparse Channel", layout="flat")
    series.add_episode(
        "Sparse Episode",
        "aaaaaaaaaaa",
        info_json=fixtures.youtube_info(
            "aaaaaaaaaaa",
            "Sparse Episode",
            description="Sparse summary",
            upload_date="",
            duration="",
            categories=["Music"],
        ),
    )
    scenario.register_library()
    metadata = scenario.metadata(CHANNEL_X, series)

    scenario.update(metadata, series.media())

    episode = metadata.seasons["1"].episodes["e001"]
    assert episode.title == "Sparse Episode"
    assert episode.summary == "Sparse summary"
    assert episode.originally_available_at is None
    assert episode.duration == 0
    assert scenario.stubs.log.errors == []


def test_movie_with_empty_published_at_and_unknown_category(scenario):
    scenario.api.add_video(
        "ddddddddddd",
        fixtures.video_body(
            "ddddddddddd",
            title="Movie Title",
            description="Movie summary",
            published_at="",
            duration="",
            category_id="99",
        ),
    )
    series = scenario.library.add_series("Movie Folder", layout="flat")
    episode = series.add_episode("Movie", "ddddddddddd")
    scenario.register_library()

    metadata = scenario.metadata("ddddddddddd", series)
    media = plex_stubs.movie_media(name="Movie", file=str(episode.path))

    scenario.update(metadata, media, movie=True)

    assert metadata.title == "Movie Title"
    assert metadata.summary == "Movie summary"
    assert metadata.originally_available_at is None
    assert metadata.year is None
    assert metadata.genres == []
    assert metadata.duration == 0


def test_json_load_stops_when_a_page_never_gets_a_next_token(scenario):
    calls = serve_pages(scenario, [{}])

    worker, outcome = call_json_load(scenario)

    assert worker.is_alive() is False
    assert "error" not in outcome
    assert len(calls) == 1
    assert outcome["value"]["items"] == []


def test_json_load_caps_pages_that_never_end(scenario):
    calls = serve_pages(scenario, [{"nextPageToken": "loop", "items": []}])

    worker, outcome = call_json_load(scenario)

    assert worker.is_alive() is False
    assert "error" not in outcome
    assert len(calls) == 50
    assert outcome["value"]["items"] == []


def test_json_load_tolerates_pages_without_items(scenario):
    first = {"nextPageToken": "p2"}
    second = {"items": [{"id": {"videoId": "aaaaaaaaaaa"}}, {"id": {"videoId": "bbbbbbbbbbb"}}]}

    calls = serve_pages(scenario, [first, second])
    worker, outcome = call_json_load(scenario)
    assert worker.is_alive() is False
    assert "error" not in outcome
    assert len(calls) == 2
    assert outcome["value"]["items"] == second["items"]

    scenario.stubs.json.responders.pop()
    calls = serve_pages(scenario, [{"pageInfo": {"resultsPerPage": 0}}])
    worker, outcome = call_json_load(scenario)
    assert worker.is_alive() is False
    assert "error" not in outcome
    assert len(calls) == 1
    assert outcome["value"]["items"] == []


def test_json_load_wraps_errors_that_carry_no_response_body(scenario):
    def responder(url):
        raise RuntimeError("network unreachable")

    scenario.stubs.json.responders.append(responder)

    with pytest.raises(ValueError) as error:
        scenario.module.json_load(PAGE_TEMPLATE, "query")

    assert str(error.value) == "network unreachable"
