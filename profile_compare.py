"""Read-only comparison of authored Wabbajack mods with an Amethyst/MO2 profile."""
import argparse
import base64
from collections import Counter, defaultdict
import ctypes
import ctypes.util
from difflib import SequenceMatcher, unified_diff
import html
import json
import os
from pathlib import Path
import re
from urllib.parse import urlsplit
import zipfile

from hash_cache import HashCache, default_cache_path

from wabbajack_format import ComparisonError, directive_type, unverified_reason

def general_metadata(text, section="general"):
    """Read Qt/MO2 INIs without rejecting duplicate keys or description text."""
    current, values = "", {}
    for line in str(text).lstrip("\ufeff").splitlines():
        line = line.strip()
        if line.startswith("[") and line.endswith("]"):
            current = line[1:-1].casefold()
        elif current == section.casefold() and "=" in line and not line.startswith((";", "#")):
            key, value = line.split("=", 1)
            values[key.strip().casefold()] = value.strip().strip('"')
    return values


def mod_entries(text, strict=True):
    """Return MO2 display order, retaining disabled and unmanaged entries."""
    entries, seen = [], set()
    for line in str(text).lstrip("\ufeff").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        prefix = line[0] if line[0] in "+-*" else "+"
        if strict and line[0] not in "+-*":
            raise ComparisonError(f"Unexpected modlist line: {line[:100]}")
        name = line[1:].strip() if line[0] in "+-*" else line
        if (not name or name in (".", "..") or any(c in name for c in '/\\\x00\r\n')
                or (prefix != "*" and ":" in name)):
            raise ComparisonError(f"Unsafe mod folder name: {name!r}")
        key = name.casefold()
        if key in seen:
            raise ComparisonError(f"Duplicate modlist entry: {name}")
        seen.add(key)
        entries.append({"name": name, "key": key, "enabled": prefix != "-", "prefix": prefix,
                        "separator": key.endswith("_separator"), "unmanaged": prefix == "*"})
    return list(reversed(entries))




def safe_url(value):
    value = str(value or "").strip()
    try:
        parts = urlsplit(value)
        if parts.scheme.lower() in ("http", "https") and parts.netloc and not parts.username:
            return value
    except ValueError:
        pass
    return ""


GAMES = {"skyrimse": "skyrimspecialedition", "skyrimspecialedition": "skyrimspecialedition",
         "skyrim": "skyrim", "fallout4": "fallout4", "falloutnv": "newvegas",
         "falloutnewvegas": "newvegas", "newvegas": "newvegas", "fallout3": "fallout3",
         "oblivion": "oblivion", "starfield": "starfield",
         "enderalspecialedition": "enderalspecialedition"}


def game_key(value):
    key = re.sub(r"[^a-z0-9]", "", str(value).casefold())
    return GAMES.get(key, key)


def positive_id(value):
    value = str(value or "").strip()
    return str(int(value)) if value.isdigit() and int(value) > 0 else ""


def archive_basename(value):
    return str(value or "").replace("\\", "/").rsplit("/", 1)[-1].casefold()


def metadata(text, default_game=""):
    values = general_metadata(text)
    file_ids = {positive_id(value) for key, value in general_metadata(text, "installedfiles").items()
                if key.casefold().endswith("\\fileid") or key.casefold() == "fileid"} - {""}
    file_id = positive_id(values.get("fileid")) or (next(iter(file_ids)) if len(file_ids) == 1 else "")
    return {"version": values.get("version", ""), "mod_id": positive_id(values.get("modid")),
            "game": game_key(values.get("gamename") or default_game),
            "repository": values.get("repository", "").casefold(),
            "installation_file": values.get("installationfile", ""),
            "file_id": file_id,
            "url": safe_url(values.get("url") or values.get("nexusurl"))}


def nexus_identity(meta):
    if meta.get("repository") not in ("", "nexus"):
        return None
    if meta.get("game") and meta.get("mod_id"):
        return meta["game"], meta["mod_id"]
    return None


def source_links(meta):
    links = []
    identity = nexus_identity(meta)
    if identity and identity[0] in GAMES.values():
        page = f"https://www.nexusmods.com/{identity[0]}/mods/{identity[1]}"
        links.append(page)
        if meta.get("file_id"):
            links.append(f"{page}?tab=files&file_id={meta['file_id']}")
    if meta.get("url"):
        links.append(meta["url"])
    return links


def read_package(path, profile="", progress=lambda _: None):
    from package_model import PackageModel
    model = path if isinstance(path, PackageModel) else PackageModel(path, progress)
    path = model.path
    before = path.stat()
    if stat_signature(before) != model.signature:
        raise ComparisonError('Wabbajack changed since it was opened; open it again before comparing.')
    manifest, destinations, names = model.manifest, model.destinations, model.profiles
    if not names:
        raise ComparisonError("No authored profile modlist.txt found; downloads cannot be compared as installed mods.")
    if not profile and len(names) != 1:
        raise ComparisonError("Choose an authored profile: " + ", ".join(sorted(names.values())))
    selected = names.get(profile.casefold()) if profile else next(iter(names.values()))
    if not selected:
        raise ComparisonError(f"Profile {profile!r} is absent. Available: {', '.join(sorted(names.values()))}")
    def inline(directive):
        return model.read_text(directive)

    entries = mod_entries(inline(destinations[f"profiles/{selected}/modlist.txt".casefold()]))
    for order, entry in enumerate(entries, 1):
        entry['order'] = order
    records = {}
    for entry in entries:
        progress(f"Reading Wabbajack metadata: {entry['name']}")
        if entry["separator"] or entry["unmanaged"]:
            continue
        item = destinations.get(f"mods/{entry['key']}/meta.ini")
        record = dict(entry, meta=metadata(inline(item) if item else "", manifest.get("GameType", "")),
                      files={}, links=[], archive_hashes=[])
        records[entry["key"]] = record
    progress(f"Indexing {len(destinations):,} authored files…")
    for index, (key, directive) in enumerate(destinations.items()):
        if index % 256 == 0:
            progress(f"Indexing authored files {index:,}/{len(destinations):,}…")
        parts = key.split("/", 2)
        if len(parts) != 3 or parts[0] != "mods" or parts[1] not in records:
            continue
        record = records[parts[1]]
        if parts[2] != "meta.ini":
            record["files"][parts[2]] = {"hash": directive.get("Hash"), "size": directive.get("Size"),
                                         "type": directive_type(directive.get("$type"))}
        hashes = model.source_hashes(directive)
        for source_hash in hashes:
            if source_hash not in record["archive_hashes"]:
                record["archive_hashes"].append(source_hash)
    archives = {item.get("Hash"): item for item in manifest.get("Archives", [])}
    for record in records.values():
        record["links"] = source_links(record["meta"])
        for source_hash in record.pop("archive_hashes"):
            item = archives.get(source_hash, {})
            state, values = item.get("State", {}), general_metadata(item.get("Meta", ""))
            source_meta = metadata(item.get("Meta", ""), state.get("GameName", ""))
            source_meta.update({"mod_id": positive_id(state.get("ModID") or values.get("modid")),
                                "file_id": positive_id(state.get("FileID") or values.get("fileid")),
                                "url": safe_url(state.get("Url") or values.get("directurl"))})
            record["links"].extend(source_links(source_meta))
            # A contributed patch archive's ID/version is not the folder's identity.
            if archive_basename(record["meta"]["installation_file"]) == archive_basename(item.get("Name")):
                if nexus_identity(record["meta"]) == nexus_identity(source_meta):
                    record["meta"]["file_id"] = record["meta"]["file_id"] or source_meta["file_id"]
        record["links"] = list(dict.fromkeys(record["links"]))
    result = {"source": str(path), "name": manifest.get("Name", path.stem),
              "version": manifest.get("Version", ""), "profile": selected, "mods": records,
              "profiles": sorted(names.values()), "excluded_entries": len(entries) - len(records),
              "diagnostics": model.diagnostics, "coverage": model.overview()}
    if stat_signature(before) != stat_signature(path.stat()):
        raise ComparisonError("The Wabbajack changed while being read. Try again when it is idle.")
    return result


def stat_signature(stat):
    return stat.st_ino, stat.st_size, stat.st_mtime_ns


def read_json_object(path):
    result = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(result, dict):
        raise ComparisonError(f"Expected a JSON object in {path}")
    return result


def configured_path(value, base):
    value = str(value).replace("\\", "/")
    if value.startswith("@ByteArray(") and value.endswith(")"):
        value = value[11:-1]
    value = value.replace("%BASE_DIR%", str(base))
    if value.startswith("//"):
        value = "//" + re.sub(r"/+", "/", value.lstrip("/"))
    else:
        value = re.sub(r"/+", "/", value)
    if os.name != "nt" and re.match(r"^[a-zA-Z]:/", value):
        raise ComparisonError("MO2 uses a Windows path. Select its Linux mods folder with Mods folder / --mods-dir.")
    if "%" in value:
        raise ComparisonError("Unresolved MO2 path variable. Select the mods folder explicitly.")
    candidate = Path(value).expanduser()
    return candidate if candidate.is_absolute() else base / candidate


def resolve_mods_dir(profile, override=None, instance=None):
    if override:
        return Path(override).expanduser().resolve()
    settings = None
    if (profile / "profile_state.json").is_file():
        state = read_json_object(profile / "profile_state.json")
        settings = state.get("profile_settings")
        if settings is not None and not isinstance(settings, dict):
            raise ComparisonError("Invalid Amethyst profile_settings.")
    if settings is None and (profile / "profile_settings.json").is_file():
        settings = read_json_object(profile / "profile_settings.json")
    if settings is not None:
        specific = settings.get("profile_specific_mods", False)
        if not isinstance(specific, bool):
            raise ComparisonError("Invalid Amethyst profile_specific_mods flag.")
        if specific:
            private = profile / 'mods'
            if not private.is_dir() and settings.get('wabbajack_directory'):
                return Path(settings['wabbajack_directory']).expanduser() / 'root/mods'
            return private
        return profile.parent.parent / "mods"
    root = Path(instance).expanduser().resolve() if instance else profile.parent.parent
    ini = next((p for p in root.iterdir() if p.name.casefold() == "modorganizer.ini"), None) if root.is_dir() else None
    if ini:
        values = general_metadata(ini.read_text(encoding="utf-8-sig"), "settings")
        base = configured_path(values.get("base_directory") or str(root), root)
        return configured_path(values.get("mod_directory") or "%BASE_DIR%/mods", base).resolve()
    # A private mods directory is also used by simple standalone profile copies.
    return profile / "mods" if (profile / "mods").is_dir() else root / "mods"


def read_local_profile(path, mods_dir=None, instance=None, progress=lambda _: None):
    path = Path(path).expanduser().resolve()
    if path.is_file() and path.name.casefold() == "modlist.txt":
        path = path.parent
    modlist = path / "modlist.txt"
    if not modlist.is_file():
        raise ComparisonError("Select a profile folder containing modlist.txt, or that file itself.")
    root = resolve_mods_dir(path, mods_dir, instance)
    entries = mod_entries(modlist.read_text(encoding="utf-8-sig"))
    if not root.is_dir() and any(not e["separator"] and not e["unmanaged"] for e in entries):
        raise ComparisonError(f"Mods folder not found: {root}. Choose its location with Choose mods folder….")
    for order, entry in enumerate(entries, 1):
        entry['order'] = order
    folders = {}
    for folder in root.iterdir() if root.is_dir() else ():
        progress(f"Locating installed mod folder: {folder.name}")
        if folder.is_dir():
            key = folder.name.casefold()
            if key in folders:
                raise ComparisonError(f"Case-colliding mod directories: {folder.name}")
            folders[key] = folder
    records = {}
    for entry in entries:
        if entry["separator"] or entry["unmanaged"]:
            continue
        progress(f"Reading installed metadata: {entry['name']}")
        folder = folders.get(entry["key"])
        meta = folder / "meta.ini" if folder else None
        values = metadata(meta.read_text(encoding="utf-8-sig", errors="replace") if meta and meta.is_file() else "")
        records[entry["key"]] = dict(entry, meta=values, present=folder is not None,
                                      folder=str(folder or root / entry["name"]), links=source_links(values))
    return {"source": str(path), "mods_dir": str(root), "mods": records,
            "excluded_entries": len(entries) - len(records)}


class FileHasher:
    """Wabbajack xxHash64 (seed 0), serialized as little-endian base64."""
    def __init__(self, progress=lambda _: None, cache_path=None):
        self.progress = progress
        self.cache = HashCache(cache_path)
        self.cache_hits = self.bytes_read = 0
        library = ctypes.util.find_library("xxhash")
        if not library:
            self.cache.close()
            raise ComparisonError("File verification needs the system libxxhash library; metadata comparison works without it.")
        self.lib = ctypes.CDLL(library)
        self.lib.XXH64_createState.restype = ctypes.c_void_p
        self.lib.XXH64_reset.argtypes = [ctypes.c_void_p, ctypes.c_ulonglong]
        self.lib.XXH64_update.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t]
        self.lib.XXH64_digest.argtypes = [ctypes.c_void_p]
        self.lib.XXH64_digest.restype = ctypes.c_ulonglong
        self.lib.XXH64_freeState.argtypes = [ctypes.c_void_p]

    def digest(self, path):
        self.progress('Checking installed file: ' + path.name)
        before = path.stat()
        cached = self.cache.get(path, before)
        if cached is not None:
            if HashCache.signature(before) != HashCache.signature(path.stat()):
                raise ComparisonError(f'File changed during verification: {path}')
            self.cache_hits += 1
            return cached
        state = self.lib.XXH64_createState()
        if not state:
            raise ComparisonError("Cannot allocate hashing state.")
        try:
            if self.lib.XXH64_reset(state, 0):
                raise ComparisonError("Cannot initialize hashing state.")
            with path.open("rb") as stream:
                if stat_signature(before) != stat_signature(os.fstat(stream.fileno())):
                    raise ComparisonError(f'File changed during verification: {path}')
                while True:
                    chunk = stream.read(1024**2)
                    if not chunk:
                        break
                    self.bytes_read += len(chunk)
                    self.progress(f'Checking {path.name}: {self.bytes_read / 1024**2:,.0f} MiB read; {self.cache_hits:,} cached hashes reused')
                    if self.lib.XXH64_update(state, chunk, len(chunk)):
                        raise ComparisonError(f"Hashing failed: {path}")
            value = self.lib.XXH64_digest(state)
        finally:
            self.lib.XXH64_freeState(state)
        after = path.stat()
        if stat_signature(before) != stat_signature(after) or HashCache.signature(before) != HashCache.signature(after):
            raise ComparisonError(f"File changed during verification: {path}")
        digest = base64.b64encode(value.to_bytes(8, "little")).decode()
        self.cache.put(path, before, digest)
        return digest

    def close(self):
        self.cache.close()


def verify_content(package, local, hasher, progress=lambda _: None):
    expected, actual, differences = package["files"], {}, []
    root = Path(local["folder"])
    def walk_error(error):
        raise error

    for parent, dirs, files in os.walk(root, onerror=walk_error):
        progress(f"Scanning installed folder: {parent}")
        for name in dirs + files:
            progress(f"Scanning {local['name']}: {name}")
            candidate = Path(parent) / name
            if candidate.is_symlink():
                raise ComparisonError(f"Cannot verify symlinked content: {candidate}")
        for name in files:
            file = Path(parent) / name
            key = file.relative_to(root).as_posix().casefold()
            if key == "meta.ini":
                continue
            if key in actual:
                raise ComparisonError(f"Case-colliding files: {file}")
            actual[key] = file
    checked, unverifiable = 0, 0
    for key in sorted(expected.keys() | actual.keys()):
        progress(f"Checking {local['name']}: {key}")
        spec, file = expected.get(key), actual.get(key)
        if spec is None:
            differences.append({"path": key, "status": "Local file only"})
        elif file is None:
            differences.append({"path": key, "status": "Missing file"})
        elif unverified_reason(spec):
            unverifiable += 1
            differences.append({"path": key, "status": "Unverified file", "type": spec["type"],
                                "reason": unverified_reason(spec)})
        elif isinstance(spec.get('size'), int) and file.stat().st_size != spec['size']:
            differences.append({'path': key, 'status': 'Content differs', 'reason': 'File size differs',
                                'package_size': spec['size'], 'local_size': file.stat().st_size})
        else:
            progress(f"Verifying {package['name']}: {key}")
            digest = hasher.digest(file)
            checked += 1
            if digest != spec["hash"]:
                differences.append({"path": key, "status": "Content differs", "package_hash": spec["hash"],
                                    "local_hash": digest})
    differs = any(d["status"] != "Unverified file" for d in differences)
    status = "Files differ" if differs else "Files unverified" if unverifiable or not expected else "Files match"
    return {"status": status, "checked": checked, "expected": len(expected), "unverifiable": unverifiable, "differences": differences}


def match_records(package, local):
    """Exact folder, then reciprocal unique identities. Never greedily pair variants."""
    left, right = package["mods"], local["mods"]
    matches = {key: (key, "Folder name") for key in left.keys() & right.keys()}
    used = {key for key, _ in matches.values()}
    for selector, label in (
        (lambda m: (nexus_identity(m), m.get("file_id")) if nexus_identity(m) and m.get("file_id") else None, "Nexus mod and file ID"),
        (lambda m: (nexus_identity(m), archive_basename(m.get("installation_file")))
         if nexus_identity(m) and m.get("installation_file") else None, "Nexus mod and archive name"),
        (lambda m: (nexus_identity(m), m['version'].strip().casefold())
         if nexus_identity(m) and m.get('version') else None, 'Nexus mod and version'),
        (nexus_identity, "Unique Nexus mod ID"),
    ):
        a, b = defaultdict(list), defaultdict(list)
        # Include already matched records in the uniqueness check: one Nexus mod may have many variants.
        for key, record in left.items():
            identity = selector(record["meta"])
            if identity:
                a[identity].append(key)
        for key, record in right.items():
            identity = selector(record["meta"])
            if identity:
                b[identity].append(key)
        for identity in a.keys() & b.keys():
            if len(a[identity]) == len(b[identity]) == 1:
                pkey, lkey = a[identity][0], b[identity][0]
                if pkey not in matches and lkey not in used:
                    matches[pkey] = lkey, label
                    used.add(lkey)
    return matches, used


def compare_inventories(package, local, verify_files=False, progress=lambda _: None):
    matches, used = match_records(package, local)
    hasher = FileHasher(progress, cache_path=default_cache_path()) if verify_files else None
    try:
        rows = []
        matched_local_order = [key for key in local["mods"] if key in used]
        package_order = [matches[key][0] for key in package["mods"] if key in matches]
        stable = set()
        for block in SequenceMatcher(None, package_order, matched_local_order, autojunk=False).get_matching_blocks():
            stable.update(package_order[block.a:block.a + block.size])
        for mod_index, (key, expected) in enumerate(package["mods"].items(), 1):
            progress(f"Comparing mod {mod_index:,}/{len(package['mods']):,}: {expected['name']}")
            pair = matches.get(key)
            current = local["mods"].get(pair[0]) if pair else None
            candidates = []
            identity = nexus_identity(expected["meta"])
            if not current and identity:
                candidates = [r["name"] for r in local["mods"].values() if nexus_identity(r["meta"]) == identity]
            differences, unknown = [], []
            if current:
                a, b = expected["meta"], current["meta"]
                for field in ("version", "mod_id", "game", "installation_file", "file_id", "repository"):
                    old, new = a.get(field, ""), b.get(field, "")
                    norm = archive_basename if field == "installation_file" else lambda v: str(v).strip().casefold()
                    if old and new and norm(old) != norm(new):
                        differences.append({"field": field, "package": old, "local": new})
                    elif field in ("version", "mod_id", "installation_file") and (not old or not new):
                        unknown.append(field)
                fields = {d["field"] for d in differences}
                if not current["present"]:
                    status = "Missing folder"
                elif fields & {"mod_id", "game", "repository"}:
                    status = "Identity differs"
                elif "version" in fields:
                    status = "Version differs"
                elif fields:
                    status = "File selection differs"
                elif not a.get("version") or not b.get("version"):
                    status = "Version unknown"
                else:
                    status = "Metadata matches"
            else:
                status = "Ambiguous match" if candidates else "Package only"
            row = {"status": status, "package_name": expected["name"], "local_name": current["name"] if current else "",
                   "package_version": expected["meta"]["version"], "local_version": current["meta"]["version"] if current else "",
                   "package_order": expected.get('order'), "local_order": current.get('order') if current else None,
                   "local_folder": current.get('folder', '') if current else '',
                   "package_enabled": expected["enabled"], "local_enabled": current["enabled"] if current else None,
                   "local_present": current["present"] if current else False,
                   "match_basis": pair[1] if pair else "", "candidates": candidates, "differences": differences,
                   "unknown_metadata": unknown, "package_meta": expected["meta"], "local_meta": current["meta"] if current else {},
                   "enabled_differs": bool(current and expected["enabled"] != current["enabled"]),
                   "order_differs": bool(current and pair[0] not in stable),
                   "unused_package_mod": not current or not current["present"] or not current["enabled"],
                   "links": expected["links"], "content": {"status": "Not checked"}}
            if hasher and current and current["present"]:
                row["content"] = verify_content(expected, current, hasher, progress)
            rows.append(row)
        for key, record in local["mods"].items():
            if key not in used:
                candidates = [r["name"] for r in package["mods"].values()
                              if nexus_identity(record["meta"]) and nexus_identity(r["meta"]) == nexus_identity(record["meta"])]
                rows.append({"status": "Ambiguous match" if candidates else "Local only", "package_name": "",
                             "local_name": record["name"], "package_version": "", "local_version": record["meta"]["version"],
                             "package_order": None, "local_order": record.get('order'), "local_folder": record.get('folder', ''),
                             "package_enabled": None, "local_enabled": record["enabled"], "local_present": record["present"],
                             "match_basis": "", "candidates": candidates, "differences": [], "unknown_metadata": [],
                             "package_meta": {}, "local_meta": record["meta"], "enabled_differs": False,
                             "order_differs": False, "unused_package_mod": False, "links": record["links"],
                             "content": {"status": "Not checked"}})
        return {"schema": 1, "package": {k: v for k, v in package.items() if k != "mods"},
                "local": {k: v for k, v in local.items() if k != "mods"}, "file_verification": verify_files,
                "verification_stats": {"bytes_read": hasher.bytes_read if hasher and isinstance(hasher.bytes_read, int) else 0,
                                       "cached_files": hasher.cache_hits if hasher and isinstance(hasher.cache_hits, int) else 0},
                "summary": dict(Counter(r["status"] for r in rows)), "rows": rows,
                "content_summary": dict(Counter(r["content"]["status"] for r in rows)),
                "notes": ["Metadata matches do not prove identical files. Versions are compared as recorded; newer/older is not inferred.",
                          "Only mods listed in the selected profiles are compared. Separators and unmanaged (*) game/DLC entries are excluded.",
                          "Disabled entries are included and marked. Wabbajack only means absent from this profile, not a newly published mod.",
                          "Ambiguous Nexus variants require manual review. A shared mod ID alone does not identify a particular optional file.",
                          "File verification reuses cached hashes only while device, inode, size, modification and change times match; otherwise bytes are read again. It reads matched mod folders only, excluding meta.ini. Generated archives, remapped files, transformed textures and unsupported directives remain unverified, even when the manifest records a hash. Installer adjustments can legitimately differ from authored bytes.",
                          "Game roots, overwrite, tools, plugins and resolved conflict winners are outside this mod-folder comparison."]}
    finally:
        if hasher:
            hasher.close()


def compare_profile_files(model, profile, local, progress=lambda _: None, limit=512 * 1024):
    """Compare authored profile text to installed configuration, with bounded previews."""
    root = Path(local)
    prefix = f'profiles/{profile}/'.casefold()
    list_files = {'modlist.txt', 'plugins.txt', 'loadorder.txt', 'lockedorder.txt', 'archives.txt', 'plugingroups.txt'}
    def relevant(key):
        # Compare active settings, excluding compiler backups and crash logs.
        return '/' not in key and (key in list_files or key.endswith('.ini'))
    authored = {key[len(prefix):]: item for key, item in model.destinations.items()
                if key.startswith(prefix) and relevant(key[len(prefix):])}
    installed = {}
    for path in root.iterdir():
        if path.is_file() and relevant(path.name.casefold()):
            installed[path.name.casefold()] = path
    ini = root / 'ini files'
    if ini.is_dir():
        for path in ini.iterdir():
            if path.is_file() and path.suffix.casefold() == '.ini':
                installed.setdefault(path.name.casefold(), path)
    rows = []
    for key in sorted(authored.keys() | installed.keys()):
        progress(f'Comparing profile file: {key}')
        item, path = authored.get(key), installed.get(key)
        if path is None and (root / key).is_file():
            path = root / key
        row = {'path': key, 'local_path': str(path) if path else '', 'status': '', 'diff': ''}
        try:
            if item and directive_type(item.get('$type')) != 'InlineFile':
                row.update(status='Unverified', diff='This file is generated or remapped during installation.')
            elif path and (path.stat().st_size > limit or path.is_symlink() or not path.resolve().is_relative_to(root.resolve())):
                row.update(status='Not previewed', diff='Installed file is a symlink or exceeds the text preview limit.')
            else:
                old = model.read_text(item, limit=limit) if item else ''
                new = path.read_text(encoding='utf-8-sig', errors='replace') if path else ''
                old, new = old.lstrip('\ufeff').replace('\r\n', '\n'), new.replace('\r\n', '\n')
                if '\x00' in old or '\x00' in new:
                    raise ComparisonError('Binary profile file; text comparison is unavailable.')
                row['status'] = 'Installed only' if not item else 'Wabbajack only' if not path else 'Same text' if old == new else 'Text differs'
                if old != new:
                    row['diff'] = ''.join(unified_diff(old.splitlines(keepends=True), new.splitlines(keepends=True),
                                                      fromfile='Wabbajack/' + key, tofile='Installed/' + key))
        except (OSError, ComparisonError) as error:
            row.update(status='Not previewed', diff=str(error))
        rows.append(row)
    return rows


def compare_paths(package, local, profile="", mods_dir=None, instance=None, verify_files=False, progress=lambda _: None):
    from package_model import PackageModel
    model = package if isinstance(package, PackageModel) else PackageModel(package, progress)
    authored = read_package(model, profile, progress)
    installed = read_local_profile(local, mods_dir, instance, progress)
    report = compare_inventories(authored, installed, verify_files, progress)
    report['profile_files'] = compare_profile_files(model, authored['profile'], installed['source'], progress)
    return report


def write_report(report, path):
    """Export a complete report; values from packages are always HTML-escaped."""
    path = Path(path)
    if path.suffix.casefold() == ".json":
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return
    escape = lambda value: html.escape(str(value), quote=True)
    rows = []
    for row in report["rows"]:
        cells = [row["status"].replace('Package only', 'Wabbajack only').replace('Local only', 'Installed only'), row["package_name"], row["local_name"], row["package_version"], row["local_version"],
                 row.get('package_order') or '—', row.get('local_order') or '—',
                 enabled_label(row["package_enabled"]), enabled_label(row["local_enabled"]), row["content"]["status"]]
        details = escape(json.dumps(row, ensure_ascii=False, indent=2))
        rows.append("<tr>" + "".join(f"<td>{escape(c)}</td>" for c in cells)
                    + f"<td><details><summary>Details</summary><pre>{details}</pre></details></td></tr>")
    title = escape(f"{report['package']['name']} {report['package']['version']} — {report['local']['source']}")
    summary = {key.replace('Package only', 'Wabbajack only').replace('Local only', 'Installed only'): count
               for key, count in report['summary'].items()}
    profile_files = '<h2>Profile files</h2>' + ''.join(f"<details><summary>{escape(r['path'])}: {escape(r['status'])}</summary><pre>{escape(r['diff'])}</pre></details>" for r in report.get('profile_files', []))
    path.write_text('<!doctype html><html lang="en"><meta charset="utf-8"><title>Mod comparison</title>'
                    '<style>body{font:15px system-ui;margin:24px}table{border-collapse:collapse;width:100%}'
                    'td,th{border:1px solid #aaa;padding:6px;text-align:left}pre{white-space:pre-wrap;max-width:70ch}</style>'
                    f'<h1>{title}</h1><p>Metadata: {escape(summary)}</p>'
                    f'<p>Content: {escape(report.get("content_summary", {}))}</p><ul>'
                    + "".join(f"<li>{escape(n)}</li>" for n in report["notes"])
                    + '</ul><table><thead><tr>' + "".join(f"<th>{h}</th>" for h in
                      ("Result", "Wabbajack mod", "Installed mod", "Wabbajack version", "Installed version", 'Wabbajack #', 'Installed #', "Wabbajack enabled", "Installed enabled", "Files", "Details"))
                    + '</tr></thead><tbody>' + "".join(rows) + '</tbody></table>' + profile_files + '</html>', encoding="utf-8")


def enabled_label(value):
    return "—" if value is None else "Yes" if value else "No"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package", type=Path)
    parser.add_argument("local", type=Path, help="Amethyst/MO2 profile folder or modlist.txt")
    parser.add_argument("--profile", default="", help="Authored profile inside the Wabbajack")
    parser.add_argument("--mods-dir", type=Path)
    parser.add_argument("--instance", type=Path, help="MO2 instance containing ModOrganizer.ini, for custom profile paths")
    parser.add_argument("--verify-files", action="store_true", help="Read and hash matched mod files (can be slow)")
    parser.add_argument("--output", type=Path, help="Write an HTML or JSON report")
    args = parser.parse_args(argv)
    try:
        report = compare_paths(args.package, args.local, args.profile, args.mods_dir, args.instance, args.verify_files)
        if args.output:
            write_report(report, args.output)
        print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    except (ComparisonError, OSError, ValueError, zipfile.BadZipFile, KeyError) as error:
        parser.exit(1, f"Comparison failed: {error}\n")


if __name__ == "__main__":
    main()
