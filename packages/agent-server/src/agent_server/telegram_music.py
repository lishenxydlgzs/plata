"""Request-local Telegram authority and catalog-backed music delivery tool."""
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from strands import tool

MAX_AUDIO_BYTES = 50 * 1024 * 1024
MAX_TRACKS = 200


@dataclass(frozen=True)
class TelegramMusicContext:
    service: object
    user_id: int
    chat_id: int
    person_id: str
    conversation_id: str
    grant_id: str
    update_id: int


music_context: ContextVar[TelegramMusicContext | None] = ContextVar('telegram_music', default=None)


class MusicSelection(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    media_ids: list[Annotated[str, Field(min_length=1, max_length=300)]] = Field(min_length=1, max_length=20)


def audio_file(catalog, song):
    """Validate at queue and upload time; never accept caller-supplied paths."""
    if not song or song.entity_type != 'media' or not catalog.playable(song):
        raise ValueError('Track is unavailable or hidden.')
    path = catalog.path(song.properties['filename'])
    if path.suffix.lower() not in {'.mp3', '.m4a'}:
        raise ValueError('Only MP3 and M4A tracks can be sent as music.')
    if path.stat().st_size > MAX_AUDIO_BYTES:
        raise ValueError('Track exceeds the 50 MB audio limit.')
    return path


def resolve_tracks(catalog, media_ids):
    selection = MusicSelection(media_ids=media_ids)
    songs, seen = [], set()
    for selected in selection.media_ids:
        entity = catalog.store.get_entity(selected)
        if entity is None or entity.entity_type not in {'media', 'playlist'}:
            raise ValueError('Unknown catalog ID. Search the library first.')
        members = catalog.members(entity.id, available_only=False) if entity.entity_type == 'playlist' else [entity]
        for song in members:
            if song.id not in seen:
                seen.add(song.id)
                songs.append(song)
                if len(songs) > MAX_TRACKS:
                    raise ValueError('Select at most 200 tracks per request.')
    tracks, skipped = [], []
    for song in songs:
        try:
            audio_file(catalog, song)
        except (ValueError, OSError, KeyError, TypeError) as error:
            reason = str(error) if isinstance(error, ValueError) else 'Track file cannot be read.'
            skipped.append({'id': song.id, 'title': song.name[:300], 'reason': reason})
        else:
            tracks.append({'id': song.id, 'title': song.name[:300]})
    return tracks, skipped


def telegram_music_tools(request):
    context = music_context.get()
    if (context is None or request.source != 'telegram'
            or request.person_id != context.person_id
            or request.conversation_id != context.conversation_id):
        return []

    @tool
    async def send_music(media_ids: list[str]) -> dict:
        """Queue library songs or complete ordered playlists to this Telegram chat.

        Args:
            media_ids: Exact song or playlist UUIDs returned by search_media, at most 20.
                Playlists expand in saved order. At most 200 unique tracks per call.
        """
        # Check the active context as well as the captured registration context.
        if music_context.get() is not context:
            return {'error': 'Telegram music authorization is unavailable.'}
        try:
            return context.service.queue_music(context, media_ids)
        except ValidationError:
            return {'error': 'Provide 1–20 exact catalog IDs as strings.'}
        except ValueError as error:
            return {'error': str(error)}

    return [send_music]
