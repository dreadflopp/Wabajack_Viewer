"""Read-only discovery shared by installed comparison and profile copying."""
import os
from pathlib import Path
import re
import sys


def default_downloads_folder():
    """Find Downloads, including redirected Windows and Linux XDG locations."""
    home = Path.home()
    if sys.platform == 'win32':
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                r'Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders') as key:
                value, _ = winreg.QueryValueEx(key, '{374DE290-123F-4565-9164-39C4925E467B}')
            return os.path.expandvars(value)
        except (ImportError, OSError):
            pass
    elif sys.platform.startswith('linux'):
        config = Path(os.environ.get('XDG_CONFIG_HOME', str(home / '.config'))) / 'user-dirs.dirs'
        try:
            for line in config.read_text(encoding='utf-8').splitlines():
                match = re.fullmatch(r'\s*XDG_DOWNLOAD_DIR="(.*)"\s*', line)
                if match:
                    value = match.group(1).replace('${HOME}', str(home)).replace('$HOME', str(home))
                    if Path(value).is_absolute():
                        return value
        except (OSError, UnicodeError):
            pass
    return str(home / 'Downloads')


def default_profiles_folder():
    base = Path.home() / 'Games' / 'Amethyst'
    choices = sorted(base.glob('*/profiles')) if base.is_dir() else []
    return str(next((p for p in choices if p.parent.name == 'Skyrim Special Edition'), choices[0] if choices else ''))


def profiles_folder(value):
    path = Path(value).expanduser().absolute()
    if path.name.casefold() == 'modlist.txt':
        path = path.parent
    if (path / 'modlist.txt').is_file():
        return path.parent, path.name
    return path, ''


def profile_names(folder):
    path = Path(folder).expanduser()
    if not path.is_dir():
        return []
    return sorted((p.name for p in path.iterdir() if p.is_dir() and not p.name.startswith('.')
                   and (p / 'modlist.txt').is_file()), key=str.casefold)


def amethyst_config_folder():
    return str(Path(os.environ.get('XDG_CONFIG_HOME', str(Path.home() / '.config'))) / 'AmethystModManager')
