"""Builders for offline Search()/Update() scenarios.

A scenario is three things wired together: a fake media library on disk, the
Plex media/metadata object trees the plugin expects, and canned YouTube Data
API payloads served through the JSON stub. Everything is offline and
deterministic, so tests can assert exact metadata values and exact call counts.

Typical use in a test::

    series = scenario.library.add_series("Test Channel")
    series.add_episode("First Video", "aaaaaaaaaaa", info_json=fixtures.youtube_info(...))
    scenario.register_library()
    scenario.update(scenario.metadata(CHANNEL_ID, series), series.media())

``Scenario`` is provided by the ``scenario`` fixture in ``tests/conftest.py``.
"""

import hashlib
import json
import os
from pathlib import Path

import plex_stubs

DEARROW_API = "https://sponsor.ajay.app"
VIDEO_BYTES = b"stub-video-bytes"


def image_bytes(url):
    """Bytes the HTTP stub returns for an image url."""
    return b"image:" + url.encode("utf-8")


def thumbnail_bytes(extension):
    """Bytes written for a local thumbnail file."""
    return b"stub-local-thumbnail:" + extension.encode("utf-8")


# ---------------------------------------------------------------------------
# Fake media library on disk
# ---------------------------------------------------------------------------


class EpisodeLayout(object):
    """One episode file plus the sidecar files written next to it."""

    def __init__(self, path, video_id, season, episode_key, info_json, thumbnails, series_root):
        self.path = path
        self.video_id = video_id
        self.season = season
        self.episode_key = episode_key
        self.info_json = info_json
        self.thumbnails = thumbnails
        self.series_root = series_root

    @property
    def info_json_path(self):
        if self.info_json is None:
            return None
        return self.path.with_name(self.path.name.rsplit(".", 1)[0] + ".info.json")

    @property
    def thumbnail_paths(self):
        """Local thumbnail path per written extension."""
        base = self.path.name.rsplit(".", 1)[0]
        return {
            extension: self.series_root / (base + "." + extension) for extension in self.thumbnails
        }

    def __repr__(self):
        return "EpisodeLayout(%r)" % (self.path.name,)


class SeriesLayout(object):
    """A series folder holding episodes, flat or in season subfolders."""

    def __init__(self, library, name, directory, layout="seasons"):
        self.library = library
        self.name = name
        self.directory = Path(directory)
        self.layout = layout
        self.episodes = []
        self.series_root = self.directory

    def add_episode(
        self,
        title,
        video_id,
        *,
        season=1,
        episode_key=None,
        info_json=None,
        thumbnails=(),
    ):
        """Create ``<title> [<video_id>].mp4`` and optional sidecar files.

        ``info_json`` is written next to the episode; local thumbnails are
        written into the series root, which is where ``img_load()`` looks.
        """
        folder = self.directory
        if self.layout == "seasons":
            folder = folder / ("Season %02d" % season)
        folder.mkdir(parents=True, exist_ok=True)

        base = "%s [%s]" % (title, video_id)
        path = folder / (base + ".mp4")
        path.write_bytes(VIDEO_BYTES)

        if info_json is not None:
            (folder / (base + ".info.json")).write_text(json.dumps(info_json), encoding="utf-8")

        thumbnails = tuple(thumbnails)
        for extension in thumbnails:
            (self.series_root / (base + "." + extension)).write_bytes(thumbnail_bytes(extension))

        if episode_key is None:
            episode_key = "e%03d" % (len(self.episodes) + 1)
        episode = EpisodeLayout(
            path,
            video_id,
            season,
            episode_key,
            info_json,
            thumbnails,
            self.series_root,
        )
        self.episodes.append(episode)
        return episode

    def media(self):
        """The Plex media tree for this series, keyed by string season/episode."""
        seasons = {}
        for episode in self.episodes:
            season_key = str(episode.season)
            seasons.setdefault(season_key, plex_stubs.Season())
            seasons[season_key].episodes[episode.episode_key] = plex_stubs.Episode(
                str(episode.path)
            )
        first_file = str(self.episodes[0].path) if self.episodes else None
        return plex_stubs.MediaContainer(show=self.name, filename=first_file, seasons=seasons)


class FakeLibrary(object):
    """A temporary media library root."""

    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def add_series(self, name, *, layout="seasons", grouping=None):
        parent = self.root if grouping is None else self.root / grouping
        directory = parent / name
        directory.mkdir(parents=True, exist_ok=True)
        return SeriesLayout(self, name, directory, layout)

    def __repr__(self):
        return "FakeLibrary(%r)" % (self.root,)


# ---------------------------------------------------------------------------
# yt-dlp .info.json and YouTube Data API payloads
# ---------------------------------------------------------------------------


def youtube_info(
    video_id,
    title,
    *,
    description="",
    upload_date="20240115",
    duration=600,
    categories=(),
    tags=(),
    thumbnails=None,
    like_count=None,
    dislike_count=None,
):
    """A yt-dlp shaped ``<base>.info.json`` payload.

    ``like_count``/``dislike_count`` are written twice because the plugin
    guards on ``likeCount`` but divides by ``like_count``.
    """
    info = {
        "id": video_id,
        "title": title,
        "description": description,
        "upload_date": upload_date,
        "duration": duration,
        "categories": list(categories),
        "tags": list(tags),
        "thumbnails": (
            list(thumbnails)
            if thumbnails is not None
            else [{"url": "https://i.ytimg.com/vi/%s/hqdefault.jpg" % video_id, "id": "hq"}]
        ),
    }
    if like_count is not None:
        info["like_count"] = like_count
        info["likeCount"] = like_count
    if dislike_count is not None:
        info["dislike_count"] = dislike_count
    return info


def channel_body(
    channel_id,
    *,
    title,
    description="",
    country=None,
    banner=None,
    poster=None,
    statistics=None,
):
    """A ``channels`` endpoint item."""
    snippet = {"title": title, "description": description}
    if country is not None:
        snippet["country"] = country
    if poster is not None:
        snippet["thumbnails"] = {"medium": {"url": poster}}
    body = {"id": channel_id, "snippet": snippet}
    if statistics is not None:
        body["statistics"] = statistics
    if banner is not None:
        body["brandingSettings"] = {"image": {"bannerTvLowImageUrl": banner}}
    return body


def search_video(
    video_id, *, title, description="", published_at="2024-01-15T12:34:56Z", thumbnail=None
):
    """A ``search`` endpoint item, as used for a channel's uploads."""
    return {
        "id": {"videoId": video_id},
        "snippet": {
            "title": title,
            "description": description,
            "channelTitle": "Test Channel",
            "publishedAt": published_at,
            "thumbnails": {
                "medium": {
                    "url": thumbnail or "https://i.ytimg.com/vi/%s/mqdefault.jpg" % video_id,
                }
            },
        },
        "contentDetails": {"videoPublishedAt": published_at},
    }


def playlist_body(
    playlist_id, *, title, description="", published_at="2024-02-01T08:00:00Z", channel_id=None
):
    """A ``playlists`` endpoint item."""
    snippet = {"title": title, "description": description, "publishedAt": published_at}
    if channel_id is not None:
        snippet["channelId"] = channel_id
    return {"id": playlist_id, "snippet": snippet}


def playlist_item(
    video_id, *, title, description="", published_at="2024-02-02T09:00:00Z", thumbnail=None
):
    """A ``playlistItems`` endpoint item (the video id lives in resourceId)."""
    return {
        "id": "playlist-item-" + video_id,
        "snippet": {
            "title": title,
            "description": description,
            "publishedAt": published_at,
            "resourceId": {"kind": "youtube#video", "videoId": video_id},
            "thumbnails": {
                "medium": {
                    "url": thumbnail or "https://i.ytimg.com/vi/%s/mqdefault.jpg" % video_id,
                }
            },
        },
        "contentDetails": {"videoPublishedAt": published_at},
    }


def video_body(
    video_id,
    *,
    title,
    description="",
    published_at="2024-03-01T10:00:00Z",
    duration="PT12M34S",
    category_id="10",
    tags=(),
    thumbnail=None,
    statistics=None,
):
    """A ``videos`` endpoint item."""
    return {
        "id": video_id,
        "snippet": {
            "title": title,
            "description": description,
            "publishedAt": published_at,
            "channelTitle": "Test Channel",
            "categoryId": category_id,
            "tags": list(tags),
            "thumbnails": {
                "maxres": {
                    "url": thumbnail or "https://i.ytimg.com/vi/%s/maxresdefault.jpg" % video_id,
                }
            },
        },
        "contentDetails": {"duration": duration},
        "statistics": statistics or {"likeCount": "100", "dislikeCount": "25"},
    }


def dearrow_body(video_id, title, *, votes=1, locked=False, original=False):
    """A DeArrow ``/api/branding/<hash>`` payload."""
    return {
        video_id: {
            "titles": [
                {"title": title, "votes": votes, "locked": locked, "original": original},
            ]
        }
    }


def _image_urls(payload):
    urls = []
    if isinstance(payload, dict):
        for key, value in payload.items():
            if isinstance(value, str) and value.startswith("http") and key.lower().endswith("url"):
                urls.append(value)
            else:
                urls.extend(_image_urls(value))
    elif isinstance(payload, (list, tuple)):
        for item in payload:
            urls.extend(_image_urls(item))
    return urls


class YouTubeAPI(object):
    """Canned responses for the YouTube Data API v3 and the DeArrow endpoint."""

    def __init__(self, module, stubs):
        self.module = module
        self.stubs = stubs
        self.api_key = stubs.prefs["YouTube-Agent_youtube_api_key"]
        self.responses = {}
        stubs.json.responders.append(self.respond)

    def respond(self, url):
        return self.responses.get(url)

    def _template_url(self, template, identifier):
        return template.format(identifier, self.api_key)

    def _serve(self, url, body):
        self.responses[url] = body
        images = _image_urls(body)
        for image_url in images:
            self.stubs.http.responses.setdefault(image_url, image_bytes(image_url))

    def _serve_pages(self, base_url, items, *, page_size, next_token):
        chunks = [items[index : index + page_size] for index in range(0, len(items), page_size)]
        if not chunks:
            chunks = [[]]
        for index, chunk in enumerate(chunks):
            url = base_url
            if index:
                url = base_url + "&pageToken=" + ("%s_%d" % (next_token, index))
            body = {
                "pageInfo": {"resultsPerPage": len(chunk), "totalResults": len(items)},
                "items": list(chunk),
            }
            if index + 1 < len(chunks):
                body["nextPageToken"] = "%s_%d" % (next_token, index + 1)
            self._serve(url, body)

    def add_channel_details(self, channel_id, body):
        self._serve(
            self._template_url(self.module.YOUTUBE_CHANNEL_DETAILS, channel_id), {"items": [body]}
        )

    def add_channel_videos(self, channel_id, videos, *, page_size=2, next_token="NEXT_PAGE"):
        self._serve_pages(
            self._template_url(self.module.YOUTUBE_CHANNEL_ITEMS, channel_id),
            videos,
            page_size=page_size,
            next_token=next_token,
        )

    def add_playlist_details(self, playlist_id, body):
        self._serve(
            self._template_url(self.module.YOUTUBE_PLAYLIST_DETAILS, playlist_id), {"items": [body]}
        )

    def add_playlist_videos(self, playlist_id, videos, *, page_size=50, next_token="NEXT_PAGE"):
        self._serve_pages(
            self._template_url(self.module.YOUTUBE_PLAYLIST_ITEMS, playlist_id),
            videos,
            page_size=page_size,
            next_token=next_token,
        )

    def add_video(self, video_id, body):
        self._serve(
            self._template_url(self.module.YOUTUBE_json_video_details, video_id), {"items": [body]}
        )

    def add_dearrow(self, video_id, body):
        digest = hashlib.sha256(video_id.encode("ascii")).hexdigest()
        self._serve(DEARROW_API + "/api/branding/" + digest[:4], body)


# ---------------------------------------------------------------------------
# Call spies and the scenario object
# ---------------------------------------------------------------------------


class Spies(object):
    """Call-counting wrappers around the filesystem walks the plugin performs."""

    def __init__(self):
        self.walk = plex_stubs.CallSpy(os.walk)
        self.listdir = plex_stubs.CallSpy(os.listdir)

    def install(self, monkeypatch):
        monkeypatch.setattr(os, "walk", self.walk)
        monkeypatch.setattr(os, "listdir", self.listdir)
        return self


class Scenario(object):
    """A loaded plugin module wired to a fake library and a canned YouTube API."""

    def __init__(self, loaded, library):
        self.module = loaded.module
        self.stubs = loaded.stubs
        self.library = library
        self.api = YouTubeAPI(self.module, self.stubs)
        self.spies = Spies()

    def register_library(self, path=None):
        """Teach GetLibraryRootPath() about the fake library root."""
        root = str(self.library.root) if path is None else str(path)
        self.module.PLEX_LIBRARY[root] = "YouTube"
        return root

    def metadata(self, guid, series=None, series_folder=None):
        if series_folder is None:
            series_folder = series.name if series is not None else ""
        metadata = plex_stubs.Metadata()
        metadata.id = "youtube|%s|%s" % (guid, series_folder)
        return metadata

    def update(self, metadata, media):
        return self.module.Update(metadata, media, "en", False, False)

    def search(self, media):
        results = plex_stubs.SearchResults()
        self.module.Search(results, media, "en", False, False)
        return results

    def call_counts(self):
        """Every counter a test may assert on, in one dict."""
        return {
            "json": self.stubs.json.calls.count,
            "http": self.stubs.http.calls.count,
            "storage": self.stubs.data.calls.count,
            "os.walk": self.spies.walk.count,
            "os.listdir": self.spies.listdir.count,
        }
