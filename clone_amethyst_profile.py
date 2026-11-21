#!/usr/bin/env python3
"""Clone an Amethyst profile into an independent ordinary profile.

Dry-run unless --apply is supplied. Only Python's standard library is needed.
Supports Wabbajack, ordinary profile-specific, and shared-mod profiles. The
game copy lives outside profiles/, as required by Amethyst's path validator.
Close Amethyst and restore the source game before applying a clone.
"""
from __future__ import annotations

import argparse
import ctypes
import errno
try:
    import fcntl
except ImportError:
    fcntl = None
import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

FICLONE = 0x40049409
GENERATED = {"Root_Backup", "backups", ".standalone-copy", ".amethyst-vfs"}
PROFILE_FILES = ("modlist.txt", "plugins.txt", "loadorder.txt", "archives.txt",
                 "lockedorder.txt", "exe_args.json")
PROFILE_DIRS = ("Root_Folder", "overwrite", "ini files", "fomod", "tools", "explorer++")
TEXT_SUFFIXES = {".ini", ".json", ".xml", ".cfg", ".toml", ".yaml", ".yml",
                 ".bat", ".cmd", ".ps1", ".py", ".sh", ".config", ".csp"}
IDENTITY_KEYS = {"original_default", "profile_locked", "hide_from_profile_dropdown"}


class CloneError(Exception):
    pass


def read_json(path: Path, default=None):
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return {} if default is None else default
    except (OSError, ValueError) as exc:
        raise CloneError(f"Cannot read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise CloneError(f"Expected a JSON object: {path}")
    return value


def overlap(a: Path, b: Path) -> bool:
    a, b = a.resolve(), b.resolve()
    return a == b or a in b.parents or b in a.parents


def valid_name(name: str) -> str:
    if (not name or name != name.strip() or name.casefold() == "default"
            or name.startswith(".") or name.endswith('.') or any(c in name for c in '/\\:*?"<>|')
            or any(ord(c) < 32 or ord(c) == 127 for c in name)):
        raise CloneError("Use a new profile name without path separators or control characters (not 'default').")
    return name


def validate_destination_name(profiles: Path, name: str):
    valid_name(name)
    if any(path.name.casefold() == name.casefold() for path in profiles.iterdir()):
        raise CloneError(f'A profile with that name already exists (ignoring case): {name}')


def discover_profile(source: Path, config_dir=None):
    """Read saved paths and distinguish a per-profile game root from the shared game."""
    from profile_locations import amethyst_config_folder
    source = Path(source).expanduser().absolute()
    config_base = Path(config_dir or amethyst_config_folder()).expanduser().resolve()
    config = config_base / 'games' / source.parent.parent.name
    paths = read_json(config / 'paths.json')
    state = read_state(source)
    settings = state['profile_settings']
    wj = Path(settings['wabbajack_directory']).expanduser().resolve() / 'root' if settings.get('wabbajack_directory') else None
    raw_private = settings.get('game_path')
    if not raw_private and wj and (wj / 'Game Root').is_dir():
        raw_private = str(wj / 'Game Root')
    raw_game = raw_private or paths.get('game_path')
    return dict(state=state, config=config, config_base=config_base, paths=paths, wj=wj,
                private_root=bool(raw_private), game=Path(raw_game).expanduser().resolve() if raw_game else None)


def wine(path: Path) -> str:
    return "Z:" + str(path).replace("/", "\\")


def running_amethyst() -> list[int]:
    result = []
    for proc in Path("/proc").glob("[0-9]*"):
        try:
            args = (proc / "cmdline").read_bytes().split(b"\0")[:2]
            if any(b"/share/amethyst-mod-manager/" in arg or
                   b"AmethystModManager" in Path(os.fsdecode(arg)).name.encode()
                   for arg in args if arg):
                result.append(int(proc.name))
        except OSError:
            pass
    return result


def infer_staging() -> Path:
    for parent in Path(__file__).resolve().parents:
        if parent.name in {"profiles", "Applications"} and (parent.parent / 'profiles').is_dir():
            return parent.parent
    raise CloneError("Supply --staging with the game's Amethyst staging folder.")


def read_state(source: Path) -> dict:
    state = read_json(source / "profile_state.json")
    # Read legacy files without calling Amethyst's migration (which writes).
    legacy = {"profile_settings": "profile_settings.json",
              "collapsed_seps": "collapsed_seps.json",
              "separator_locks": "separator_locks.json",
              "separator_colors": "separator_colors.json",
              "separator_deploy_paths": "separator_deploy_paths.json",
              "root_folder_state": "root_folder_state.json",
              "mod_strip_prefixes": "mod_strip_prefixes.json",
              "plugin_locks": "plugin_locks.json",
              "disabled_plugins": "disabled_plugins.json",
              "excluded_mod_files": "excluded_mod_files.json"}
    for key, filename in legacy.items():
        path = source / filename
        if key not in state and path.is_file():
            try:
                state[key] = json.loads(path.read_text(encoding="utf-8-sig"))
            except (ValueError, OSError) as exc:
                raise CloneError(f"Cannot read legacy state {path}: {exc}") from exc
    state.setdefault("profile_settings", {})
    if not isinstance(state["profile_settings"], dict):
        raise CloneError("Invalid profile_settings.")
    return state


@dataclass
class Job:
    source: Path
    target: Path


@dataclass
class Plan:
    staging: Path
    source: Path
    destination: Path
    source_game: Path | None
    game: Path | None
    config: Path
    state: dict
    jobs: list[Job] = field(default_factory=list)
    mappings: list[tuple[Path, Path]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    external_tools: list[str] = field(default_factory=list)
    dropped_tools: list[str] = field(default_factory=list)
    files: int = 0
    bytes: int = 0
    authored_name: str = ""
    authored_roots: list[str] = field(default_factory=list)
    authored_game_subpath: str = ""

    @property
    def runtime_game(self):
        return self.game or self.source_game

    def mapped_path(self, path: Path) -> Path | None:
        path = path.resolve()
        for old, new in sorted(self.mappings, key=lambda item: len(str(item[0])), reverse=True):
            if path == old or path.is_relative_to(old):
                return new / path.relative_to(old)
        return None

    def preview(self) -> dict:
        return {"mode": "dry-run", "source_profile": str(self.source),
                "destination_profile": str(self.destination),
                "source_game": str(self.source_game) if self.source_game else None,
                "destination_game": str(self.game) if self.game else None,
                "copies_game_root": self.game is not None,
                "files_to_copy": self.files, "logical_GiB": round(self.bytes / 1024**3, 2),
                "wabbajack_association": "removed", "external_tools_kept": self.external_tools,
                "authored_windows_roots_to_rebase": self.authored_roots,
                "warnings": self.warnings,
                "copies": [{"source": str(job.source), "target": str(job.target)}
                           for job in self.jobs if not job.target.is_relative_to(self.destination / "mods")]}


def build_plan(args) -> Plan:
    staging = (Path(args.staging).expanduser() if args.staging else infer_staging()).resolve()
    source_arg = Path(args.source).expanduser()
    source = (source_arg if source_arg.is_absolute() else staging / "profiles" / source_arg).absolute()
    if source.is_symlink() or not source.is_dir() or source.parent.resolve() != staging / "profiles":
        raise CloneError("Source must be a real profile directory in this game's profiles/ folder.")
    name = valid_name(args.name)
    destination = staging / "profiles" / name
    validate_destination_name(destination.parent, name)
    discovered = discover_profile(source, args.config_dir)
    state, config_base, config, global_paths = (discovered[key] for key in ('state', 'config_base', 'config', 'paths'))
    copy_game = discovered['private_root'] or bool(args.game_root or args.game_root_source)
    game = (Path(args.game_root).expanduser() if args.game_root else staging.parent / f"{name} Game Root").absolute() if copy_game else None
    for path in [destination, *([game] if game else [])]:
        if os.path.lexists(path):
            raise CloneError(f"Destination already exists; nothing will be overwritten: {path}")
        if not path.parent.is_dir():
            raise CloneError(f"Destination parent must already exist: {path.parent}")
    settings = state["profile_settings"]
    if not isinstance(settings.get('profile_specific_mods', False), bool):
        raise CloneError('Invalid profile_specific_mods flag in profile_state.json.')
    if settings.get("is_group"):
        raise CloneError("Select a member profile rather than a profile group.")
    wj_root = discovered['wj']
    source_game = Path(args.game_root_source).expanduser().resolve() if args.game_root_source else discovered['game']
    if copy_game and source_game is None:
        raise CloneError('No source root folder was found in the profile settings.')
    if copy_game and not source_game.is_dir():
        raise CloneError(f"Source game folder does not exist: {source_game}")
    protected = [staging / item for item in ("mods", "profiles", "overwrite", "Root_Folder", "Root_Backup")]
    if game and (any(overlap(game, path) for path in protected) or overlap(game, staging)):
        raise CloneError("Place --game-root outside the Amethyst staging folder and profiles/ tree.")
    if source_game and ((game and overlap(game, source_game)) or overlap(destination, source_game)):
        raise CloneError("Destination storage must be separate from the source game.")
    deploy = read_json(config / "deploy_state.json")
    context_game = deploy.get("recovery_context", {}).get("game_root", {}).get("path")
    deployed_game = context_game or global_paths.get("game_path")
    if deploy.get("deploy_active") and (deploy.get("last_deployed") == source.name or
            (source_game and deployed_game and Path(deployed_game).resolve() == source_game)):
        raise CloneError("Restore/undeploy the source game in Amethyst before cloning it.")
    if copy_game and ((source_game / "Data/.mm_deployed").exists() or (source_game / ".mm_deployed").exists()):
        raise CloneError("The source game has a deployment marker. Restore it in Amethyst first.")
    # A shared default prefix is intentional; deployment/index state is never copied.
    mod_root = ((source / "mods") if settings.get("profile_specific_mods") else staging / "mods").resolve()
    if settings.get('profile_specific_mods') and not mod_root.is_dir() and wj_root:
        mod_root = wj_root / 'mods'
    if not mod_root.is_dir():
        from profile_compare import mod_entries
        modlist = source / 'modlist.txt'
        entries = mod_entries(modlist.read_text(encoding='utf-8-sig')) if modlist.is_file() else []
        if any(not entry['separator'] and not entry['unmanaged'] for entry in entries):
            raise CloneError(f"Source mod store is missing: {mod_root}")
    plan = Plan(staging, source, destination, source_game, game, config, state)
    plan.mappings = [(source, destination), (mod_root, destination / "mods")]
    if game:
        plan.mappings.append((source_game, game))
    plan.authored_name = str(settings.get("wabbajack_profile") or "")
    wj = wj_root
    if wj:
        plan.mappings.append((wj, destination))
        plan.mappings.append((wj / "profiles" / (plan.authored_name or source.name), destination))
        if source_game and source_game.is_relative_to(wj):
            plan.authored_game_subpath = source_game.relative_to(wj).as_posix()
        # The authored profile can be named "Performance" while the installation
        # folder is named after the list. Read the list title without modifying
        # its installation database or inheriting its Wabbajack identity.
        authored_names = {plan.authored_name} if plan.authored_name else set()
        database = wj.parent / 'state.sqlite'
        if database.is_file():
            try:
                connection = sqlite3.connect(database.as_uri() + '?mode=ro&immutable=1', uri=True)
                try:
                    row = connection.execute("SELECT value FROM metadata WHERE key='name'").fetchone()
                finally:
                    connection.close()
                if row:
                    title = json.loads(row[0])
                    if isinstance(title, str):
                        authored_names.add(title)
            except (sqlite3.Error, ValueError) as exc:
                plan.warnings.append(f'Could not infer the authored list folder name: {exc}. Use --authored-root if needed.')
        for title in sorted(authored_names):
            if title and not any(char in title for char in '/\\:*?"<>|') and not any(ord(char) < 32 for char in title):
                plan.authored_roots.extend(drive + ':\\' + title for drive in 'ABCDEFGHIJKLMNOPQRSTUVWXY')
    for root in args.authored_root:
        normalized = root.replace('/', '\\').rstrip('\\')
        if not re.fullmatch(r'[A-Za-z]:\\[^\r\n]+', normalized):
            raise CloneError(f'--authored-root must be an absolute Windows folder, for example D:\\ListName: {root}')
        plan.authored_roots.append(normalized)
    plan.authored_roots = sorted(set(plan.authored_roots))

    def add(old: Path, new: Path):
        if not old.exists():
            return
        old = old.resolve()
        if any(new == job.target for job in plan.jobs):
            return
        if any(new.is_relative_to(job.target) for job in plan.jobs if job.source.is_dir()):
            return
        plan.jobs.append(Job(old, new))
        plan.mappings.append((old, new))

    if game:
        add(source_game, game)
    if settings.get("profile_specific_mods"):
        add(mod_root, destination / "mods")
    else:
        names = set()
        modlist = source / "modlist.txt"
        for line in modlist.read_text(encoding="utf-8-sig").splitlines() if modlist.exists() else []:
            if not line.startswith(("+", "-")):
                continue
            mod = line[1:].strip()
            if not mod or mod in {".", ".."} or any(c in mod for c in '/\\'):
                raise CloneError(f"Unsafe mod name in modlist.txt: {mod!r}")
            if mod in names:
                continue
            names.add(mod)
            folder = mod_root / mod
            if not folder.is_dir():
                if mod.endswith("_separator"):
                    continue
                raise CloneError(f"Listed mod folder is missing: {folder}")
            add(folder, destination / "mods" / mod)
    for name in PROFILE_FILES:
        add(source / name, destination / name)
    for name in (*PROFILE_DIRS, *(() if args.no_saves else ("Saves",))):
        old = staging / name if name in {'Root_Folder', 'overwrite'} and not settings.get('profile_specific_mods') else source / name
        if not old.exists() and wj and name in {"tools", "explorer++"}:
            old = wj / name
        add(old, destination / name)

    for executable in state.get("custom_exes", []):
        old = Path(executable).expanduser()
        if not old.is_file():
            plan.dropped_tools.append(executable)
            plan.warnings.append(f"Omitting missing custom executable: {executable}")
            continue
        resolved = old.resolve()
        mapped = plan.mapped_path(resolved)
        if mapped is not None:
            if not any(mapped == job.target or mapped.is_relative_to(job.target) for job in plan.jobs):
                owner = wj if wj and resolved.is_relative_to(wj) else source
                if resolved.is_relative_to(owner):
                    top = resolved.relative_to(owner).parts[0]
                    if top in GENERATED or top in {"mods", "Game Root"}:
                        raise CloneError(f"Cannot infer custom tool ownership: {old}")
                    add(owner / top, destination / top)
        elif args.copy_external_tools:
            parent = resolved.parent
            if parent in {Path('/'), Path.home(), staging, staging.parent, config_base, source}:
                raise CloneError(f"Refusing to copy a broad folder as an external tool: {parent}")
            suffix = hashlib.sha256(str(parent).encode()).hexdigest()[:8]
            add(parent, destination / "tools" / f"{parent.name}-{suffix}")
        else:
            plan.external_tools.append(str(old))
    if plan.external_tools:
        plan.warnings.append("External applications remain shared. Use --copy-external-tools to copy their containing directories too.")
    if running_amethyst():
        plan.warnings.append("Amethyst is running; close it before using --apply.")
    inspect_sources(plan)
    return plan


def inspect_sources(plan: Plan):
    for job in plan.jobs:
        entries = [job.source]
        if job.source.is_dir():
            entries = (Path(parent) / name for parent, dirs, files in os.walk(job.source, followlinks=False)
                       for name in dirs + files)
        for path in entries:
            info = path.lstat()
            if stat.S_ISREG(info.st_mode):
                plan.files += 1
                plan.bytes += info.st_size
            elif stat.S_ISLNK(info.st_mode):
                target = path.resolve()
                mapped = plan.mapped_path(target)
                if not target.exists() or mapped is None or not any(
                        mapped == item.target or mapped.is_relative_to(item.target) for item in plan.jobs):
                    raise CloneError(f"External/broken symbolic link would prevent an independent copy: {path} -> {os.readlink(path)}")
            elif not stat.S_ISDIR(info.st_mode):
                raise CloneError(f"Cannot copy a special file: {path}")


class Rebaser:
    def __init__(self, plan: Plan):
        pairs = []
        for old, new in plan.mappings:
            pairs.append((str(old), str(new), False))
            for count in [4, 2, 1]:
                sep = "\\" * count
                pairs.append((str(old).replace('/', sep), str(new).replace('/', sep), True))
        for authored_root in plan.authored_roots:
            for count in [4, 2, 1]:
                sep = "\\" * count
                root = authored_root.replace('\\', sep)
                if plan.runtime_game:
                    game_subpaths = {plan.authored_game_subpath or plan.source_game.name, 'Game Root'}
                    for subpath in game_subpaths:
                        pairs.append((root + sep + subpath.replace('/', sep), wine(plan.runtime_game).replace('\\', sep), True))
                if plan.authored_name:
                    pairs.append((root + sep + 'profiles' + sep + plan.authored_name,
                                  wine(plan.destination).replace('\\', sep), True))
                pairs.append((root, wine(plan.destination).replace('\\', sep), True))
        self.patterns = [(re.compile(re.escape(old) + r'(?=$|[/\\\s\"\x27;,\)])', re.I if insensitive else 0), new)
                         for old, new, insensitive in sorted(set(pairs), key=lambda item: len(item[0]), reverse=True)]

    def text(self, value: str) -> str:
        for pattern, new in self.patterns:
            value = pattern.sub(lambda match: new, value)
        return value

    def value(self, value):
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, list):
            return [self.value(item) for item in value]
        if isinstance(value, dict):
            return {self.text(key): self.value(item) for key, item in value.items()}
        return value


def rename_new(old: Path, new: Path):
    """Atomic no-overwrite publish on Linux, including directory destinations."""
    libc = ctypes.CDLL(None, use_errno=True)
    rename = getattr(libc, "renameat2", None)
    if rename is None:
        raise CloneError("This Linux system does not provide renameat2; refusing a potentially overwriting publish.")
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(-100, os.fsencode(old), -100, os.fsencode(new), 1) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), str(new))


def apply_plan(plan: Plan, args, progress=print) -> dict:
    if fcntl is None:
        raise CloneError("Standalone Amethyst copying currently requires Linux.")
    if running_amethyst():
        raise CloneError("Close Amethyst before applying the clone.")
    # Recheck the source and destinations immediately before any write.
    fresh = build_plan(args)
    if fresh.source_game != plan.source_game or fresh.mappings != plan.mappings:
        raise CloneError("The source configuration changed; run the preview again.")
    if args.copy_mode == 'full':
        available = shutil.disk_usage(plan.destination.parent).free
        if plan.bytes > available:
            raise CloneError(f"Full copy requires {plan.bytes / 1024**3:.1f} GiB; only {available / 1024**3:.1f} GiB available.")
    stage_profile = Path(tempfile.mkdtemp(prefix='.profile-clone-', dir=plan.destination.parent))
    stage_game = None
    stages = {plan.destination: stage_profile}
    if plan.game:
        try:
            stage_game = Path(tempfile.mkdtemp(prefix='.game-clone-', dir=plan.game.parent))
        except BaseException:
            shutil.rmtree(stage_profile)
            raise
        stage_game.rmdir()  # copytree creates the game root with the original metadata.
        stages[plan.game] = stage_game
    published = []
    copied = reflinked = 0
    started = time.monotonic()
    changed = []

    def staged(path: Path) -> Path:
        for final, stage in stages.items():
            if path == final or path.is_relative_to(final):
                return stage / path.relative_to(final)
        raise CloneError(f"Write outside the two planned destinations: {path}")

    def copy_file(source, target):
        nonlocal copied, reflinked
        source, target = Path(source), Path(target)
        with source.open('rb') as src, target.open('xb') as dst:
            before = os.fstat(src.fileno())
            cloned = False
            if args.copy_mode != 'full':
                try:
                    fcntl.ioctl(dst.fileno(), FICLONE, src.fileno())
                    cloned = True
                    reflinked += 1
                except OSError as exc:
                    if args.copy_mode == 'reflink' or exc.errno not in {errno.EXDEV, errno.ENOTTY, errno.EOPNOTSUPP, errno.EINVAL}:
                        raise
            if not cloned:
                shutil.copyfileobj(src, dst, 1024 * 1024)
            after = os.fstat(src.fileno())
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise CloneError(f'Source file changed while copying: {source}')
            if os.fstat(dst.fileno()).st_size != before.st_size:
                # Buffered full copies must be flushed before checking their size.
                dst.flush()
                if os.fstat(dst.fileno()).st_size != before.st_size:
                    raise CloneError(f'Incomplete copied file: {source}')
        shutil.copystat(source, target)
        copied += 1
        if copied % 5000 == 0:
            progress(f'Copied {copied:,}/{plan.files:,} files ({time.monotonic() - started:.0f}s).')
        return str(target)

    def write_json(path: Path, value):
        path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')

    try:
        for job in plan.jobs:
            target = staged(job.target)
            target.parent.mkdir(parents=True, exist_ok=True)
            if job.source.is_dir():
                shutil.copytree(job.source, target, symlinks=True, copy_function=copy_file)
            else:
                copy_file(job.source, target)
        for name in ['mods', 'overwrite', 'Root_Folder']:
            (stage_profile / name).mkdir(exist_ok=True)
        # Rebase links without ever following them back into source storage.
        for job in plan.jobs:
            if not job.source.is_dir():
                continue
            for parent, dirs, files in os.walk(job.source, followlinks=False):
                for name in dirs + files:
                    source_link = Path(parent) / name
                    if source_link.is_symlink():
                        final_link = job.target / source_link.relative_to(job.source)
                        target = staged(final_link)
                        mapped = plan.mapped_path(source_link.resolve())
                        assert mapped is not None
                        target.unlink()
                        target.symlink_to(os.path.relpath(mapped, final_link.parent))

        rebase = Rebaser(plan)
        for root in stages.values():
            for parent, dirs, files in os.walk(root, followlinks=False):
                for name in files:
                    path = Path(parent) / name
                    if path.is_symlink() or path.suffix.lower() not in TEXT_SUFFIXES or path.stat().st_size > 2_000_000:
                        continue
                    raw = path.read_bytes()
                    encoding = 'utf-16' if raw[:2] in [b'\xff\xfe', b'\xfe\xff'] else 'utf-8-sig' if raw.startswith(b'\xef\xbb\xbf') else 'utf-8'
                    try:
                        text = raw.decode(encoding)
                    except UnicodeError:
                        continue
                    updated = rebase.text(text)
                    if path.suffix.lower() == '.json':
                        try:
                            data = json.loads(text)
                        except ValueError:
                            pass
                        else:
                            value = rebase.value(data)
                            updated = json.dumps(value, indent=2, ensure_ascii=False) + '\n' if value != data else text
                    if updated != text:
                        path.write_bytes(updated.encode(encoding))
                        changed.append(str(path.relative_to(root)))

        state = rebase.value(plan.state)
        # The historically named working-directory map is runtime configuration,
        # not install identity; preserve it only for non-default custom CWDs.
        cwd_map = state.get('wabbajack_working_directories', {})
        cwd_map = {key: value for key, value in cwd_map.items()
                   if value != str(Path(key).parent)}
        state = {key: value for key, value in state.items() if not key.startswith('wabbajack_')}
        if cwd_map:
            state['wabbajack_working_directories'] = cwd_map
        settings = state['profile_settings']
        for key in list(settings):
            if key.startswith('wabbajack_') or key in IDENTITY_KEYS:
                settings.pop(key)
        if settings.get('collection_identity') or settings.get('collection_url'):
            settings['collection_custom_name'] = True
        settings['profile_specific_mods'] = True
        if plan.game:
            settings['game_path'] = str(plan.game)
        state['custom_exes'] = [rebase.text(path) for path in plan.state.get('custom_exes', [])
                                if path not in plan.dropped_tools]
        if args.no_saves:
            # Keep profile-specific saves enabled, starting with an empty folder.
            settings['profile_saves'] = True
            (stage_profile / 'Saves').mkdir(exist_ok=True)
        write_json(stage_profile / 'profile_state.json', state)
        for name in ['modlist.txt', 'plugins.txt', 'loadorder.txt']:
            if not (stage_profile / name).exists():
                (stage_profile / name).write_text('', encoding='utf-8')
        for line in (stage_profile / 'modlist.txt').read_text(encoding='utf-8-sig').splitlines():
            if line.startswith(('+', '-')) and line[1:].endswith('_separator'):
                name = line[1:]
                if name not in {'.', '..'} and not any(char in name for char in '/\\'):
                    (stage_profile / 'mods' / name).mkdir(exist_ok=True)
        if plan.runtime_game and (plan.runtime_game / 'SkyrimSE.exe').is_file():
            configure_skyrim_tools(plan, stage_profile, changed)
        elif stage_game and (stage_game / 'SkyrimSE.exe').is_file():
            configure_skyrim_tools(plan, stage_profile, changed)
        for executable in state['custom_exes']:
            path = Path(executable)
            if any(path == final or path.is_relative_to(final) for final in stages):
                if not staged(path).is_file():
                    raise CloneError(f'Copied custom executable is unavailable: {executable}')

        # Check exact list copies, complete independent files, and the new paths.
        for name in ['modlist.txt', 'plugins.txt', 'loadorder.txt']:
            if (plan.source / name).exists() and (stage_profile / name).read_bytes() != (plan.source / name).read_bytes():
                raise CloneError(f'Profile list changed unexpectedly: {name}')
        for job in plan.jobs:
            entries = [job.source] if job.source.is_file() else (
                Path(parent) / name for parent, _, files in os.walk(job.source, followlinks=False) for name in files)
            for source in entries:
                if source.is_symlink():
                    continue
                final = job.target if job.source.is_file() else job.target / source.relative_to(job.source)
                target = staged(final)
                if not target.is_file() or source.stat().st_ino == target.stat().st_ino and source.stat().st_dev == target.stat().st_dev:
                    raise CloneError(f'Copied file is missing or shares a source inode: {final}')
        report = {'status': 'passed', 'source_profile': str(plan.source),
                  'profile': str(plan.destination), 'game_root': str(plan.game) if plan.game else None,
                  'shared_game_root': str(plan.source_game) if not plan.game and plan.source_game else None,
                  'copied_files': copied, 'reflinked_files': reflinked,
                  'wabbajack_install_identity_removed': True,
                  'undeployed': True, 'external_tools_kept': plan.external_tools,
                  'warnings': plan.warnings, 'rebased_configuration_files': sorted(set(changed)),
                  'seconds': round(time.monotonic() - started, 2),
                  'next_step': 'Reopen Amethyst and select the new profile. Keep Steam running when launching a Steam game.'}
        write_json(stage_profile / 'clone-report.json', report)
        if plan.game:
            rename_new(stage_game, plan.game)
            published.append((plan.game, stage_game))
        validate_destination_name(plan.destination.parent, plan.destination.name)
        rename_new(stage_profile, plan.destination)
        published.append((plan.destination, stage_profile))
        for root in stages:
            for parent, dirs, files in os.walk(root, followlinks=False):
                for name in dirs + files:
                    path = Path(parent) / name
                    if path.is_symlink() and (not path.exists() or not any(path.resolve().is_relative_to(final) for final in stages)):
                        raise CloneError(f'Invalid copied symbolic link: {path}')
        return report
    except BaseException:
        # Only remove locations created by this run; pre-existing paths are never touched.
        for final, stage in reversed(published):
            rename_new(final, stage)
        raise
    finally:
        for stage in stages.values():
            if stage.exists():
                shutil.rmtree(stage)


def configure_skyrim_tools(plan: Plan, stage: Path, changed: list[str]):
    """Keep PGPatcher/PGLite's per-mod attribution on a live ordinary profile."""
    configs = []
    for path in (stage / 'mods').rglob('settings.json'):
        rel = path.relative_to(stage).as_posix()
        if not (rel.endswith('/PGPatcher/cfg/settings.json') or rel.endswith('/PGLite/settings.json')):
            continue
        data = read_json(path)
        params = data.get('params', {})
        if not isinstance(params, dict) or not isinstance(params.get('game'), dict):
            continue
        configs.append((path, data))
    if configs:
        stub = plan.destination / 'tools/amm_mo2_dummy'
        actual = stage / 'tools/amm_mo2_dummy'
        actual.mkdir(parents=True, exist_ok=True)
        game_type = configs[0][1]['params']['game'].get('type', 0)
        (actual / 'ModOrganizer.ini').write_text(
            '[General]\ngameName=Skyrim Special Edition\n'
            f'selected_profile=@ByteArray({plan.destination.name})\n'
            f'gamePath=@ByteArray({wine(plan.runtime_game)})\n'
            f'game_edition={"GOG" if game_type == 1 else "Steam"}\n\n[Settings]\n'
            f'base_directory={wine(stub)}\nmod_directory={wine(plan.destination / "mods")}\n'
            f'profiles_directory={wine(plan.destination.parent)}\n'
            f'overwrite_directory={wine(plan.destination / "overwrite")}\n', encoding='utf-8')
        for path, data in configs:
            params = data['params']
            params['game']['dir'] = wine(plan.runtime_game)
            params.setdefault('modmanager', {}).update(type=2, mo2instancedir=wine(stub), mo2useloosefileorder=True)
            path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
            changed.append(str(path.relative_to(stage)))
        args_path = stage / 'exe_args.json'
        args = read_json(args_path)
        current = args.get('PGPatcher.exe', '')
        if '--ignore-mo2vfscheck' not in current:
            args['PGPatcher.exe'] = (current + ' --ignore-mo2vfscheck').strip()
        args_path.write_text(json.dumps(args, indent=2) + '\n', encoding='utf-8')
    for path in (stage / 'tools').rglob('Bethini.ini') if (stage / 'tools').exists() else []:
        data = path.read_text(encoding='utf-8-sig')
        data = re.sub(r'^sSkyrim Special EditionINIPath=[^\r\n]*',
                      lambda match: 'sSkyrim Special EditionINIPath=' + wine(plan.destination / 'ini files') + '\\',
                      data, flags=re.M)
        path.write_text(data, encoding='utf-8')


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    result.add_argument('--source', required=True, help='Existing profile name, or its absolute directory')
    result.add_argument('--name', required=True, help='New ordinary profile name; destination must not exist')
    result.add_argument('--staging', help='Game staging directory containing profiles/ and mods/; inferred when possible')
    result.add_argument('--game-root-source', help='Override the source game folder if it is not in saved settings')
    result.add_argument('--game-root', help='New root folder; a profile with a separate game root defaults to <name> Game Root beside staging. Shared games are not copied unless explicitly requested.')
    result.add_argument('--authored-root', action='append', default=[],
                        help='Original Windows install folder, e.g. D:\\ListName; repeat for additional aliases')
    result.add_argument('--config-dir', default=str(Path(os.environ.get('XDG_CONFIG_HOME', str(Path.home() / '.config'))) / 'AmethystModManager'),
                        help='Read-only Amethyst configuration directory')
    result.add_argument('--copy-mode', choices=['auto', 'reflink', 'full'], default='auto',
                        help='auto tries independent reflinks and falls back to a full copy')
    result.add_argument('--copy-external-tools', action='store_true', help='Also copy external custom executables and their sibling files')
    result.add_argument('--no-saves', action='store_true', help='Start with an empty Saves folder')
    result.add_argument('--apply', action='store_true', help='Create the profile and game copy; otherwise preview without writing')
    return result


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    try:
        plan = build_plan(args)
        if not args.apply:
            print(json.dumps(plan.preview(), indent=2, ensure_ascii=False))
            print('\nDry run: no files created. Add --apply to create this clone.')
        else:
            result = apply_plan(plan, args, progress=lambda message: print(message, flush=True))
            print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    except (CloneError, OSError, shutil.Error) as exc:
        print(f'Clone failed: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
