"""Ontology-owned music catalog; filesystem discovery is an ingestion operation."""
import hashlib
from pathlib import Path

from . import media
from .cc_catalog import MANIFEST, playlist_groups, read_manifest


class MediaCatalog:
    def __init__(self, knowledge):
        self.knowledge = knowledge
        self.store = knowledge.store

    def entities(self, kind):
        rows = self.store._db.execute(
            'SELECT * FROM entities WHERE entity_type=? ORDER BY id', (kind,)).fetchall()
        return [self.store._row_to_entity(row) for row in rows]

    def path(self, filename):
        """Only local, non-hidden, safe files may be ingested or executed."""
        path = Path(filename)
        if path.is_absolute() or not path.parts or any(
                part.startswith('.') or '..' in part for part in path.parts):
            raise ValueError('Unsafe media path')
        resolved = (media.MEDIA_DIR / path).resolve()
        if not resolved.is_relative_to(media.MEDIA_DIR.resolve()):
            raise ValueError('Media path escapes library')
        return resolved

    def playable(self, entity):
        if not entity or entity.entity_type != 'media' or not entity.properties.get('available'):
            return False
        if entity.properties.get('hidden', False):
            return False
        try:
            path = self.path(entity.properties['filename'])
            return media._is_playable_file(path) and path.stat().st_size > 0
        except (ValueError, TypeError, KeyError, OSError):
            return False

    def save(self, entity, *, name=None, **changes):
        props = {**entity.properties, **changes}
        if entity.entity_type == 'media' and name is not None:
            props['title'] = name
        if props != entity.properties or (name is not None and name != entity.name):
            self.store.update_entity(entity.id, name=name, properties=props)
        return self.store.get_entity(entity.id)

    def song(self, filename, title=None, **changes):
        self.path(filename)
        entity = self.store.get_entity_by_identifier('media_path', filename)
        if entity is None:
            # Reuse pre-migration graph identities, including their existing edges.
            entity = next((e for e in self.entities('media')
                           if e.properties.get('filename') == filename), None)
        if entity is None:
            entity = self.store.create_entity('media', title or media._title_from_filename(Path(filename).stem),
                                              {'filename': filename, 'media_content_type': 'music', 'subjects': [],
                                               'cycles': [], 'weeks': [], 'tags': [], 'topics': [], 'entities': [],
                                               'all_cycles': False})
        self.store.set_identifier(entity.id, 'media_path', filename)
        path = self.path(filename)
        available = media._is_playable_file(path) and path.stat().st_size > 0
        return self.save(entity, name=title, title=title or entity.properties.get('title', entity.name),
                         catalog_managed=True, available=available, **changes)

    def playlist(self, alias, **changes):
        entity = self.store.get_entity_by_identifier('playlist_id', alias)
        if entity is None:
            entity = self.store.create_entity('playlist', alias.replace('_', ' ').title(),
                                              {'playlist_id': alias})
            self.store.set_identifier(entity.id, 'playlist_id', alias)
        return self.save(entity, **changes)

    def set_members(self, playlist, songs):
        desired = {song.id for song in songs}
        rows = self.store._db.execute(
            "SELECT id,to_entity FROM links WHERE relationship_type='contains' AND from_entity=?",
            (playlist.id,)).fetchall()
        for row in rows:
            if row['to_entity'] not in desired:
                self.store.delete_link(row['id'])
        for position, song in enumerate(songs):
            self.store.upsert_link('contains', playlist.id, song.id, {'position': position})
        self.save(playlist, track_count=len(songs), active=bool(songs))

    def members(self, playlist_id, *, available_only=True):
        playlist = self.resolve(playlist_id, 'playlist')
        if not playlist:
            return []
        rows = self.store._db.execute(
            """SELECT e.* FROM links l JOIN entities e ON e.id=l.to_entity
               WHERE l.relationship_type='contains' AND l.from_entity=? AND e.entity_type='media'
               ORDER BY CAST(json_extract(l.properties,'$.position') AS INTEGER),e.id""",
            (playlist.id,)).fetchall()
        songs = [self.store._row_to_entity(row) for row in rows]
        if available_only:
            songs = [s for s in songs if s.properties.get('available') and not s.properties.get('hidden')]
        return songs

    def resolve(self, selected, kind=None):
        entity = self.store.get_entity(selected)
        if entity and entity.entity_type in {'media', 'playlist'}:
            return entity if kind is None or entity.entity_type == kind else None
        # Historical conversation selections are aliases only; never produce new IDs.
        if kind != 'media':
            entity = self.store.get_entity_by_identifier('playlist_id', selected)
            if entity:
                return entity
        if kind != 'playlist':
            entity = self.store.get_entity_by_identifier('media_file', selected)
            if entity:
                return entity
            for song in self.entities('media'):
                filename = song.properties.get('filename', '')
                old_id = (Path(filename).stem.lower().replace('-', '_').replace(' ', '_')
                          if '/' not in filename else 'song:' + hashlib.sha256(filename.encode()).hexdigest())
                if selected == old_id:
                    return song
        return None

    def track(self, song):
        return {**song.properties, 'id': song.id, 'file': song.properties['filename'],
                'title': song.name}

    def songs(self, *, available_only=True):
        return [e for e in self.entities('media') if e.properties.get('catalog_managed')
                and (not available_only or (e.properties.get('available') and not e.properties.get('hidden')))]

    def playlists(self):
        return [e for e in self.entities('playlist') if e.properties.get('active', True)]

    def source(self, folder):
        alias = folder.replace('/', '_').replace(' ', '_').lower()
        return self.playlist(alias, origin='folder', source_folder=folder)

    def rebuild_groups(self):
        """Materialize playlist views only from authoritative graph metadata."""
        sources = [p for p in self.entities('playlist') if p.properties.get('origin') == 'folder']
        cutover = any(p.properties.get('cc_mode') and p.properties.get('replace_legacy_cc') for p in sources)
        songs = self.songs(available_only=False)
        by_folder = {}
        for song in songs:
            filename = song.properties['filename']
            hidden = cutover and Path(filename).parts[0] in {'cc_cycle1', 'cc_cycle2', 'cc_cycle3'}
            song = self.save(song, hidden=hidden)
            by_folder.setdefault(str(Path(filename).parent), []).append(song)
        previous_members = {p.properties.get('playlist_id', p.id): self.members(p.id, available_only=False)
                            for p in self.entities('playlist')}
        groups = {}
        source_members = {}
        source_aliases = {p.properties['playlist_id'] for p in sources}
        for source in sources:
            tracks = by_folder.get(source.properties['source_folder'], [])
            tracks.sort(key=lambda s: (s.properties.get('source_position', 1000000), s.properties['filename']))
            visible = [s for s in tracks if not s.properties.get('hidden')]
            source_members[source.properties['playlist_id']] = visible
            if source.properties.get('cc_mode'):
                for song in visible:
                    for alias in playlist_groups(song.properties):
                        groups.setdefault(alias, {})[song.id] = song
        for source in sources:
            alias = source.properties['playlist_id']
            if alias not in groups:
                self.set_members(source, source_members[alias])
        previous = [p for p in self.entities('playlist') if p.properties.get('derived_cc') or p.properties.get('origin') in {'cc_group', 'legacy_catalog'}]
        for alias, group in groups.items():
            playlist = self.playlist(alias, derived_cc=True, **({} if alias in source_aliases else {'origin': 'cc_group'}))
            for song in source_members.get(alias, []):
                group[song.id] = song
            # Preserve persisted order through migration/reclassification; append new members.
            ordered = [s for s in previous_members.get(alias, []) if s.id in group]
            present = {s.id for s in ordered}
            ordered.extend(sorted((s for s in group.values() if s.id not in present),
                                  key=lambda s: s.properties['filename']))
            self.set_members(playlist, ordered)
        for playlist in previous:
            if playlist.properties['playlist_id'] not in groups and playlist.properties['playlist_id'] not in source_aliases:
                self.set_members(playlist, [])
        for song in songs:
            self.knowledge.sync_media_associations(song.id, song.properties)

    def reconcile(self):
        """Discover files; import manifests once, transactionally. Never reimport metadata."""
        db = self.store._db
        with self.store.transaction():
            db.execute('CREATE TABLE IF NOT EXISTS media_catalog_version (version INTEGER PRIMARY KEY)')
            migrating = not db.execute('SELECT 1 FROM media_catalog_version WHERE version=1').fetchone()
            # Adopt existing graph records even when their files are now missing.
            for entity in self.entities('media'):
                filename = entity.properties.get('filename')
                if not filename:
                    continue
                try:
                    self.song(filename)
                except ValueError:
                    self.save(entity, available=False)
            found = set()
            if media.MEDIA_DIR.is_dir():
                for path in sorted(media.MEDIA_DIR.rglob('*')):
                    if not media._is_playable_file(path) or path.name.lower() in media.SYSTEM_MEDIA_FILENAMES:
                        continue
                    filename = str(path.relative_to(media.MEDIA_DIR))
                    try:
                        self.path(filename)
                    except ValueError:
                        continue
                    self.song(filename)
                    found.add(filename)
                    if path.parent != media.MEDIA_DIR:
                        self.source(str(path.parent.relative_to(media.MEDIA_DIR)))
            for song in self.songs(available_only=False):
                if song.properties['filename'] not in found:
                    self.save(song, available=False)
            if migrating:
                for playlist in self.entities('playlist'):
                    if not playlist.properties.get('origin') and playlist.properties.get('playlist_id') and 'track_count' in playlist.properties:
                        self.save(playlist, origin='legacy_catalog')
                for path in sorted(media.MEDIA_DIR.glob(f'*/{MANIFEST}')):
                    folder = str(path.parent.relative_to(media.MEDIA_DIR))
                    manifest = read_manifest(path.parent)
                    self.configure_source(folder, cc_mode=bool(manifest.get('cc_mode')),
                                          replace_legacy_cc=bool(manifest.get('replace_legacy_cc')))
                    for video_id, track in manifest['tracks'].items():
                        filename = track.get('filename', '')
                        if not filename or Path(filename).name != filename:
                            continue
                        metadata = {k: v for k, v in track.items() if k in {
                            'subjects', 'cycles', 'cycle', 'all_cycles', 'weeks', 'tags', 'topics', 'entities',
                            'classification', 'classification_status', 'classification_error', 'confidence',
                            'needs_review', 'explanation'}}
                        metadata['video_id'] = video_id
                        if 'cycles' not in metadata:
                            metadata['cycles'] = [metadata['cycle']] if metadata.get('cycle') else []
                        self.song(f'{folder}/{filename}', title=track.get('title'), **metadata)
                for source in self.entities('playlist'):
                    folder = source.properties.get('source_folder')
                    if not folder:
                        continue
                    for position, song in enumerate(self.members(source.id, available_only=False)):
                        if str(Path(song.properties.get('filename', '')).parent) == folder:
                            self.save(song, source_position=song.properties.get('source_position', position))
                if media.MEDIA_DIR.is_dir():
                    db.execute('INSERT INTO media_catalog_version VALUES(1)')
            self.rebuild_groups()
            for session in self.entities('playback_session'):
                selected = session.properties.get('playlist_id')
                playlist = self.resolve(selected, 'playlist') if selected else None
                if playlist and selected != playlist.id:
                    self.save(session, playlist_id=playlist.id)

    def configure_source(self, folder, **settings):
        return self.save(self.source(folder), **settings)

    def publish(self, folder, video_id, title, filename, position):
        if Path(filename).name != filename:
            raise ValueError('Expected a file basename')
        with self.store.transaction():
            song = self.song(f'{folder}/{filename}', title=title, video_id=video_id, source_position=position)
            self.rebuild_groups()
            return song

    def source_state(self, folder):
        alias = folder.replace('/', '_').replace(' ', '_').lower()
        source = self.store.get_entity_by_identifier('playlist_id', alias)
        settings = source.properties if source else {}
        tracks = {e.properties['video_id']: {
                    **e.properties, 'id': e.id, 'title': e.name, 'filename': Path(e.properties['filename']).name}
                  for e in sorted(self.songs(available_only=False),
                                  key=lambda s: (s.properties.get('source_position', 1000000), s.properties['filename']))
                  if str(Path(e.properties['filename']).parent) == folder and e.properties.get('video_id')}
        return {'cc_mode': settings.get('cc_mode', False),
                'replace_legacy_cc': settings.get('replace_legacy_cc', False), 'tracks': tracks}

    def save_classifications(self, tracks):
        with self.store.transaction():
            for track in tracks:
                entity = self.store.get_entity(track['id'])
                if not entity or entity.entity_type != 'media':
                    raise ValueError('Unknown classification target')
                if entity.name != track['title']:
                    # A title edited during the model call invalidates that result.
                    self.save(entity, classification_status='pending')
                    continue
                changes = {k: v for k, v in track.items() if k in {
                    'subjects', 'cycles', 'cycle', 'all_cycles', 'weeks', 'tags', 'topics', 'entities',
                    'classification', 'classification_status', 'classification_error', 'confidence',
                    'needs_review', 'explanation'}}
                # Removing stale errors matters when a subsequent classification succeeds.
                props = {**entity.properties, **changes}
                if 'classification_error' not in track:
                    props.pop('classification_error', None)
                self.store.update_entity(entity.id, properties=props)
            self.rebuild_groups()
