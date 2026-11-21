"""Validated, lossless, read-only authored package model shared by every view."""
from collections import Counter, defaultdict
from contextlib import nullcontext
import json
from pathlib import Path
import zipfile

from wabbajack_format import ComparisonError, directive_type, downloader_type, package_path, unverified_reason

KNOWN_TYPES = {'InlineFile', 'FromArchive', 'PatchedFromArchive', 'CreateBSA',
               'RemappedInlineFile', 'TransformedTexture', 'MergedPatch', 'PropertyFile', 'ArchiveMeta'}
INLINE_TYPES = {'InlineFile', 'RemappedInlineFile', 'PropertyFile', 'ArchiveMeta'}


class PackageModel:
    def __init__(self, path, progress=lambda _: None, *, cached=None):
        self.path = Path(path).expanduser().resolve()
        before = self.path.stat()
        self.source_signature = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
        progress('Restoring cached package metadata…' if cached is not None else 'Reading Wabbajack manifest…')
        if cached is not None:
            if [before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns] != cached['source_signature']:
                raise ComparisonError('The original Wabbajack has changed since this snapshot was saved.')
            self.members = Counter(cached['members'])
            self.manifest = cached['manifest']
        else:
            with zipfile.ZipFile(self.path) as archive:
                self.members = Counter(archive.namelist())
                name = next((n for n in ('modlist', 'modlist.json') if n in self.members), None)
                if not name:
                    raise ComparisonError('The archive has no modlist or modlist.json manifest.')
                if self.members[name] != 1 or archive.getinfo(name).file_size > 768 * 1024**2:
                    raise ComparisonError('Duplicate or oversized Wabbajack manifest.')
                self.manifest = json.loads(archive.read(name))
        if not isinstance(self.manifest, dict) or not isinstance(self.manifest.get('Directives'), list) or not isinstance(self.manifest.get('Archives', []), list):
            raise ComparisonError('Invalid manifest: expected an object with Directives and Archives arrays.')
        self.destinations, self.profiles, self.archives = {}, {}, {}
        self.diagnostics, self.inline_sources, self.temp_inputs = [], {}, defaultdict(list)
        self.text_cache = {}
        for item in self.manifest.get('Archives', []):
            if not isinstance(item, dict) or not item.get('Hash'):
                raise ComparisonError('An archive record has no hash.')
            key = item['Hash']
            if key in self.archives:
                raise ComparisonError(f'Duplicate archive hash: {key}')
            self.archives[key] = item
        for index, item in enumerate(self.manifest['Directives']):
            if index % 2500 == 0:
                progress(f'Indexing instructions: {index:,}/{len(self.manifest["Directives"]):,}')
            if not isinstance(item, dict) or not isinstance(item.get('To'), str):
                raise ComparisonError('A directive has no valid destination.')
            name = package_path(item['To'])
            key, kind = name.casefold(), directive_type(item.get('$type'))
            if key in self.destinations:
                raise ComparisonError(f'Duplicate Wabbajack destination: {name}')
            self.destinations[key] = item
            parts = name.split('/')
            if len(parts) == 3 and parts[0].casefold() == 'profiles' and parts[2].casefold() == 'modlist.txt':
                self.profiles[parts[1].casefold()] = parts[1]
            if kind not in KNOWN_TYPES:
                self.warn('unsupported_instruction', name, f'Unsupported instruction: {kind or "(missing)"}; raw fields retained.')
            chain = item.get('ArchiveHashPath') or []
            if not isinstance(chain, list) or any(not isinstance(part, str) for part in chain):
                raise ComparisonError(f'Invalid source chain: {name}')
            if chain and chain[0] not in self.archives:
                self.warn('missing_archive', name, f'Source archive not found: {chain[0]}')
            if kind in INLINE_TYPES:
                source = item.get('SourceDataID')
                if not isinstance(source, str) or self.members[source] != 1:
                    self.warn('missing_inline_payload', name, 'Missing or duplicate inline payload.')
                else:
                    self.inline_sources[key] = source
            patch = item.get('PatchID')
            if patch and self.members[str(patch)] != 1:
                self.warn('missing_patch', name, f'Missing or duplicate patch payload: {patch}')
            if parts[0].casefold() == 'temp_bsa_files' and len(parts) > 2:
                self.temp_inputs[parts[1].casefold()].append(item)
        self._source_cache = {}
        for item in self.destinations.values():
            if directive_type(item.get('$type')) == 'CreateBSA':
                inputs = self.ingredients(item)
                states = item.get('FileStates', [])
                actual = {package_path(x['To']).split('/', 2)[2].casefold() for x in inputs}
                declared = {str(x.get('Path', '')).replace('\\', '/').casefold() for x in states}
                if not inputs or (declared and actual != declared):
                    self.warn('incomplete_generated_archive', item['To'], f'Generated archive ingredients: {len(actual)} indexed, {len(declared)} declared.')
            if directive_type(item.get('$type')) == 'MergedPatch':
                for source in item.get('Sources', []):
                    key = package_path(source.get('RelativePath', '')).casefold()
                    if key not in self.destinations:
                        self.warn('missing_merged_source', item['To'], f'Merged-patch source not found: {key}')
        after = self.path.stat()
        self.signature = (after.st_ino, after.st_size, after.st_mtime_ns)
        if (before.st_ino, before.st_size, before.st_mtime_ns) != self.signature:
            raise ComparisonError('Wabbajack changed while being read.')
        for item in self.destinations.values():
            if directive_type(item.get('$type')) in ('CreateBSA', 'MergedPatch'):
                self.source_hashes(item)
        if cached is not None:
            self.text_cache = dict(cached.get('text_cache', {}))
            return
        total = 0
        with zipfile.ZipFile(self.path) as archive:
            for index, (key, source) in enumerate(self.inline_sources.items()):
                if not ((key.startswith('mods/') and key.endswith('/meta.ini')) or
                        (key.startswith('profiles/') and len(key.split('/')) == 3 and key.endswith(('.txt', '.ini')))):
                    continue
                if index % 100 == 0:
                    progress('Reading authored profile metadata…')
                info = archive.getinfo(source)
                total += info.file_size
                if info.file_size > 16 * 1024**2 or total > 256 * 1024**2:
                    raise ComparisonError('Inline metadata exceeds reading limits.')
                self.text_cache[key] = archive.read(info).decode('utf-8-sig', errors='replace')
        current = self.path.stat()
        if (current.st_ino, current.st_size, current.st_mtime_ns) != self.signature:
            raise ComparisonError('Wabbajack changed while metadata was being read.')

    def warn(self, code, path, message):
        self.diagnostics.append({'code': code, 'path': path, 'message': message})

    def ingredients(self, item):
        return self.temp_inputs.get(str(item.get('TempID', '')).casefold(), [])

    def source_hashes(self, item, visiting=None):
        key = package_path(item['To']).casefold()
        if key in self._source_cache:
            return self._source_cache[key]
        visiting = set() if visiting is None else visiting
        if key in visiting:
            self.warn('source_cycle', item['To'], 'Cyclic generated/merged source reference.')
            return []
        visiting.add(key)
        hashes = list((item.get('ArchiveHashPath') or [])[:1])
        children = list(self.ingredients(item))
        if directive_type(item.get('$type')) == 'MergedPatch':
            children.extend(self.destinations[k] for source in item.get('Sources', [])
                            if (k := package_path(source.get('RelativePath', '')).casefold()) in self.destinations)
        for child in children:
            hashes.extend(self.source_hashes(child, visiting))
        visiting.remove(key)
        result = list(dict.fromkeys(hashes))
        self._source_cache[key] = result
        return result

    def read_text(self, item, limit=16 * 1024**2, *, archive=None):
        key = package_path(item['To']).casefold() if isinstance(item, dict) else str(item).replace('\\', '/').casefold()
        source = self.inline_sources.get(key)
        if not source:
            raise ComparisonError(f'No readable inline payload: {key}')
        current = self.path.stat()
        if (current.st_ino, current.st_size, current.st_mtime_ns) != self.signature:
            raise ComparisonError('Wabbajack changed since it was opened; reload it.')
        if key not in self.text_cache:
            with (zipfile.ZipFile(self.path) if archive is None else nullcontext(archive)) as archive:
                info = archive.getinfo(source)
                if info.file_size > limit:
                    raise ComparisonError(f'Inline text exceeds the {limit:,}-byte preview limit: {key}')
                self.text_cache[key] = archive.read(info).decode('utf-8-sig', errors='replace')
        if len(self.text_cache[key].encode('utf-8')) > limit:
            raise ComparisonError(f'Inline text exceeds the {limit:,}-byte preview limit: {key}')
        return self.text_cache[key]

    @staticmethod
    def category(item):
        root = package_path(item['To']).split('/')[0].casefold()
        return {'mods': 'Mods', 'profiles': 'Profiles', 'game root': 'Game Root', 'tools': 'Tools',
                'temp_bsa_files': 'Archive ingredients'}.get(root, 'Other')

    def overview(self):
        final = [d for d in self.destinations.values() if self.category(d) != 'Archive ingredients']
        unknown = sum(directive_type(d.get('$type')) == 'CreateBSA' and not d.get('Size') for d in final)
        return {'source': str(self.path), 'metadata': {k: v for k, v in self.manifest.items() if k not in ('Archives', 'Directives')},
                'instructions': len(self.destinations), 'categories': dict(Counter(self.category(d) for d in self.destinations.values())),
                'instruction_types': dict(Counter(directive_type(d.get('$type')) for d in final)),
                'download_archives': len(self.archives),
                'download_bytes_unique': sum(a.get('Size', 0) or 0 for a in self.archives.values() if downloader_type(a.get('State', {}).get('$type')) != 'GameFileSource'),
                'known_output_bytes': sum(d.get('Size', 0) or 0 for d in final), 'unknown_generated_sizes': unknown,
                'exact_hash_eligible': sum(not unverified_reason({'type': directive_type(d.get('$type')), 'hash': d.get('Hash')}) for d in final),
                'required_game_versions': sorted({f"{a['State'].get('Game', '')} {a['State'].get('GameVersion', '')}".strip()
                                                  for a in self.archives.values() if downloader_type(a.get('State', {}).get('$type')) == 'GameFileSource'}),
                'diagnostics': self.diagnostics}
