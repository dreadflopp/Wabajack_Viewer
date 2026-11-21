"""Presentation of authored instructions without changing their recorded sources."""
import json

from wabbajack_format import directive_type


EMPTY_FILE_HASH = 'menYUTfbRu8='


def is_empty_archive_file(file_info):
    """Require a known empty-file hash; a missing or generated size is not evidence."""
    return (directive_type(file_info.get('directive_type')) == 'FromArchive'
            and file_info.get('size') == 0 and file_info.get('hash') == EMPTY_FILE_HASH)


def archive_contributions(row):
    """Account for direct and generated inputs, retaining full, unfiltered groups."""
    grouped = {}
    for item in row.get('folder_files', []):
        hashes = list(dict.fromkeys([item.get('archive_hash', '')] + item.get('generated_source_hashes', [])))
        for source_hash in filter(None, hashes):
            grouped.setdefault(source_hash, []).append(item)
    installation = row.get('folder_meta', {}).get('installation_file', '')
    installation = installation.replace('\\', '/').rsplit('/', 1)[-1].casefold()
    result = []
    for archive in row.get('source_archives', []):
        files = grouped.get(archive.get('Hash'), [])
        primary = bool(installation and archive.get('Name', '').casefold() == installation)
        empty_only = bool(files) and all(is_empty_archive_file(item) for item in files)
        embedded = str(archive.get('Hash', '')).startswith('INLINEFILE::')
        role = ('Recorded mod archive' if primary else 'Embedded package files' if embedded
                else 'Empty files only' if empty_only else 'Recorded extraction source')
        result.append({'archive': archive, 'files': files, 'primary': primary,
                       'empty_only': empty_only, 'role': role,
                       'auxiliary_empty': empty_only and not primary and not embedded})
    represented = {id(item) for group in result for item in group['files']}
    other = [item for item in row.get('folder_files', []) if id(item) not in represented]
    if other:
        result.append({'archive': {'Hash': '', 'Name': 'Other instructions'}, 'files': other,
                       'primary': False, 'empty_only': False, 'auxiliary_empty': False,
                       'role': 'Other instructions'})
    return result


def contribution_summary(group, files=None):
    files = group['files'] if files is None else files
    count = len(files)
    suffix = 'file' if count == 1 else 'files'
    if group['empty_only']:
        return f'{count} empty {suffix} · 0 bytes'
    configurations, animations, empty, other = [], [], [], []
    for item in files:
        parts = str(item.get('target_path', '')).replace('\\', '/').casefold().split('/')
        if len(parts) > 2 and parts[0] == 'mods':
            parts = parts[2:]
        name = parts[-1]
        if is_empty_archive_file(item):
            empty.append(item)
        elif name == 'config.json' or name.endswith(('.ini', '.cfg', '.toml', '.yaml', '.yml')):
            configurations.append(item)
        elif name.endswith('.hkx') and 'animations' in parts[:-1]:
            genders = {'male', 'female'} & set(parts[:-1])
            gender = next(iter(genders)) if len(genders) == 1 else None
            animations.append((gender, 'sprint' in name or 'sprint' in parts[:-1]))
        else:
            other.append(item)
    def quantity(number, label):
        return f'{number} {label}{"" if number == 1 else "s"}'
    descriptions = []
    if configurations:
        descriptions.append(quantity(len(configurations), 'configuration file'))
    if animations:
        genders = {gender for gender, _ in animations}
        gender = next(iter(genders)) if len(genders) == 1 else None
        label = (gender + ' ' if gender else '')
        label += 'sprint ' if all(sprint for _, sprint in animations) else ''
        descriptions.append(quantity(len(animations), label + 'animation'))
    if empty:
        descriptions.append(quantity(len(empty), 'empty file'))
    if other:
        descriptions.append(quantity(len(other), 'other file' if descriptions else 'file'))
    if len(descriptions) == 1:
        return descriptions[0]
    if descriptions:
        return f'{count} {suffix} · ' + ', '.join(descriptions)
    return f'{count} {suffix}'


def describe_directive(file_info, raw, archive=None, group=None):
    """Explain the operation; raw JSON remains available for every type-specific field."""
    raw = raw or {}
    kind = directive_type(raw.get('$type') or file_info.get('directive_type'))
    target = raw.get('To') or file_info.get('target_path', '')
    operations = {
        'FromArchive': 'Extract the source file and write it unchanged to the destination.',
        'PatchedFromArchive': 'Extract the source file, apply the bundled binary patch, and write the result.',
        'InlineFile': 'Write a file embedded in the Wabbajack package to the destination.',
        'RemappedInlineFile': 'Write an embedded file with paths substituted for this installation.',
        'PropertyFile': 'Write the embedded property file to the destination.',
        'ArchiveMeta': 'Write download metadata adjusted for this installation.',
        'TransformedTexture': 'Extract the source texture and transform it using the recorded image settings.',
        'CreateBSA': 'Build a game archive from the recorded temporary input files and archive settings.',
        'MergedPatch': 'Build the merged patch from its recorded source files.',
    }
    lines = [operations.get(kind, 'Inspect the recorded fields for this directive.'), '',
             f'Directive type: {kind or "Unknown"}', f'Destination: {target}']
    recorded_chain = raw.get('ArchiveHashPath') or file_info.get('archive_hash_path') or []
    chain = file_info.get('archive_hash_path', []) if file_info.get('preferred_source') else recorded_chain
    if archive:
        lines.append(f'Source archive: {archive.get("Name", "Unknown")}')
    if chain:
        lines.append(f'Archive hash: {chain[0]}')
        lines.append('Source path: ' + ' → '.join(str(part) for part in chain[1:]))
    elif file_info.get('archive_path'):
        lines.append(f'Source path: {file_info["archive_path"]}')
    size = raw.get('Size') if raw else file_info.get('size')
    if kind == 'CreateBSA' and not size:
        size = None
    lines.extend([f'Expected output size: {size:,} bytes' if size is not None else 'Expected output size: Unknown',
                  f'Recorded output hash: {raw.get("Hash") or file_info.get("hash") or "Unknown"}'])
    if group:
        lines.append(f'Archive role: {group["role"]}')
    if file_info.get('preferred_source'):
        lines.extend(['', 'Verified equivalent file in the mod archive; used for this view.',
                      'Recorded Wabbajack source archive: ' + file_info.get('recorded_archive', {}).get('Name', 'Unknown'),
                      'Recorded archive hash: ' + str(recorded_chain[0] if recorded_chain else ''),
                      'Recorded source path: ' + ' → '.join(str(part) for part in recorded_chain[1:]),
                      'Installation exports retain the recorded Wabbajack source.'])
    if is_empty_archive_file(file_info):
        lines.extend(['', 'This is an empty file.'])
        if group and group['auxiliary_empty']:
            lines.append('Wabbajack can select an identical file from another download.')
            lines.append('This archive supplies only empty files to this mod; its selection does not establish a mod dependency.')
            lines.append('Availability in the recorded mod archive has not been checked by this view.')
    settings = {key: value for key, value in raw.items()
                if key not in ('$type', 'To', 'Hash', 'Size', 'ArchiveHashPath')}
    if settings:
        lines.extend(['', 'Additional recorded fields:', json.dumps(settings, indent=2, ensure_ascii=False)])
    return '\n'.join(lines)
