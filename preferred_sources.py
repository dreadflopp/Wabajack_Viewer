"""Verify equivalent archive members for presentation without altering authored instructions."""
from pathlib import Path
import json
import shutil
import subprocess
import tempfile
import time
import zipfile

from profile_compare import FileHasher
from wabbajack_format import directive_type, package_path


class SourceVerificationCancelled(RuntimeError):
    pass


def signature(path):
    try:
        s = Path(path).stat()
        return (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
    except OSError:
        return None


def relative_target(item):
    return str(item.get('target_path', '')).replace('\\', '/').split('/', 2)[-1]


def run_archive_tool(arguments, output, progress, timeout=60):
    with subprocess.Popen(arguments, stdout=output, stderr=subprocess.DEVNULL,
                          stdin=subprocess.DEVNULL) as process:
        deadline = time.monotonic() + timeout
        try:
            while True:
                progress('Reading archive…')
                try:
                    code = process.wait(timeout=.1)
                    break
                except subprocess.TimeoutExpired:
                    if time.monotonic() >= deadline:
                        raise subprocess.TimeoutExpired(arguments, timeout)
            if code:
                raise subprocess.CalledProcessError(code, arguments)
        except BaseException:
            process.kill()
            process.wait()
            raise


def unar_entries(path, progress):
    executable = shutil.which('lsar')
    if not executable or not shutil.which('unar'):
        raise ValueError('7-Zip could not read this archive. Install lsar/unar to verify its sources.')
    with tempfile.TemporaryFile() as listing:
        run_archive_tool([executable, '-j', '-jss', '-nr', str(path.resolve())], listing, progress, 30)
        listing.seek(0)
        return json.load(listing)['lsarContents']


def sevenzip_records(path, progress):
    executable = shutil.which('7z') or shutil.which('7zz') or shutil.which('7za')
    if not executable:
        raise FileNotFoundError('7-Zip is not available.')
    with tempfile.TemporaryFile() as listing:
        run_archive_tool([executable, 'l', '-slt', '-ba', '-sccUTF-8', '--', str(path)], listing, progress, 30)
        listing.seek(0)
        output = listing.read().decode('utf-8')
    records = []
    for block in output.replace('\r\n', '\n').split('\n\n'):
        fields = dict(line.split(' = ', 1) for line in block.splitlines() if ' = ' in line)
        if 'Path' in fields and fields.get('Folder') != '+' and not fields.get('Attributes', '').startswith('D'):
            records.append((fields['Path'], int(fields.get('Size', -1))))
    return records


def archive_members(path, progress=lambda message: None):
    """List actual files, rejecting duplicate or case-ambiguous archive member names."""
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            records = [(item.filename, item.file_size) for item in archive.infolist() if not item.is_dir()]
    else:
        try:
            records = sevenzip_records(path, progress)
        except (FileNotFoundError, subprocess.CalledProcessError):
            records = [(entry['XADFileName'], entry.get('XADFileSize', -1)) for entry in unar_entries(path, progress)
                       if not entry.get('XADIsDirectory') and not entry.get('XADIsLink')]
    members, duplicates = {}, set()
    for name, size in records:
        key = name.replace('\\', '/').casefold()
        if key in members:
            duplicates.add(key)
        members[key] = (name, size)
    return {key: (name, size) for key, (name, size) in members.items() if key not in duplicates}


def member_digest(path, member, size, hasher):
    with tempfile.TemporaryDirectory(prefix='wabbajack-source-') as directory:
        output = Path(directory) / 'member'
        with output.open('wb') as stream:
            if zipfile.is_zipfile(path):
                with zipfile.ZipFile(path) as archive, archive.open(member) as source:
                    remaining = size + 1
                    while remaining:
                        chunk = source.read(min(1024**2, remaining))
                        if not chunk:
                            break
                        stream.write(chunk)
                        remaining -= len(chunk)
                        hasher.progress('Checking archive member: ' + member)
            else:
                executable = shutil.which('7z') or shutil.which('7zz') or shutil.which('7za')
                run_archive_tool([executable, 'x', '-so', '-spd', '-y', '--', str(path), member],
                                 stream, hasher.progress)
        return hasher.digest(output) if output.stat().st_size == size else None


def member_digests(path, selected, hasher):
    """Extract a solid archive once for all selected members of this mod."""
    if zipfile.is_zipfile(path):
        return {member: member_digest(path, member, size, hasher) for member, size in selected.items()}
    with tempfile.TemporaryDirectory(prefix='wabbajack-sources-') as directory:
        root = Path(directory)
        names = {member: package_path(member) for member in selected}
        selection = root / 'members.txt'
        selection.write_text(''.join(member + '\n' for member in names), encoding='utf-8')
        destination = root / 'files'
        executable = shutil.which('7z') or shutil.which('7zz') or shutil.which('7za')
        with tempfile.TemporaryFile() as log:
            try:
                if not executable:
                    raise FileNotFoundError('7-Zip is not available.')
                run_archive_tool([executable, 'x', '-y', '-spd', '-scsUTF-8', '-o' + str(destination),
                                  str(path.resolve()), '@' + str(selection)], log, hasher.progress, 180)
            except (FileNotFoundError, subprocess.CalledProcessError):
                entries = {entry['XADFileName'].replace('\\', '/').casefold(): entry
                           for entry in unar_entries(path, hasher.progress)}
                try:
                    indexes = [str(entries[member.replace('\\', '/').casefold()]['XADIndex']) for member in names]
                except KeyError as error:
                    raise ValueError('Archive readers disagree about a selected member.') from error
                run_archive_tool([shutil.which('unar'), '-q', '-f', '-D', '-nr', '-i', '-o', str(destination),
                                  str(path.resolve()), *indexes], log, hasher.progress, 180)
        result = {}
        for member, relative in names.items():
            output = destination / relative
            hasher.progress('Checking archive member: ' + member)
            result[member] = (hasher.digest(output) if output.is_file() and not output.is_symlink()
                              and output.stat().st_size == selected[member] else None)
        return result


def primary_archive(row, archives=()):
    installation = row.get('folder_meta', {}).get('installation_file', '').replace('\\', '/').rsplit('/', 1)[-1]
    return next((a for a in archives or row.get('source_archives', [])
                 if installation and a.get('Name', '').casefold() == installation.casefold()), None)


def preferred_source_candidates(row, archives=()):
    primary = primary_archive(row, archives)
    return [item for item in row.get('folder_files', [])
            if primary and directive_type(item.get('directive_type')) == 'FromArchive'
            and item.get('archive_hash') != primary.get('Hash') and item.get('hash')
            and isinstance(item.get('size'), int) and item['size'] >= 0]


def verify_preferred_sources(row, folder, archives=(), progress=lambda message: None, inspections=None):
    installation = row.get('folder_meta', {}).get('installation_file', '').replace('\\', '/').rsplit('/', 1)[-1]
    primary = primary_archive(row, archives)
    result = {'alternatives': {}, 'path': str(Path(folder) / installation), 'signature': None, 'message': ''}
    result['signature'] = signature(result['path'])
    if not primary or not folder or installation in ('.', '..'):
        return result
    path = Path(folder) / installation
    result['signature'] = signature(path)
    candidates = preferred_source_candidates(row, archives)
    if not candidates:
        return result
    if result['signature'] is None:
        result['message'] = 'Recorded mod archive not found in the downloads folder.'
        return result
    try:
        progress('Inspecting archive: ' + path.name)
        hasher = FileHasher(progress)
        try:
            inspections = {} if inspections is None else inspections
            cache_key = (str(path), result['signature'], primary.get('Hash'))
            inspection = inspections.get(cache_key)
            if inspection is None:
                inspection = {'members': archive_members(path, progress), 'digests': {}, 'verified': False}
                inspection['by_name'] = {}
                for key in inspection['members']:
                    inspection['by_name'].setdefault(key.rsplit('/', 1)[-1], []).append(key)
                inspections[cache_key] = inspection
            members = inspection['members']
            prefixes = {''}
            for item in row.get('folder_files', []):
                chain = item.get('archive_hash_path') or []
                relative = relative_target(item)
                if (item.get('archive_hash') == primary['Hash'] and len(chain) == 2
                        and directive_type(item.get('directive_type')) == 'FromArchive'):
                    source = chain[1].replace('\\', '/')
                    if source.casefold().endswith(relative.casefold()):
                        prefix = source[:-len(relative)]
                        if not prefix or prefix.endswith('/'):
                            prefixes.add(prefix)
            matched = []
            for item in candidates:
                progress('Checking source member: ' + relative_target(item))
                relative = relative_target(item)
                # FOMOD options can contain a complete destination tree even when
                # no previously selected file establishes that option's prefix.
                keys = {(prefix + relative).casefold() for prefix in prefixes}
                if '/' in relative:
                    keys.update(key for key in inspection['by_name'].get(relative.rsplit('/', 1)[-1].casefold(), [])
                                if key.endswith('/' + relative.casefold()))
                matches = sorted({members[key] for key in keys if key in members and members[key][1] == item['size']})
                if matches:
                    matched.append((item, matches))
            if matched:
                if not inspection['verified']:
                    if hasher.digest(path) != primary.get('Hash'):
                        raise ValueError('The local archive does not match the Wabbajack archive hash.')
                    inspection['verified'] = True
                needed = {member: size for _, matches in matched for member, size in matches
                          if member not in inspection['digests']}
                if needed:
                    inspection['digests'].update(member_digests(path, needed, hasher))
            for item, matches in matched:
                equivalent = [member for member, _ in matches if inspection['digests'][member] == item['hash']]
                if equivalent:
                    member = min(equivalent, key=lambda name: (name.count('/') + name.count('\\'), name.casefold()))
                    result['alternatives'][item['target_path']] = {'archive': primary, 'member': member}
        finally:
            hasher.cache.close()
        if signature(path) != result['signature']:
            raise ValueError('Archive changed during verification.')
    except SourceVerificationCancelled:
        raise
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError, zipfile.BadZipFile, UnicodeError) as error:
        result['alternatives'] = {}
        result['message'] = str(error)
    return result


def presentation_row(row, verification):
    """Return copies; exporters and raw package records keep their original mapping."""
    if not verification or not verification['alternatives'] or signature(verification['path']) != verification['signature']:
        return row
    files, preferred_archives = [], {}
    for original in row.get('folder_files', []):
        alternative = verification['alternatives'].get(original['target_path'])
        if not alternative:
            files.append(original)
            continue
        archive, member = alternative['archive'], alternative['member']
        preferred_archives[archive['Hash']] = archive
        recorded_archive = next((a for a in row.get('source_archives', []) if a.get('Hash') == original.get('archive_hash')), {})
        files.append(dict(original, recorded_file_info=original, recorded_archive=recorded_archive, preferred_source=alternative,
                          archive_hash=archive['Hash'], archive_path=member, archive_depth=2,
                          archive_hash_path=[archive['Hash'], member], generated_source_hashes=[archive['Hash']]))
    used = {h for item in files for h in [item.get('archive_hash')] + item.get('generated_source_hashes', [])}
    archives = {a['Hash']: a for a in row.get('source_archives', []) if a['Hash'] in used}
    archives.update(preferred_archives)
    total_size = sum(a.get('Size', 0) or 0 for a in archives.values())
    return dict(row, folder_files=files, source_archives=list(archives.values()),
                size_mb=f'{total_size / 1024**2:.1f}' if total_size else '')
