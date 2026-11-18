"""Regression tests for lazy thumbnail loading and img_load() (RC-C, RC-D).

The plugin used to read every local image into memory even when the metadata
already had it, and img_load() mutated its own loop input while returning an
empty string on a miss, which both broke the extension probe and silently
disabled the remote fallback.
"""

import os

import fixtures
import plex_stubs

CHANNEL_X = "UC0123456789abcdefghijkl"
REMOTE_THUMB = "https://i.ytimg.com/vi/aaaaaaaaaaa/hqdefault.jpg"


def prepare_channel(scenario, *, name="Lazy Channel", description=""):
    scenario.api.add_channel_details(
        CHANNEL_X,
        fixtures.channel_body(CHANNEL_X, title=name, description=description),
    )
    scenario.api.add_channel_videos(CHANNEL_X, [])


def local_thumb(episode, extension="jpg"):
    return str(episode.thumbnail_paths[extension])


def test_existing_thumbnail_skips_reading_the_image_file(scenario):
    prepare_channel(scenario)
    series = scenario.library.add_series("Lazy Channel", layout="seasons")
    episode = series.add_episode(
        "Local Episode",
        "aaaaaaaaaaa",
        season=1,
        info_json=fixtures.youtube_info(
            "aaaaaaaaaaa", "Local Episode", description="Local summary"
        ),
        thumbnails=("jpg",),
    )
    scenario.register_library()
    metadata = scenario.metadata(CHANNEL_X, series)
    episode_metadata = metadata.seasons["1"].episodes["e001"]
    already_there = plex_stubs.ProxyMedia(b"already-there", sort_order=1)
    episode_metadata.thumbs[local_thumb(episode)] = already_there

    scenario.update(metadata, series.media())

    assert episode_metadata.thumbs[local_thumb(episode)] is already_there
    assert scenario.stubs.data.calls.args() == (str(episode.info_json_path),)
    assert scenario.stubs.http.calls.count == 0
    assert episode_metadata.title == "Local Episode"
    assert episode_metadata.summary == "Local summary"


def test_missing_local_image_falls_back_to_the_info_json_thumbnail(scenario):
    prepare_channel(scenario)
    series = scenario.library.add_series("Lazy Channel", layout="seasons")
    episode = series.add_episode(
        "Remote Episode",
        "aaaaaaaaaaa",
        season=1,
        info_json=fixtures.youtube_info(
            "aaaaaaaaaaa",
            "Remote Episode",
            description="Remote summary",
            thumbnails=[{"url": REMOTE_THUMB, "id": "hq"}],
        ),
    )
    scenario.stubs.http.responses[REMOTE_THUMB] = b"remote-image-bytes"
    scenario.register_library()
    metadata = scenario.metadata(CHANNEL_X, series)

    scenario.update(metadata, series.media())

    episode_metadata = metadata.seasons["1"].episodes["e001"]
    assert scenario.stubs.http.calls.args() == (REMOTE_THUMB,)
    assert episode_metadata.thumbs[REMOTE_THUMB].data == b"remote-image-bytes"
    assert scenario.stubs.data.calls.args() == (str(episode.info_json_path),)
    assert episode_metadata.title == "Remote Episode"


def test_img_load_probes_each_extension_without_doubling_the_path(scenario, tmp_path, monkeypatch):
    relative = tmp_path / "rel"
    relative.mkdir()
    png_base = "Clip [aaaaaaaaaaa]"
    (relative / (png_base + ".png")).write_bytes(b"png-bytes")
    jpeg_base = "Other [ccccccccccc]"
    (relative / (jpeg_base + ".jpeg")).write_bytes(b"jpeg-bytes")

    monkeypatch.chdir(tmp_path)
    module = scenario.module

    found, payload = module.img_load(["", "rel"], png_base + ".mp4")
    assert found == os.path.join("rel", png_base + ".png")
    assert payload is None

    found, payload = module.img_load(["", "rel"], jpeg_base + ".mp4")
    assert found == os.path.join("rel", jpeg_base + ".jpeg")
    assert payload is None

    assert module.img_load(["", "rel"], "Missing [bbbbbbbbbbb].mp4") == (None, None)


def test_prefers_the_image_beside_the_episode(scenario):
    prepare_channel(scenario)
    series = scenario.library.add_series("Lazy Channel", layout="seasons")
    episode = series.add_episode(
        "Beside Episode",
        "aaaaaaaaaaa",
        season=1,
        info_json=fixtures.youtube_info(
            "aaaaaaaaaaa", "Beside Episode", description="Beside summary"
        ),
        thumbnails=("jpg",),  # written into the series root, which must lose here
    )
    beside = episode.path.parent / (episode.path.name.rsplit(".", 1)[0] + ".jpg")
    beside.write_bytes(b"beside-bytes")

    scenario.register_library()
    metadata = scenario.metadata(CHANNEL_X, series)

    scenario.update(metadata, series.media())

    episode_metadata = metadata.seasons["1"].episodes["e001"]
    assert list(episode_metadata.thumbs.keys()) == [str(beside)]
    assert episode_metadata.thumbs[str(beside)].data == b"beside-bytes"
    assert scenario.stubs.data.calls.args() == (str(episode.info_json_path), str(beside))
    assert scenario.stubs.http.calls.count == 0


def test_no_thumbnail_source_never_requests_one(scenario):
    prepare_channel(scenario)
    series = scenario.library.add_series("Lazy Channel", layout="seasons")
    series.add_episode(
        "Bare Episode",
        "aaaaaaaaaaa",
        season=1,
        info_json=fixtures.youtube_info(
            "aaaaaaaaaaa",
            "Bare Episode",
            description="Bare summary",
            duration=42,
            thumbnails=[],
        ),
    )
    scenario.register_library()
    metadata = scenario.metadata(CHANNEL_X, series)

    scenario.update(metadata, series.media())

    episode_metadata = metadata.seasons["1"].episodes["e001"]
    assert scenario.stubs.http.calls.count == 0
    assert episode_metadata.thumbs == {}
    assert episode_metadata.title == "Bare Episode"
    assert episode_metadata.summary == "Bare summary"
    assert episode_metadata.duration == 42
