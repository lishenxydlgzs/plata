"""Readable test projections over the authoritative repository, never runtime paths."""
def catalog_view(knowledge):
    catalog = knowledge.media_catalog
    return {p.properties.get('playlist_id', p.id): [catalog.track(s) for s in catalog.members(p.id)]
            for p in catalog.playlists()}
