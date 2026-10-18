# Media catalog search

The conversation agent uses the read-only `search_media` tool to discover songs
and playlists in the ontology-owned catalog. See
[Ontology-owned media catalog](ontology-media-catalog.md) for storage, migration,
import, classification and playback contracts.

The tool returns distinct song/playlist kinds, ontology entity UUIDs, titles,
bounded metadata, playlist total/available track counts and pagination. It supports
literal text, kind, subject, cycle, week, tag, availability, exact IDs and playlist
membership. Membership browsing keeps persisted order; other searches sort
predictably. Empty queries browse. The agent chooses searches and selections.

Imported and unclassified songs are individually searchable and playable. A file
has one entity across all playlist views. Playback validates selected entities and
current file availability, then uses the existing HA actions and session tracking.
Historical filename, playlist, and earlier local song-hash IDs are accepted only
as compatibility aliases at resolution; new tool output uses entity UUIDs.

The prompt contains tool instructions and recent playback context rather than a
full catalog dump. Search results are untrusted data and cannot write state. Query,
page and output sizes and per-turn calls are bounded. Tests exercise retrieval and
executor contracts independently of model interpretation, without live LLM calls.
