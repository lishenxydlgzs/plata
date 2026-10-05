"""Media catalog scanning and action helpers."""

import logging
import os
from pathlib import Path
from typing import Any

from .models import Action, ConversationMode, ConversationResponse
from .cc_catalog import MANIFEST, playlist_groups, read_manifest

logger = logging.getLogger(__name__)

MEDIA_DIR = Path(os.environ.get("MEDIA_DIR", "./media"))
MEDIA_BASE = "media-source://media_source/local/kids_robot"
MEDIA_EXTENSIONS = {".mp3", ".mp4", ".wav", ".ogg", ".flac", ".m4a"}
SYSTEM_MEDIA_FILENAMES = {"timer.wav"}

_playlist_cache: dict[str, list[dict[str, Any]]] | None = None

def invalidate_playlist_cache() -> None:
    global _playlist_cache
    _playlist_cache = None


def _title_from_filename(stem: str) -> str:
    """Convert a filename stem like 'bedtime_music' or 'my-lullaby' to a title."""
    return stem.replace("_", " ").replace("-", " ").title()


def _is_playable_file(path: Path) -> bool:
    """Return whether Home Assistant can safely resolve this media path."""
    if not path.is_file() or path.suffix.lower() not in MEDIA_EXTENSIONS:
        return False
    if ".." in path.name:
        logger.warning("Skipping unsafe media filename containing '..': %s", path)
        return False
    return True


def scan_media_catalog() -> list[dict[str, Any]]:
    """Scan the media directory and build a catalog from filenames."""
    if not MEDIA_DIR.is_dir():
        logger.warning("Media directory does not exist: %s", MEDIA_DIR)
        return []

    catalog = []
    for path in sorted(MEDIA_DIR.iterdir()):
        if (
            not _is_playable_file(path)
            or path.name.lower() in SYSTEM_MEDIA_FILENAMES
        ):
            continue
        stem = path.stem
        title = _title_from_filename(stem)
        catalog.append({
            "id": stem.lower().replace("-", "_").replace(" ", "_"),
            "title": title,
            "file": path.name,
            "media_content_type": "music",
        })

    logger.info("Media catalog scanned: %d items from %s", len(catalog), MEDIA_DIR)
    return catalog


def get_media_catalog() -> list[dict[str, Any]]:
    """Return the current media catalog by scanning the media directory."""
    return scan_media_catalog()


def scan_playlist_catalog() -> dict[str, list[dict[str, Any]]]:
    """Scan subdirectories for playlist folders.

    Returns a dict mapping playlist_id to list of track items.
    A playlist is any nested directory structure containing audio files.
    Directory path becomes the playlist ID (e.g. cc_cycle3/week_1 → cc_cycle3_week_1).
    """
    global _playlist_cache
    if _playlist_cache is not None:
        return _playlist_cache

    if not MEDIA_DIR.is_dir():
        return {}

    playlists: dict[str, list[dict[str, Any]]] = {}
    manifests = {path.parent: read_manifest(path.parent)
                 for path in MEDIA_DIR.glob(f"*/{MANIFEST}")}
    replace_legacy = any(
        manifest.get("cc_mode") and manifest.get("replace_legacy_cc")
        and any(Path(track["filename"]).name == track["filename"]
                and _is_playable_file(folder / track["filename"])
                for track in manifest["tracks"].values())
        for folder, manifest in manifests.items()
    )
    cc_tracks = []

    for subdir in sorted(MEDIA_DIR.rglob("*")):
        if not subdir.is_dir() or any(part.startswith(".") for part in subdir.relative_to(MEDIA_DIR).parts):
            continue
        # Skip the root media dir itself
        rel_path = subdir.relative_to(MEDIA_DIR)
        if replace_legacy and rel_path.parts[0] in {"cc_cycle1", "cc_cycle2", "cc_cycle3"}:
            continue
        if str(rel_path) == ".":
            continue

        # Check if this directory has audio files directly in it
        tracks = []
        manifest = manifests.get(subdir, {})
        metadata = {track["filename"]: track for track in manifest.get("tracks", {}).values()}
        for path in sorted(subdir.iterdir()):
            if _is_playable_file(path):
                track = {
                    "file": str(path.relative_to(MEDIA_DIR)),
                    "title": _title_from_filename(path.stem),
                    "media_content_type": "music",
                }
                if manifest.get("cc_mode"):
                    info = metadata.get(path.name, {})
                    track.update({"title": info.get("title", track["title"]),
                                  "subjects": info.get("subjects", []),
                                  "cycle": info.get("cycle"), "all_cycles": info.get("all_cycles", False),
                                  "weeks": info.get("weeks", [])})
                    for key in ('cycles', 'tags', 'topics', 'entities', 'confidence', 'needs_review',
                                'explanation', 'classification', 'classification_status', 'classification_error'):
                        if key in info:
                            track[key] = info[key]
                    cc_tracks.append(track)
                tracks.append(track)

        if tracks:
            playlist_id = str(rel_path).replace("/", "_").replace(" ", "_").lower()
            playlists[playlist_id] = tracks

    for track in cc_tracks:
        for group in playlist_groups(track):
            playlists.setdefault(group, []).append(track)

    logger.info("Playlist catalog scanned: %d playlists from %s", len(playlists), MEDIA_DIR)
    _playlist_cache = playlists
    return playlists


def get_playlist_catalog() -> dict[str, list[dict[str, Any]]]:
    """Return the current playlist catalog."""
    return scan_playlist_catalog()


CC_SUBJECTS = {
    "bible": ["bible", "books_of_the_bible"],
    "english": ["english"],
    "history": ["history"],
    "science": ["science"],
    "latin": ["latin", "john_1"],
    "geography": ["geography"],
    "timeline": ["timeline"],
    "math": ["skip_counting", "math", "geometry", "associative", "commutative", "distributive", "identity_law", "equivalents", "teaspoons"],
}


def resolve_playlist(playlist_id: str) -> list[dict[str, Any]] | None:
    """Resolve a playlist ID to its track list. Returns None if not found.

    Supports subject filtering: "cc_cycle3_week_5_science" resolves to
    week 5's playlist filtered to science tracks only.
    """
    playlists = get_playlist_catalog()

    # Direct match first
    if playlist_id in playlists:
        tracks = playlists[playlist_id]
        return [
            {**track, "media_content_id": f"{MEDIA_BASE}/{track['file']}"}
            for track in tracks
        ]

    # Try subject-filtered match: e.g. "cc_cycle3_week_5_science"
    for subject, keywords in CC_SUBJECTS.items():
        suffix = f"_{subject}"
        if playlist_id.endswith(suffix):
            base_id = playlist_id[: -len(suffix)]
            tracks = playlists.get(base_id)
            if tracks:
                filtered = [
                    t for t in tracks
                    if (subject in t["subjects"] if "subjects" in t
                        else any(kw in t["file"].lower() for kw in keywords))
                ]
                if filtered:
                    return [
                        {**track, "media_content_id": f"{MEDIA_BASE}/{track['file']}"}
                        for track in filtered
                    ]

    return None


def media_stop_response(reply_text: str) -> ConversationResponse:
    return ConversationResponse(
        reply_text=reply_text,
        mode=ConversationMode.CHAT,
        continue_conversation=False,
        actions=[
            Action(
                type="ha_service",
                data={
                    "domain": "kids_robot",
                    "service": "stop_playback",
                    "service_data": {},
                },
            )
        ],
    )


def media_play_response(reply_text: str, item: dict[str, Any]) -> ConversationResponse:
    return ConversationResponse(
        reply_text=reply_text,
        mode=ConversationMode.CHAT,
        continue_conversation=False,
        actions=[
            Action(
                type="ha_service",
                data={
                    "domain": "media_player",
                    "service": "play_media",
                    "service_data": {
                        "media_content_id": f"{MEDIA_BASE}/{item['file']}",
                        "media_content_type": item["media_content_type"],
                    },
                },
            )
        ],
    )


def media_playlist_response(
    reply_text: str,
    items: list[dict[str, Any]],
    session_id: str | None = None,
    start_index: int = 0,
) -> ConversationResponse:
    """Build a response that plays multiple tracks sequentially."""
    tracks = [f"{MEDIA_BASE}/{item['file']}" for item in items]
    return ConversationResponse(
        reply_text=reply_text,
        mode=ConversationMode.CHAT,
        continue_conversation=False,
        actions=[
            Action(
                type="ha_service",
                data={
                    "domain": "kids_robot",
                    "service": "play_playlist",
                    "service_data": {
                        "tracks": tracks,
                        "session_id": session_id,
                        "start_index": start_index,
                    },
                },
            )
        ],
    )
