import configparser
from bisect import bisect_left
from collections import Counter
from difflib import SequenceMatcher
import gzip
import json
from pathlib import Path
import re
import tkinter as tk
import tkinter.font as tkfont
from tkinter import filedialog, messagebox, scrolledtext, ttk
from datetime import datetime
import webbrowser
import zipfile


class Tooltip:
    def __init__(self, widget, text):
        self.widget = widget
        self.text = text
        self.window = None
        self.after_id = None
        widget.bind("<Enter>", self.schedule_show, add="+")
        widget.bind("<Leave>", self.hide, add="+")

    def schedule_show(self, event=None):
        self.cancel()
        self.after_id = self.widget.after(350, self.show)

    def cancel(self):
        if self.after_id is not None:
            try:
                self.widget.after_cancel(self.after_id)
            except Exception:
                pass
            self.after_id = None

    def show(self):
        if self.window or not self.text:
            return
        try:
            x = self.widget.winfo_rootx() + 16
            y = self.widget.winfo_rooty() + self.widget.winfo_height() + 8
            self.window = tk.Toplevel(self.widget)
            self.window.wm_overrideredirect(True)
            self.window.wm_geometry(f"+{x}+{y}")
            label = tk.Label(
                self.window,
                text=self.text,
                justify=tk.LEFT,
                background="#ffffe0",
                relief=tk.SOLID,
                borderwidth=1,
                font=("TkDefaultFont", 9),
                padx=6,
                pady=4,
            )
            label.pack()
        except Exception:
            self.window = None

    def hide(self, event=None):
        self.cancel()
        if self.window is not None:
            try:
                self.window.destroy()
            except Exception:
                pass
            self.window = None


class WabbajackGuideApp:
    SOURCE_DIRECTIVE_TYPES = {"FromArchive", "PatchedFromArchive"}
    INLINE_SOURCE_PREFIX = "INLINEFILE::"

    @staticmethod
    def sanitize_filename(value):
        """Convert text into a filesystem-friendly filename stem."""
        cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value).strip())
        return cleaned.strip("_") or "mod"

    def __init__(self, root):
        self.root = root
        self.root.title("Wabbajack Manual Installation Guide")
        self.root.geometry("1300x850")
        self.root.resizable(True, True)

        # Modlist metadata
        self.modlist_data = None
        self.archives = []
        self.directives = []
        self.archive_lookup = {}
        self.archive_by_hash = {}
        self.archive_name_index = {}
        self.mod_folder_index = {}
        self.archive_normalized_index = {}
        self.mod_folder_meta_cache = {}
        self.mod_folder_meta_paths = {}
        self.mod_folder_files = {}
        self.mod_folder_archive_hashes = {}
        self.synthetic_archive_by_hash = {}

        # Parsed InlineFile profile data
        self.inline_file_sources = {}
        self.inline_text_cache = {}
        self.active_profile = None
        self.all_profile_paths = []
        self.profile_modlist_text = ""
        self.profile_ini_files = {}

        # UI list state
        self.mod_rows = []
        self.row_by_item_id = {}
        self.row_by_source_item_id = {}
        self.sort_column = "number"
        self.sort_reverse = False
        self.sources_sort_column = "source_path"
        self.sources_sort_reverse = False
        self.files_sort_column = "target_path"
        self.files_sort_reverse = False
        self.sort_state_before_external_only = None
        self.current_selected_row = None
        self.external_compare_path = None
        self.external_only_compare_names = []
        self._tooltips = []

        self.current_wabbajack_file = None
        self.loaded_from_saved_modlist = False
        self.last_saved_modlist_path = ""
        self.config_path = Path(__file__).with_name("wabbajack_viewer.config.json")

        self.load_persisted_preferences()

        self.setup_ui()
        self.auto_load_last_saved_modlist()

    @staticmethod
    def is_saved_modlist_path(file_path):
        """Return True only for saved viewer snapshot files."""
        lower = str(file_path or "").lower()
        return lower.endswith(".wvmodlist.json.gz") or lower.endswith(".wvjcache.json.gz")

    def load_persisted_preferences(self):
        """Load persisted app preferences from disk."""
        self.last_saved_modlist_path = ""
        if not self.config_path.exists():
            return

        try:
            payload = json.loads(self.config_path.read_text(encoding="utf-8"))
            candidate = payload.get("last_saved_modlist_path", "")
            if self.is_saved_modlist_path(candidate):
                self.last_saved_modlist_path = str(candidate)
        except Exception:
            self.last_saved_modlist_path = ""

    def persist_preferences(self):
        """Save app preferences to disk."""
        payload = {"last_saved_modlist_path": self.last_saved_modlist_path or ""}
        try:
            self.config_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    def remember_saved_modlist_path(self, file_path):
        """Remember the selected saved modlist path for next startup."""
        if self.is_saved_modlist_path(file_path):
            self.last_saved_modlist_path = str(file_path)
            self.persist_preferences()

    def load_saved_modlist_from_path(self, file_path):
        """Load a previously saved modlist snapshot from an explicit path."""
        if not file_path:
            return False

        path = Path(file_path)
        if not path.exists():
            return False

        try:
            self.set_status("Loading saved modlist...")
            with gzip.open(path, "rb") as handle:
                payload = json.load(handle)

            self.modlist_data = payload.get("modlist_data") or {}
            self.archives = payload.get("archives") or []
            self.directives = []
            self.archive_lookup = {}
            self.archive_by_hash = {}
            self.mod_folder_meta_cache = {}
            self.mod_folder_meta_paths = {}
            self.mod_folder_files = {}
            self.mod_folder_archive_hashes = {}
            self.synthetic_archive_by_hash = {}
            self.inline_file_sources = {}
            self.inline_text_cache = {}
            self.profile_ini_files = payload.get("profile_ini_files") or {}
            self.all_profile_paths = payload.get("all_profile_paths") or []
            self.active_profile = payload.get("active_profile")
            self.current_wabbajack_file = payload.get("source_wabbajack")

            for archive in self.archives:
                archive_hash = archive.get("Hash", "")
                archive_name = archive.get("Name", "")
                self.archive_lookup[archive_hash] = archive_name
                self.archive_by_hash[archive_hash] = archive

            self.archive_name_index = self.build_archive_name_index()
            self.mod_folder_index = {}
            self.archive_normalized_index = self.build_archive_normalized_index()

            rows = payload.get("mod_rows") or []
            hydrated_rows = []
            for row in rows:
                hydrated = dict(row)
                source_hashes = hydrated.pop("source_archive_hashes", [])
                folder_files = hydrated.get("folder_files", [])
                hydrated["source_archives"] = [
                    self.resolve_archive_reference(archive_hash, folder_files)
                    for archive_hash in source_hashes
                    if self.resolve_archive_reference(archive_hash, folder_files)
                ]
                hydrated["archive"] = self.archive_by_hash.get(hydrated.get("archive_hash", ""))
                hydrated.setdefault("folder_files", [])
                hydrated.setdefault("activation_state", "active" if hydrated.get("active", True) else "inactive")
                hydrated["source_file_counts"] = self.count_files_by_archive(hydrated["folder_files"])
                hydrated_rows.append(hydrated)

            self.mod_rows = hydrated_rows
            self.loaded_from_saved_modlist = True
            self.update_ui_after_load(prepare_rows=False)
            self.remember_saved_modlist_path(str(path))
            self.set_status(f"Loaded saved modlist: {path.name}")
            return True
        except Exception as e:
            messagebox.showerror("Error", f"Failed to load saved modlist: {e}")
            return False

    def auto_load_last_saved_modlist(self):
        """Load the last saved snapshot automatically when available."""
        if not self.last_saved_modlist_path:
            return
        if not self.is_saved_modlist_path(self.last_saved_modlist_path):
            return
        self.load_saved_modlist_from_path(self.last_saved_modlist_path)

    def load_wabbajack_file(self):
        """Open file dialog to select and load Wabbajack file."""
        file_path = filedialog.askopenfilename(
            title="Select Wabbajack File",
            filetypes=[("Wabbajack files", "*.wabbajack"), ("All files", "*.*")],
        )

        if file_path:
            self.current_wabbajack_file = file_path
            self.extract_and_load_modlist(file_path)

    def set_status(self, message):
        """Update status text and flush pending UI updates."""
        if hasattr(self, "status_var"):
            self.status_var.set(message)
            self.root.update_idletasks()

    @staticmethod
    def quote_bash(value):
        """Return a single-quoted Bash string literal."""
        text = str(value)
        return "'" + text.replace("'", "'\"'\"'") + "'"

    @staticmethod
    def is_mohidden_file(file_info):
        """Return True when a file entry represents a .mohidden file."""
        target_path = str(file_info.get("target_path", "")).lower()
        archive_path = str(file_info.get("archive_path", "")).lower()
        return target_path.endswith(".mohidden") or archive_path.endswith(".mohidden")

    @staticmethod
    def is_mod_meta_file_path(target_path):
        """Return True for the per-mod meta.ini file stored under mods\\<folder>\\meta.ini."""
        normalized = str(target_path or "").replace("/", "\\").lower()
        return normalized.startswith("mods\\") and normalized.endswith("\\meta.ini")

    @staticmethod
    def escape_batch(value):
        """Escape text for safe use inside quoted batch strings."""
        return str(value).replace("%", "%%")

    def build_grouped_install_data(self, row):
        """Group install operations by source type for efficient extraction."""
        folder_name = str(row.get("raw_name", ""))
        folder_prefix = f"mods\\{folder_name}\\"
        archive_names = {
            archive.get("Hash", ""): archive.get("Name", "")
            for archive in row.get("source_archives", [])
            if not str(archive.get("State", {}).get("$type", "")).startswith("InlineFile")
        }

        archive_groups = {}
        embedded_files = []
        for file_info in row.get("folder_files", []):
            archive_hash = file_info.get("archive_hash", "")
            target_path = str(file_info.get("target_path", ""))
            target_relative = target_path[len(folder_prefix):] if target_path.lower().startswith(folder_prefix.lower()) else target_path

            if str(archive_hash).startswith(self.INLINE_SOURCE_PREFIX):
                source_data_id = str(file_info.get("source_data_id", "")).strip()
                if not source_data_id:
                    archive_path = str(file_info.get("archive_path", ""))
                    if archive_path.startswith("[Embedded InlineFile] "):
                        source_data_id = archive_path[len("[Embedded InlineFile] "):].strip()
                if source_data_id:
                    embedded_files.append(
                        {
                            "source_data_id": source_data_id,
                            "target_relative": target_relative,
                        }
                    )
                continue

            archive_filename = archive_names.get(archive_hash, "")
            if not archive_filename:
                continue

            group = archive_groups.setdefault(archive_filename, [])
            group.append(
                {
                    "archive_path": str(file_info.get("archive_path", "")),
                    "target_relative": target_relative,
                }
            )

        return {"archive_groups": archive_groups, "embedded_files": embedded_files}

    def build_batch_install_script(self, row):
        """Return a grouped Windows batch installer script for the selected mod."""
        folder_name = str(row.get("raw_name", row.get("name", "mod")))
        grouped = self.build_grouped_install_data(row)
        archive_groups = grouped.get("archive_groups", {})
        embedded_files = grouped.get("embedded_files", [])
        wabbajack_name = Path(self.current_wabbajack_file).name if self.current_wabbajack_file else ""
        lines = [
            "@echo off",
            "setlocal EnableDelayedExpansion",
            'set "SCRIPT_DIR=%~dp0"',
            f'set "OUTPUT_DIR=%SCRIPT_DIR%{self.escape_batch(folder_name)}"',
            'set "TEMP_ROOT=%SCRIPT_DIR%.extract_temp"',
            'set "LOG_PATH=%SCRIPT_DIR%install_errors.log"',
            f'set "WABBAJACK_PATH=%SCRIPT_DIR%{self.escape_batch(wabbajack_name)}"' if wabbajack_name else 'set "WABBAJACK_PATH="',
            'set "ARCHIVE_SEARCH_DIRS=%SCRIPT_DIR%"',
            ':parse_args',
            'if "%~1"=="" goto args_done',
            'set "ARG=%~1"',
            '  if /I "!ARG:~0,11!"=="/wabbajack:" (',
            '    set "WABBAJACK_PATH=!ARG:~11!"',
            '  ) else (',
            '    set "ARCHIVE_SEARCH_DIRS=!ARCHIVE_SEARCH_DIRS!;!ARG!"',
            '  )',
            'shift',
            'goto parse_args',
            ':args_done',
            'where 7z >nul 2>nul',
            'if errorlevel 1 (',
            '  echo 7z is required and was not found in PATH.',
            '  exit /b 1',
            ')',
            'if exist "%TEMP_ROOT%" rmdir /s /q "%TEMP_ROOT%"',
            'mkdir "%TEMP_ROOT%" >nul 2>nul',
            'mkdir "%OUTPUT_DIR%" >nul 2>nul',
            'break > "%LOG_PATH%"',
            '',
            ':resolve_archive',
            'set "FOUND_ARCHIVE_PATH="',
            'for %%D in ("%ARCHIVE_SEARCH_DIRS:;=" "%") do (',
            '  if not defined FOUND_ARCHIVE_PATH if exist "%%~fD\\%ARCHIVE_FILE%" set "FOUND_ARCHIVE_PATH=%%~fD\\%ARCHIVE_FILE%"',
            ')',
            'exit /b',
            '',
        ]

        for index, (archive_filename, items) in enumerate(archive_groups.items(), start=1):
            escaped_archive_filename = self.escape_batch(archive_filename)
            lines.extend(
                [
                    f'rem Archive group {index}: {escaped_archive_filename}',
                    f'set "ARCHIVE_FILE={escaped_archive_filename}"',
                    'call :resolve_archive',
                    f'set "STAGE_DIR=%TEMP_ROOT%\\archive_{index}"',
                    'if not defined FOUND_ARCHIVE_PATH (',
                    '  >> "%LOG_PATH%" echo MISSING ARCHIVE: %ARCHIVE_FILE%',
                    '  goto :after_archive_' + str(index),
                    ')',
                    'mkdir "%STAGE_DIR%" >nul 2>nul',
                ]
            )

            extract_command = '7z x -y "-o%STAGE_DIR%" "%FOUND_ARCHIVE_PATH%"'
            for item in items:
                extract_command += f' "{self.escape_batch(item["archive_path"])}"'
            lines.append(extract_command + ' >nul')
            lines.extend(
                [
                    'if errorlevel 1 >> "%LOG_PATH%" echo EXTRACT FAILED OR PARTIAL: %ARCHIVE_FILE%',
                    '',
                ]
            )

            for item in items:
                source_path = self.escape_batch(item["archive_path"])
                target_relative = self.escape_batch(item["target_relative"])
                lines.extend(
                    [
                        f'set "SOURCE_REL={source_path}"',
                        f'set "TARGET_REL={target_relative}"',
                        'if exist "%STAGE_DIR%\\%SOURCE_REL%" (',
                        '  for %%D in ("%OUTPUT_DIR%\\%TARGET_REL%") do mkdir "%%~dpD" >nul 2>nul',
                        '  copy /Y "%STAGE_DIR%\\%SOURCE_REL%" "%OUTPUT_DIR%\\%TARGET_REL%" >nul',
                        ') else (',
                        '  >> "%LOG_PATH%" echo MISSING FILE IN ARCHIVE: %ARCHIVE_FILE% ^| %SOURCE_REL%',
                        ')',
                    ]
                )

            lines.extend(
                [
                    f':after_archive_{index}',
                    'if exist "%STAGE_DIR%" rmdir /s /q "%STAGE_DIR%"',
                    '',
                ]
            )

        if embedded_files:
            lines.extend(
                [
                    'rem Embedded InlineFile payloads from .wabbajack',
                    'if "%WABBAJACK_PATH%"=="" (',
                    '  >> "%LOG_PATH%" echo MISSING WABBAJACK FILE: no /wabbajack:path provided and no default file set',
                    ') else if not exist "%WABBAJACK_PATH%" (',
                    '  >> "%LOG_PATH%" echo MISSING WABBAJACK FILE: %WABBAJACK_PATH%',
                    ')',
                    '',
                ]
            )

        for index, item in enumerate(embedded_files, start=1):
            source_data_id = self.escape_batch(item.get("source_data_id", ""))
            target_relative = self.escape_batch(item.get("target_relative", ""))
            lines.extend(
                [
                    f'rem Embedded file {index}: {source_data_id}',
                    f'set "STAGE_DIR=%TEMP_ROOT%\\embedded_{index}"',
                    'if exist "%WABBAJACK_PATH%" (',
                    '  mkdir "%STAGE_DIR%" >nul 2>nul',
                    f'  7z x -y "-o%STAGE_DIR%" "%WABBAJACK_PATH%" "{source_data_id}" >nul',
                    f'  if exist "%STAGE_DIR%\\{source_data_id}" (',
                    f'    for %%D in ("%OUTPUT_DIR%\\{target_relative}") do mkdir "%%~dpD" >nul 2>nul',
                    f'    copy /Y "%STAGE_DIR%\\{source_data_id}" "%OUTPUT_DIR%\\{target_relative}" >nul',
                    '  ) else (',
                    f'    >> "%LOG_PATH%" echo MISSING EMBEDDED FILE: {source_data_id}',
                    '  )',
                    ')',
                    'if exist "%STAGE_DIR%" rmdir /s /q "%STAGE_DIR%"',
                    '',
                ]
            )

        lines.extend(
            [
                'if exist "%TEMP_ROOT%" rmdir /s /q "%TEMP_ROOT%"',
                'echo Install replay complete. Output: %OUTPUT_DIR%',
                'echo Errors logged to: %LOG_PATH%',
                'echo Optional args: [archiveDir1] [archiveDir2] ... [/wabbajack:C:\\path\\list.wabbajack]',
                'pause',
                'endlocal',
            ]
        )
        return "\r\n".join(lines) + "\r\n"

    def build_bash_install_script(self, row):
        """Return a grouped Bash installer script for the selected mod."""
        folder_name = str(row.get("raw_name", row.get("name", "mod")))
        grouped = self.build_grouped_install_data(row)
        archive_groups = grouped.get("archive_groups", {})
        embedded_files = grouped.get("embedded_files", [])
        wabbajack_name = Path(self.current_wabbajack_file).name if self.current_wabbajack_file else ""
        lines = [
            '#!/usr/bin/env bash',
            'set +e',
            '',
            'SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"',
            f'OUTPUT_DIR="$SCRIPT_DIR/{folder_name}"',
            'TEMP_ROOT="$SCRIPT_DIR/.extract_temp"',
            'LOG_PATH="$SCRIPT_DIR/install_errors.log"',
            f'WABBAJACK_PATH="$SCRIPT_DIR/{wabbajack_name}"' if wabbajack_name else 'WABBAJACK_PATH=""',
            'SEARCH_DIRS=("$SCRIPT_DIR")',
            'for arg in "$@"; do',
            '  if [[ "$arg" == --wabbajack=* ]]; then',
            '    WABBAJACK_PATH="${arg#--wabbajack=}"',
            '  else',
            '    SEARCH_DIRS+=("$arg")',
            '  fi',
            'done',
            '',
            'if ! command -v 7z >/dev/null 2>&1; then',
            '  echo "7z is required and was not found in PATH." >&2',
            '  exit 1',
            'fi',
            '',
            'rm -rf "$TEMP_ROOT"',
            'mkdir -p "$TEMP_ROOT" "$OUTPUT_DIR"',
            ': > "$LOG_PATH"',
            '',
            'resolve_archive_path() {',
            '  local archive_file="$1"',
            '  local dir',
            '  for dir in "${SEARCH_DIRS[@]}"; do',
            '    if [ -f "$dir/$archive_file" ]; then',
            '      printf "%s" "$dir/$archive_file"',
            '      return 0',
            '    fi',
            '  done',
            '  return 1',
            '}',
            '',
        ]

        for index, (archive_filename, items) in enumerate(archive_groups.items(), start=1):
            lines.extend(
                [
                    f'# Archive group {index}: {archive_filename}',
                    f'archive_file={self.quote_bash(archive_filename)}',
                    'archive_path="$(resolve_archive_path "$archive_file")"',
                    f'stage_dir="$TEMP_ROOT/archive_{index}"',
                    'if [ -z "$archive_path" ] || [ ! -f "$archive_path" ]; then',
                    '  echo "MISSING ARCHIVE: $archive_file" >> "$LOG_PATH"',
                    'else',
                    '  mkdir -p "$stage_dir"',
                ]
            )

            extract_command = '  7z x -y "-o$stage_dir" "$archive_path"'
            for item in items:
                extract_command += f' {self.quote_bash(item["archive_path"])}'
            lines.append(extract_command + ' >/dev/null 2>&1')
            lines.append('  if [ $? -ne 0 ]; then echo "EXTRACT FAILED OR PARTIAL: $archive_file" >> "$LOG_PATH"; fi')

            for item in items:
                archive_path = item["archive_path"]
                target_relative = item["target_relative"]
                lines.extend(
                    [
                        f'  source_rel={self.quote_bash(archive_path)}',
                        f'  target_rel={self.quote_bash(target_relative)}',
                        '  if [ -f "$stage_dir/$source_rel" ]; then',
                        '    mkdir -p "$(dirname "$OUTPUT_DIR/$target_rel")"',
                        '    cp -f "$stage_dir/$source_rel" "$OUTPUT_DIR/$target_rel"',
                        '  else',
                        '    echo "MISSING FILE IN ARCHIVE: $archive_file | $source_rel" >> "$LOG_PATH"',
                        '  fi',
                    ]
                )

            lines.extend(
                [
                    '  rm -rf "$stage_dir"',
                    'fi',
                    '',
                ]
            )

        if embedded_files:
            lines.extend(
                [
                    '# Embedded InlineFile payloads from .wabbajack',
                    'if [ -z "$WABBAJACK_PATH" ]; then',
                    '  echo "MISSING WABBAJACK FILE: no --wabbajack= path provided and no default file set" >> "$LOG_PATH"',
                    'elif [ ! -f "$WABBAJACK_PATH" ]; then',
                    '  echo "MISSING WABBAJACK FILE: $WABBAJACK_PATH" >> "$LOG_PATH"',
                    'fi',
                    '',
                ]
            )

        for index, item in enumerate(embedded_files, start=1):
            source_data_id = item.get("source_data_id", "")
            target_relative = item.get("target_relative", "")
            lines.extend(
                [
                    f'# Embedded file {index}: {source_data_id}',
                    f'stage_dir="$TEMP_ROOT/embedded_{index}"',
                    'if [ -f "$WABBAJACK_PATH" ]; then',
                    '  mkdir -p "$stage_dir"',
                    f'  7z x -y "-o$stage_dir" "$WABBAJACK_PATH" {self.quote_bash(source_data_id)} >/dev/null 2>&1',
                    f'  if [ -f "$stage_dir/{source_data_id}" ]; then',
                    f'    target_rel={self.quote_bash(target_relative)}',
                    '    mkdir -p "$(dirname "$OUTPUT_DIR/$target_rel")"',
                    f'    cp -f "$stage_dir/{source_data_id}" "$OUTPUT_DIR/$target_rel"',
                    '  else',
                    f'    echo "MISSING EMBEDDED FILE: {source_data_id}" >> "$LOG_PATH"',
                    '  fi',
                    'fi',
                    'rm -rf "$stage_dir"',
                    '',
                ]
            )

        lines.extend(
            [
                'rm -rf "$TEMP_ROOT"',
                'echo "Install replay complete. Output: $OUTPUT_DIR"',
                'echo "Errors logged to: $LOG_PATH"',
                'echo "Optional args: [archiveDir1] [archiveDir2] ... [--wabbajack=/path/to/list.wabbajack]"',
            ]
        )
        return "\n".join(lines) + "\n"

    def copy_selected_mod_name(self):
        """Copy the selected mod name to clipboard."""
        row = self.current_selected_row
        if not row:
            return

        name = str(row.get("raw_name") or row.get("name") or "").strip()
        if not name:
            return

        self.root.clipboard_clear()
        self.root.clipboard_append(name)
        self.set_status(f"Copied mod name: {name}")

    def open_selected_mod_page(self):
        """Open the selected mod's page URL in the default browser."""
        row = self.current_selected_row
        url = self.get_mod_page_url(row)
        if not url:
            messagebox.showwarning("No URL", "No mod page URL is available for the selected mod.")
            return

        try:
            webbrowser.open(url)
        except Exception as e:
            messagebox.showerror("Error", f"Failed to open mod URL: {e}")

    def add_tooltip(self, widget, text):
        """Attach a tooltip to a widget and keep it alive."""
        if not text:
            return
        self._tooltips.append(Tooltip(widget, text))

    @staticmethod
    def format_sort_heading(base_label, is_active, reverse):
        """Format a tree heading label with a simple ASCII sort indicator."""
        if not is_active:
            return base_label
        return f"{base_label} {'▾' if reverse else '▴'}"

    def update_sort_indicators(self):
        """Refresh visible sort labels for the mod list and detail trees."""
        if hasattr(self, "tree"):
            mod_headings = {
                "#0": "#",
                "active": "Active",
                "name": "Mod Name",
                "sources": "Sources",
                "author": "Author",
                "version": "Version",
                "size": "Size (MB)",
            }
            for column, label in mod_headings.items():
                is_active = (column == "#0" and self.sort_column == "number") or (column != "#0" and self.sort_column == column)
                reverse = self.sort_reverse if is_active else False
                self.tree.heading(column, text=self.format_sort_heading(label, is_active, reverse))

        if hasattr(self, "sources_tree"):
            source_headings = {
                "#0": ("Source Archive", "source"),
                "target_path": ("Install Path", "target_path"),
                "source_path": ("Source Path", "source_path"),
                "size": ("Size (bytes)", "size"),
                "kind": ("Directive", "kind"),
            }
            for column, (label, sort_key) in source_headings.items():
                is_active = self.sources_sort_column == sort_key
                reverse = self.sources_sort_reverse if is_active else False
                self.sources_tree.heading(column, text=self.format_sort_heading(label, is_active, reverse))

        if hasattr(self, "files_tree"):
            file_headings = {
                "#0": ("Target Path", "target_path"),
                "source_mod": ("Source Mod", "source_mod"),
                "source_path": ("Source Path", "source_path"),
                "size": ("Size (bytes)", "size"),
            }
            for column, (label, sort_key) in file_headings.items():
                is_active = self.files_sort_column == sort_key
                reverse = self.files_sort_reverse if is_active else False
                self.files_tree.heading(column, text=self.format_sort_heading(label, is_active, reverse))

    def get_mod_page_urls(self, row):
        """Return all distinct mod-page URLs available for a row."""
        if not row or row.get("is_separator"):
            return []

        urls = []
        seen = set()
        for source_archive in row.get("source_archives", []):
            page = self.build_archive_links(source_archive).get("page")
            if page and page not in seen:
                seen.add(page)
                urls.append((source_archive, page))

        return urls

    def get_mod_page_url(self, row):
        """Return the first mod-page URL source used by the details panel."""
        urls = self.get_mod_page_urls(row)
        return urls[0][1] if urls else ""

    def get_source_archive_label(self, source_archive):
        """Return a user-facing label for a source archive or embedded source."""
        if not source_archive:
            return "Unknown"
        state = source_archive.get("State", {})
        return state.get("Name") or source_archive.get("Name", "Unknown")

    def get_source_sort_value(self, source_archive, source_files, column):
        """Compute a stable sort key for a source tree row."""
        state = source_archive.get("State", {}) if source_archive else {}
        if column == "source":
            return self.get_source_archive_label(source_archive).lower()
        if column == "target_path":
            return min((str(item.get("target_path", "")).lower() for item in source_files), default="")
        if column == "source_path":
            return min((str(item.get("archive_path", "")).lower() for item in source_files), default="")
        if column == "size":
            return sum(int(item.get("size", 0) or 0) for item in source_files)
        if column == "kind":
            return ",".join(sorted({str(item.get("directive_type", "")).lower() for item in source_files if item.get("directive_type")}))
        return self.get_source_archive_label(source_archive).lower()

    def sort_files_for_column(self, files):
        """Sort file rows using the current Files tab column selection."""
        column = self.files_sort_column
        reverse = self.files_sort_reverse

        def key(item):
            if column == "source_mod":
                return str(item.get("source_mod", "")).lower()
            if column == "source_path":
                return str(item.get("archive_path", "")).lower()
            if column == "size":
                return int(item.get("size", 0) or 0)
            return str(item.get("target_path", "")).lower()

        return sorted(files, key=key, reverse=reverse)

    def on_sources_sort_requested(self, column):
        """Sort the Sources tab by the clicked column."""
        if column == self.sources_sort_column:
            self.sources_sort_reverse = not self.sources_sort_reverse
        else:
            self.sources_sort_column = column
            self.sources_sort_reverse = False
        if self.current_selected_row:
            self.update_sources_tab(self.current_selected_row)
        self.update_sort_indicators()

    def on_files_sort_requested(self, column):
        """Sort the Files tab by the clicked column."""
        if column == self.files_sort_column:
            self.files_sort_reverse = not self.files_sort_reverse
        else:
            self.files_sort_column = column
            self.files_sort_reverse = False
        if self.current_selected_row:
            self.update_files_tab(self.current_selected_row)
        self.update_sort_indicators()

    def export_install_script_for_selected(self, script_type):
        """Export a BAT or Bash installer script for the selected mod."""
        row = self.current_selected_row
        if not row or row.get("is_separator"):
            messagebox.showwarning("No Mod Selected", "Select a non-separator mod entry first.")
            return
        if not row.get("folder_files"):
            messagebox.showwarning("No Files", "The selected mod has no file mapping data to export.")
            return

        script_type = str(script_type).lower().strip()
        if script_type not in {"bat", "sh"}:
            messagebox.showerror("Error", f"Unsupported script type: {script_type}")
            return

        base_name = self.sanitize_filename(row.get("raw_name", row.get("name", "mod")))
        extension = ".bat" if script_type == "bat" else ".sh"
        suggested_name = f"installer_script_{base_name}{extension}"

        output_path = filedialog.asksaveasfilename(
            title=f"Export {extension} install script",
            defaultextension=extension,
            initialfile=suggested_name,
            filetypes=[
                ("Batch script", "*.bat") if script_type == "bat" else ("Bash script", "*.sh"),
                ("All files", "*.*"),
            ],
        )
        if not output_path:
            return

        try:
            script_text = self.build_batch_install_script(row) if script_type == "bat" else self.build_bash_install_script(row)
            newline = "\r\n" if script_type == "bat" else "\n"
            Path(output_path).write_text(script_text, encoding="utf-8", newline=newline)
            self.set_status(f"Exported {script_type} install script for {row.get('raw_name', row.get('name', 'mod'))}")
            messagebox.showinfo(
                "Install Script Exported",
                f"Exported:\n{Path(output_path).name}\n\n"
                "Usage: pass archive folder paths as script arguments.\n"
                "Optional: pass /wabbajack:<path> (BAT) or --wabbajack=<path> (Bash) for embedded files.\n"
                "The script groups extraction by source and logs errors while continuing."
            )
        except Exception as e:
            messagebox.showerror("Error", f"Failed to export install script: {e}")

    def collapse_all_mod_sections(self):
        """Collapse all top-level rows in the mod list tree."""
        for item_id in self.tree.get_children(""):
            self.tree.item(item_id, open=False)

    def expand_all_mod_sections(self):
        """Expand all top-level rows in the mod list tree."""
        for item_id in self.tree.get_children(""):
            self.tree.item(item_id, open=True)

    def on_mod_right_click(self, event):
        """Show row actions for the clicked mod."""
        item_id = self.tree.identify_row(event.y)
        if not item_id:
            return

        self.tree.selection_set(item_id)
        row = self.row_by_item_id.get(item_id)
        if not row:
            return
        self.current_selected_row = row

        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label="Copy Mod Name", command=self.copy_selected_mod_name)
        menu.add_separator()
        menu.add_command(label="Expand All", command=self.expand_all_mod_sections)
        menu.add_command(label="Collapse All", command=self.collapse_all_mod_sections)

        if not row.get("is_separator"):
            urls = self.get_mod_page_urls(row)
            if len(urls) == 1:
                menu.add_command(label="Mod URL", command=lambda value=urls[0][1]: webbrowser.open(value))
            elif len(urls) > 1:
                url_menu = tk.Menu(menu, tearoff=0)
                for source_archive, url in urls:
                    label = self.get_source_archive_label(source_archive)
                    url_menu.add_command(label=label, command=lambda value=url: webbrowser.open(value))
                menu.add_cascade(label="Mod URLs", menu=url_menu)
            if row.get("folder_files"):
                menu.add_command(
                    label="Export BAT Install Script",
                    command=lambda: self.export_install_script_for_selected("bat"),
                )
                menu.add_command(
                    label="Export Bash Install Script",
                    command=lambda: self.export_install_script_for_selected("sh"),
                )

        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def build_modlist_txt(self, include_external_only=False, deactivate_external_only=False, reverse_for_mo2=True):
        """Return modlist.txt content in MO2 load-order format.
        
        Builds visible order first (including external-only entries at their positions),
        then reverses to match modlist.txt format.
        """
        # Build visible order. For standard export, honor current sort.
        # For merged export, use compare placement order for external-only entries.
        visible_order = []
        if include_external_only:
            export_rows = self.get_rows_in_compare_placement_order(include_external_only=True)
        else:
            export_rows = self.get_rows_in_current_sort_order()

        for row in export_rows:
            is_external_only = bool(row.get("external_only"))
            if is_external_only:
                prefix = "-" if deactivate_external_only else "+"
            else:
                prefix = self.get_activation_prefix_from_state(row.get("activation_state") or ("active" if row.get("active") else "inactive"))
            raw_name = row.get("raw_name") or row.get("name") or "mod"
            visible_order.append((prefix, raw_name, is_external_only))

        # MO2 modlist.txt uses reverse of display order. For diagnostics, caller can disable reversal.
        ordered_rows = reversed(visible_order) if reverse_for_mo2 else visible_order
        lines = [f"{prefix}{name}" for prefix, name, _ in ordered_rows]
        return "\r\n".join(lines) + ("\r\n" if lines else "")

    @staticmethod
    def count_nonempty_lines(text):
        """Count non-empty lines in an exported text block."""
        return sum(1 for line in str(text).splitlines() if line.strip())

    @staticmethod
    def get_activation_state_from_prefix(prefix):
        """Map a modlist prefix to an activation state."""
        if prefix == "*":
            return "always"
        if prefix == "-":
            return "inactive"
        return "active"

    @staticmethod
    def get_activation_prefix_from_state(state):
        """Map an activation state to a modlist prefix."""
        if state == "always":
            return "*"
        if state == "inactive":
            return "-"
        return "+"

    @staticmethod
    def get_activation_mark_for_row(row):
        """Return a compact table mark for the row activation state."""
        if row.get("is_separator"):
            return ""
        state = row.get("activation_state") or ("active" if row.get("active") else "inactive")
        if state == "always":
            return "[*]"
        if state == "inactive":
            return "[ ]"
        return "[x]"

    @staticmethod
    def get_activation_label_for_row(row):
        """Return a human-readable activation label for the row."""
        state = row.get("activation_state") or ("active" if row.get("active") else "inactive")
        if state == "always":
            return "Always Active"
        if state == "inactive":
            return "Inactive"
        return "Active"

    def get_rows_in_compare_placement_order(self, include_external_only=False):
        """Return rows in compare placement order, optionally including external-only entries."""
        if not include_external_only or not self.external_only_compare_names:
            return list(self.mod_rows)

        inserts_by_index = {}
        for entry in self.external_only_compare_names:
            insert_index = int(entry.get("insert_index", len(self.mod_rows)))
            inserts_by_index.setdefault(insert_index, []).append(entry)

        interleaved_rows = []
        wb_index = 0
        external_counter = 0

        for row in self.mod_rows:
            if not row.get("is_separator"):
                for entry in inserts_by_index.get(wb_index, []):
                    external_counter += 1
                    raw_name = str(entry.get("name", ""))
                    display_name, is_separator = self.parse_external_entry_name(raw_name)
                    interleaved_rows.append(
                        {
                            "number": f"E{external_counter}",
                            "name": display_name,
                            "raw_name": raw_name,
                            "folder_key": self.normalize_mod_key(raw_name),
                            "active": False,
                            "activation_state": "inactive",
                            "is_separator": is_separator,
                            "author": "-",
                            "version": "",
                            "size_mb": "",
                            "direct_link_count": 0,
                            "archive_hash": "",
                            "archive_name": "",
                            "archive": None,
                            "folder_meta": {},
                            "source_archives": [],
                            "folder_files": [],
                            "source_file_counts": {},
                            "compare_status": "external_only",
                            "external_only": True,
                        }
                    )
                wb_index += 1

            interleaved_rows.append(row)

        for entry in inserts_by_index.get(wb_index, []):
            external_counter += 1
            raw_name = str(entry.get("name", ""))
            display_name, is_separator = self.parse_external_entry_name(raw_name)
            interleaved_rows.append(
                {
                    "number": f"E{external_counter}",
                    "name": display_name,
                    "raw_name": raw_name,
                    "folder_key": self.normalize_mod_key(raw_name),
                    "active": False,
                    "activation_state": "inactive",
                    "is_separator": is_separator,
                    "author": "-",
                    "version": "",
                    "size_mb": "",
                    "direct_link_count": 0,
                    "archive_hash": "",
                    "archive_name": "",
                    "archive": None,
                    "folder_meta": {},
                    "source_archives": [],
                    "folder_files": [],
                    "source_file_counts": {},
                    "compare_status": "external_only",
                    "external_only": True,
                }
            )

        return interleaved_rows

    def get_rows_in_current_sort_order(self):
        """Return base mod rows ordered exactly like the current sort settings."""
        rows = list(self.mod_rows)

        def sort_key(item):
            if self.sort_column == "number":
                number = item.get("number", 0)
                if isinstance(number, str) and number.startswith("E"):
                    try:
                        return (1, int(number[1:]))
                    except Exception:
                        return (1, 0)
                try:
                    return (0, int(number))
                except Exception:
                    return (0, 0)
            if self.sort_column == "active":
                state = item.get("activation_state") or ("active" if item.get("active") else "inactive")
                if state == "always":
                    return 2
                if state == "active":
                    return 1
                return 0
            if self.sort_column == "name":
                return str(item.get("name", "")).lower()
            if self.sort_column == "sources":
                return len(item.get("source_archives", []))
            if self.sort_column == "author":
                return str(item.get("author", "")).lower()
            if self.sort_column == "version":
                return str(item.get("version", "")).lower()
            if self.sort_column == "size":
                try:
                    return float(item.get("size_mb") or 0)
                except Exception:
                    return 0.0
            return item.get("number", 0)

        return sorted(rows, key=sort_key, reverse=self.sort_reverse)

    def copy_tree_selection(self, event=None):
        """Copy selected rows from the mod table as tab-separated text."""
        if not hasattr(self, "tree"):
            return "break"

        selection = self.tree.selection()
        if not selection:
            return "break"

        lines = []
        for item_id in selection:
            row = self.row_by_item_id.get(item_id)
            if not row:
                continue

            number = str(row.get("number", ""))
            active_mark = self.get_activation_mark_for_row(row)
            name = str(row.get("name", ""))
            links = "" if row.get("is_separator") else str(row.get("direct_link_count", 0))
            author = str(row.get("author", ""))
            version = str(row.get("version", ""))
            size_mb = str(row.get("size_mb", ""))
            lines.append("\t".join([number, active_mark, name, links, author, version, size_mb]))

        if not lines:
            return "break"

        self.root.clipboard_clear()
        self.root.clipboard_append("\n".join(lines))
        self.set_status(f"Copied {len(lines)} table row(s) to clipboard")
        return "break"

    def export_modlist_txt(self):
        """Export the current MO2 modlist.txt load-order file."""
        if not self.mod_rows:
            messagebox.showwarning("No Data", "Load a Wabbajack file first.")
            return

        include_external_only = False
        deactivate_external_only = False
        has_external_only = bool(self.external_only_compare_names)
        
        if has_external_only:
            dialog = tk.Toplevel(self.root)
            dialog.title("Export modlist.txt Options")
            dialog.geometry("450x220")
            dialog.resizable(False, False)
            dialog.transient(self.root)
            dialog.grab_set()
            
            frame = ttk.Frame(dialog, padding="20")
            frame.pack(fill=tk.BOTH, expand=True)
            
            ttk.Label(
                frame,
                text="A compare modlist is loaded with external-only entries.",
                font=("TkDefaultFont", 10)
            ).pack(anchor=tk.W, pady=(0, 10))
            
            ttk.Label(
                frame,
                text=f"External-only entries found: {len(self.external_only_compare_names)}",
                font=("TkDefaultFont", 9, "italic")
            ).pack(anchor=tk.W, pady=(0, 15))
            
            include_var = tk.BooleanVar(value=False)
            include_check = ttk.Checkbutton(
                frame,
                text="Include external-only mods in export (merged modlist)",
                variable=include_var
            )
            include_check.pack(anchor=tk.W, pady=(0, 10))
            
            deactivate_var = tk.BooleanVar(value=False)
            deactivate_check = ttk.Checkbutton(
                frame,
                text="Deactivate all external-only mods (prefix with -)",
                variable=deactivate_var,
                state=tk.DISABLED
            )
            deactivate_check.pack(anchor=tk.W, pady=(0, 20))
            
            def on_include_toggled(*args):
                deactivate_check.config(state=tk.NORMAL if include_var.get() else tk.DISABLED)
            
            include_var.trace("w", on_include_toggled)
            
            button_frame = ttk.Frame(frame)
            button_frame.pack(fill=tk.X)
            
            def on_ok():
                nonlocal include_external_only, deactivate_external_only
                include_external_only = include_var.get()
                deactivate_external_only = deactivate_var.get() and include_external_only
                dialog.destroy()
            
            export_button = ttk.Button(button_frame, text="Export", command=on_ok)
            export_button.pack(side=tk.RIGHT, padx=(5, 0))
            cancel_button = ttk.Button(button_frame, text="Cancel", command=dialog.destroy)
            cancel_button.pack(side=tk.RIGHT)

            self.add_tooltip(include_check, "Include entries that exist only in the comparison modlist.")
            self.add_tooltip(deactivate_check, "Write external-only entries with a leading - in the exported modlist.")
            self.add_tooltip(export_button, "Generate the modlist export with the selected options.")
            self.add_tooltip(cancel_button, "Close the export options dialog without exporting.")
            
            self.root.wait_window(dialog)
        
        output_path = filedialog.asksaveasfilename(
            title="Export modlist.txt",
            defaultextension=".txt",
            initialfile="modlist.txt",
            filetypes=[
                ("MO2 modlist", "modlist.txt"),
                ("Text files", "*.txt"),
                ("All files", "*.*"),
            ],
        )
        if not output_path:
            return

        try:
            output = Path(output_path)
            output.parent.mkdir(parents=True, exist_ok=True)
            modlist_text = self.build_modlist_txt(
                include_external_only=include_external_only,
                deactivate_external_only=deactivate_external_only
            )
            output.write_text(modlist_text, encoding="utf-8", newline="")
            line_count = self.count_nonempty_lines(modlist_text)
            base_count = len(self.mod_rows)
            external_only_count = len(self.external_only_compare_names) if include_external_only else 0

            export_type = "merged " if include_external_only else ""
            deactivate_note = " (external-only deactivated)" if deactivate_external_only else ""
            self.set_status(
                f"Exported {export_type}modlist.txt: {output.name}{deactivate_note} "
                f"({line_count} lines = {base_count} base + {external_only_count} external-only)"
            )
            messagebox.showinfo(
                "modlist.txt Exported",
                f"Saved {export_type}modlist.txt to:\n{output}{deactivate_note}\n\n"
                f"Line count: {line_count}\n"
                f"Base rows: {base_count}\n"
                f"External-only rows included: {external_only_count}\n"
                "Order: MO2 file order (reverse of viewer top-to-bottom)."
            )
        except Exception as e:
            messagebox.showerror("Error", f"Failed to export modlist.txt: {e}")

    def get_separator_folder_names(self):
        """Return base separator folder names in visible order, excluding external-only rows."""
        seen = set()
        names = []
        for row in self.mod_rows:
            if row.get("external_only") or row.get("compare_status") == "external_only":
                continue
            if not row.get("is_separator"):
                continue
            raw_name = str(row.get("raw_name") or "").strip()
            if not raw_name or raw_name in seen:
                continue
            seen.add(raw_name)
            names.append(raw_name)
        return names

    def parse_external_entry_name(self, name):
        """Parse external entry name and detect separators, return (display_name, is_separator)."""
        raw_name = str(name or "").strip()
        is_separator = raw_name.lower().endswith("_separator")
        display_name = raw_name[:-10] if is_separator else raw_name
        return display_name, is_separator

    def export_separators_zip(self):
        """Export all modlist separator folders (ending in _separator) as a zip."""
        if not self.mod_rows:
            messagebox.showwarning("No Data", "Load a Wabbajack file first.")
            return

        separator_names = self.get_separator_folder_names()
        if not separator_names:
            messagebox.showwarning("No Separators", "No _separator folders were found in the loaded modlist.")
            return

        modlist_name = self.sanitize_filename(self.modlist_data.get("Name", "modlist")) if self.modlist_data else "modlist"
        suggested_name = f"{modlist_name}_separators.zip"
        output_path = filedialog.asksaveasfilename(
            title="Export separators zip",
            defaultextension=".zip",
            initialfile=suggested_name,
            filetypes=[
                ("ZIP archive", "*.zip"),
                ("All files", "*.*"),
            ],
        )
        if not output_path:
            return

        try:
            output = Path(output_path)
            output.parent.mkdir(parents=True, exist_ok=True)

            with zipfile.ZipFile(output, mode="w", compression=zipfile.ZIP_DEFLATED) as zip_handle:
                for folder_name in separator_names:
                    folder_entry = folder_name.replace("\\", "/").rstrip("/") + "/"
                    zip_handle.writestr(folder_entry, "")

            self.set_status(f"Exported separators zip: {output.name} ({len(separator_names)} folders)")
            messagebox.showinfo(
                "Separators Exported",
                f"Saved separators zip to:\n{output}\n\nFolders exported: {len(separator_names)}"
            )
        except Exception as e:
            messagebox.showerror("Error", f"Failed to export separators zip: {e}")

    @staticmethod
    def normalize_mod_key(raw_name):
        """Build a case-insensitive comparison key for a mod folder name."""
        return str(raw_name or "").strip().lower()

    def parse_external_modlist_text(self, text):
        """Parse an external modlist and return names in display order (including separators)."""
        lines = []
        for raw_line in str(text).splitlines():
            line = raw_line.strip()
            if not line:
                continue
            # MO2 modlist comments begin with '#'.
            if line.startswith("#"):
                continue
            lines.append(line)
        if not lines:
            return []

        prefixed = sum(1 for line in lines if line[:1] in "+-")
        if prefixed >= max(1, len(lines) // 2):
            entries = self.parse_modlist_entries("\n".join(lines))
            return [entry["raw_name"] for entry in entries]

        names = []
        for line in lines:
            raw_name = line[1:].strip() if line[:1] in "+-" else line
            names.append(raw_name)
        return names

    def compare_with_external_modlist(self):
        """Colorize rows by comparing against an external modlist.txt order."""
        if not self.mod_rows:
            messagebox.showwarning("No Data", "Load a Wabbajack file first.")
            return

        file_path = filedialog.askopenfilename(
            title="Select external modlist.txt",
            filetypes=[("modlist", "modlist.txt"), ("Text files", "*.txt"), ("All files", "*.*")],
        )
        if not file_path:
            return

        try:
            text = Path(file_path).read_text(encoding="utf-8")
        except UnicodeDecodeError:
            try:
                text = Path(file_path).read_text(encoding="utf-8-sig")
            except Exception as e:
                messagebox.showerror("Error", f"Failed to read modlist.txt: {e}")
                return
        except Exception as e:
            messagebox.showerror("Error", f"Failed to read modlist.txt: {e}")
            return

        external_names = self.parse_external_modlist_text(text)
        if not external_names:
            messagebox.showwarning("No Entries", "The selected modlist.txt has no comparable mod entries.")
            return

        external_name_key_pairs = [(name, self.normalize_mod_key(name)) for name in external_names]
        external_keys = [key for _, key in external_name_key_pairs]
        wb_rows = list(self.mod_rows)
        wb_keys = [self.normalize_mod_key(row.get("raw_name") or row.get("name")) for row in wb_rows]

        for row in self.mod_rows:
            row["compare_status"] = None
        self.external_only_compare_names = []

        # Use an LCS-style matcher (same family of idea as git diff) so stable subsequences stay green.
        matcher = SequenceMatcher(None, wb_keys, external_keys, autojunk=False)
        green_indices = set()
        wb_to_external_index = {}
        for a_start, _b_start, size in matcher.get_matching_blocks():
            for index in range(a_start, a_start + size):
                green_indices.add(index)
        for a_start, b_start, size in matcher.get_matching_blocks():
            for offset in range(size):
                wb_to_external_index[a_start + offset] = b_start + offset

        external_counts = Counter(external_keys)
        consumed_counts = Counter()
        used_external_indices = set()
        for ext_index in wb_to_external_index.values():
            used_external_indices.add(ext_index)

        for index in green_indices:
            row = wb_rows[index]
            key = wb_keys[index]
            row["compare_status"] = "green"
            consumed_counts[key] += 1

        external_indices_by_key = {}
        for external_index, key in enumerate(external_keys):
            external_indices_by_key.setdefault(key, []).append(external_index)

        def claim_external_index_for_key(key):
            indices = external_indices_by_key.get(key, [])
            for external_index in indices:
                if external_index not in used_external_indices:
                    used_external_indices.add(external_index)
                    return external_index
            return None

        for index, row in enumerate(wb_rows):
            if row.get("compare_status") == "green":
                continue
            key = wb_keys[index]
            if consumed_counts[key] < external_counts[key]:
                row["compare_status"] = "yellow"
                consumed_counts[key] += 1
                external_index = claim_external_index_for_key(key)
                if external_index is not None:
                    wb_to_external_index[index] = external_index
            else:
                row["compare_status"] = "red"

        consumed_pairs = sorted((external_index, wb_index) for wb_index, external_index in wb_to_external_index.items())
        consumed_external_indices = [pair[0] for pair in consumed_pairs]

        external_only = []
        for external_index, (raw_name, _key) in enumerate(external_name_key_pairs):
            if external_index in used_external_indices:
                continue

            insert_pos = bisect_left(consumed_external_indices, external_index)
            prev_wb_index = consumed_pairs[insert_pos - 1][1] if insert_pos > 0 else None
            next_wb_index = consumed_pairs[insert_pos][1] if insert_pos < len(consumed_pairs) else None

            if prev_wb_index is not None and next_wb_index is not None:
                insert_index = max(prev_wb_index + 1, 0)
                insert_index = min(insert_index, next_wb_index)
            elif prev_wb_index is not None:
                insert_index = prev_wb_index + 1
            elif next_wb_index is not None:
                insert_index = next_wb_index
            else:
                insert_index = len(wb_rows)

            external_only.append(
                {
                    "name": raw_name,
                    "insert_index": int(insert_index),
                    "external_index": external_index,
                }
            )

        self.external_only_compare_names = external_only

        self.external_compare_path = file_path
        self.apply_filters_and_sort()
        self.update_legend_visibility()

        if hasattr(self, "clear_compare_button"):
            self.clear_compare_button.config(state=tk.NORMAL)
        if hasattr(self, "show_external_only_button"):
            has_external_only = self.has_external_only_rows()
            self.show_external_only_button.config(state=tk.NORMAL if has_external_only else tk.DISABLED)
            if not has_external_only:
                self.show_external_only_var.set(False)

        green_count = sum(1 for row in self.mod_rows if row.get("compare_status") == "green")
        yellow_count = sum(1 for row in self.mod_rows if row.get("compare_status") == "yellow")
        red_count = sum(1 for row in self.mod_rows if row.get("compare_status") == "red")
        external_only_count = len(self.external_only_compare_names)
        self.set_status(
            f"External compare: {green_count} green, {yellow_count} yellow, {red_count} red, {external_only_count} external-only"
        )

    def clear_compare_colors(self):
        """Clear external compare statuses and remove row colorization."""
        for row in self.mod_rows:
            row["compare_status"] = None

        self.external_only_compare_names = []
        self.external_compare_path = None
        if hasattr(self, "show_external_only_var"):
            self.show_external_only_var.set(False)
        self.apply_filters_and_sort()
        self.update_legend_visibility()

        if hasattr(self, "clear_compare_button"):
            self.clear_compare_button.config(state=tk.DISABLED)
        if hasattr(self, "show_external_only_button"):
            self.show_external_only_button.config(state=tk.DISABLED)

        self.set_status("Compare colors cleared")

    def save_modlist(self):
        """Save loaded data to a compact compressed modlist snapshot for fast reloads."""
        if not self.mod_rows:
            messagebox.showwarning("No Data", "Load a Wabbajack file first.")
            return

        default_name = "wabbajack_modlist.wvmodlist.json.gz"
        output_path = filedialog.asksaveasfilename(
            title="Save Modlist",
            defaultextension=".wvmodlist.json.gz",
            initialfile=default_name,
            filetypes=[
                ("Wabbajack Viewer Modlist", "*.wvmodlist.json.gz"),
                ("Legacy Viewer Cache", "*.wvjcache.json.gz"),
                ("All files", "*.*"),
            ],
        )
        if not output_path:
            return

        cache_rows = []
        for row in self.mod_rows:
            serialized_row = {
                "number": row.get("number"),
                "name": row.get("name"),
                "raw_name": row.get("raw_name"),
                "folder_key": row.get("folder_key", ""),
                "active": row.get("active", True),
                "activation_state": row.get("activation_state", "active" if row.get("active", True) else "inactive"),
                "is_separator": row.get("is_separator", False),
                "author": row.get("author", ""),
                "version": row.get("version", ""),
                "size_mb": row.get("size_mb", ""),
                "direct_link_count": row.get("direct_link_count", 0),
                "archive_hash": row.get("archive_hash", ""),
                "archive_name": row.get("archive_name", ""),
                "folder_meta": row.get("folder_meta", {}),
                "folder_files": row.get("folder_files", []),
                "source_archive_hashes": [
                    archive.get("Hash", "") for archive in row.get("source_archives", []) if archive.get("Hash")
                ],
            }
            cache_rows.append(serialized_row)

        payload = {
            "snapshot_version": 1,
            "source_wabbajack": self.current_wabbajack_file,
            "modlist_data": self.modlist_data,
            "archives": self.archives,
            "active_profile": self.active_profile,
            "all_profile_paths": self.all_profile_paths,
            "profile_ini_files": self.profile_ini_files,
            "mod_rows": cache_rows,
        }

        try:
            self.set_status("Saving modlist...")
            with gzip.open(output_path, "wt", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False)
            self.remember_saved_modlist_path(output_path)
            self.set_status(f"Modlist saved: {output_path.split('\\')[-1]}")
            messagebox.showinfo("Modlist Saved", f"Saved modlist to:\n{output_path}")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to save modlist: {e}")

    def load_saved_modlist(self):
        """Load a previously saved modlist snapshot without re-reading the full Wabbajack archive."""
        file_path = filedialog.askopenfilename(
            title="Load Modlist",
            filetypes=[
                ("Wabbajack Viewer Modlist", "*.wvmodlist.json.gz"),
                ("Legacy Viewer Cache", "*.wvjcache.json.gz"),
                ("All files", "*.*"),
            ],
        )
        if not file_path:
            return
        self.load_saved_modlist_from_path(file_path)

    def extract_and_load_modlist(self, wabbajack_path):
        """Load modlist and inline profile files directly from the Wabbajack archive."""
        try:
            self.loaded_from_saved_modlist = False
            self.set_status("Opening Wabbajack archive...")
            with zipfile.ZipFile(wabbajack_path, "r") as zip_ref:
                self.set_status("Reading modlist metadata...")
                modlist_files = [
                    name for name in zip_ref.namelist() if name == "modlist" or name == "modlist.json"
                ]
                if not modlist_files:
                    messagebox.showerror("Error", "No modlist file found in Wabbajack archive")
                    return

                modlist_file = modlist_files[0]
                with zip_ref.open(modlist_file, "r") as handle:
                    self.modlist_data = json.loads(handle.read().decode("utf-8"))

                self.archives = self.modlist_data.get("Archives", [])
                self.directives = self.modlist_data.get("Directives", [])
                self.archive_lookup = {}
                self.archive_by_hash = {}
                self.mod_folder_meta_cache = {}
                self.mod_folder_meta_paths = {}
                self.mod_folder_files = {}
                self.mod_folder_archive_hashes = {}
                self.synthetic_archive_by_hash = {}
                self.inline_file_sources = {}
                self.inline_text_cache = {}

                for archive in self.archives:
                    archive_hash = archive.get("Hash", "")
                    archive_name = archive.get("Name", "")
                    self.archive_lookup[archive_hash] = archive_name
                    self.archive_by_hash[archive_hash] = archive

                self.set_status("Processing directives...")
                self.process_directives()
                self.archive_name_index = self.build_archive_name_index()
                self.mod_folder_index = self.build_mod_folder_index()
                self.archive_normalized_index = self.build_archive_normalized_index()
                self.set_status("Indexing InlineFile sources...")
                self.inline_file_sources = self.load_inline_file_sources(zip_ref)
                self.mod_folder_meta_paths = self.build_mod_folder_meta_paths()
                self.set_status("Loading profile data...")
                self.load_profile_inline_data(zip_ref=zip_ref)

                self.set_status("Building mod rows...")
                self.update_ui_after_load()

                messagebox.showinfo(
                    "Success",
                    f"Successfully loaded modlist from:\n{wabbajack_path.split('\\\\')[-1]}",
                )
        except Exception as e:
            messagebox.showerror("Error", f"Failed to load Wabbajack file: {e}")

    def build_archive_name_index(self):
        """Create a fast lowercase name -> archive mapping."""
        name_index = {}
        for archive in self.archives:
            state = archive.get("State", {})
            candidates = [state.get("Name", ""), archive.get("Name", "")]
            for candidate in candidates:
                if candidate:
                    key = candidate.strip().lower()
                    if key and key not in name_index:
                        name_index[key] = archive
        return name_index

    def build_mod_folder_index(self):
        """Index MO2 mod folder names (mods\\<folder>) to source archive metadata."""
        folder_index = {}
        for folder_name, archive_hashes in self.mod_folder_archive_hashes.items():
            if not archive_hashes:
                continue
            archive = self.archive_by_hash.get(archive_hashes[0])
            if archive and folder_name not in folder_index:
                folder_index[folder_name] = archive

        return folder_index

    @staticmethod
    def normalize_name(value):
        """Normalize names for resilient matching (remove punctuation and spacing)."""
        return re.sub(r"[^a-z0-9]+", "", str(value).lower())

    def build_archive_normalized_index(self):
        """Build normalized name index from archive names and mod folder names."""
        normalized = {}
        for key, archive in self.archive_name_index.items():
            norm_key = self.normalize_name(key)
            if norm_key and norm_key not in normalized:
                normalized[norm_key] = archive
        for key, archive in self.mod_folder_index.items():
            norm_key = self.normalize_name(key)
            if norm_key and norm_key not in normalized:
                normalized[norm_key] = archive
        return normalized

    def find_archive_for_mod(self, mod_name):
        """Resolve a modlist entry to archive metadata using exact and normalized keys."""
        key = str(mod_name).strip().lower()
        if not key:
            return None

        archive = self.archive_name_index.get(key)
        if archive:
            return archive

        archive = self.mod_folder_index.get(key)
        if archive:
            return archive

        return self.archive_normalized_index.get(self.normalize_name(key))

    @staticmethod
    def parse_meta_ini(meta_text):
        """Parse MO2 meta.ini content into a compact metadata dict."""
        parser = configparser.RawConfigParser()
        parser.optionxform = str

        try:
            parser.read_string(meta_text)
        except configparser.Error:
            return {}

        general = dict(parser.items("General")) if parser.has_section("General") else {}
        return {
            "installation_file": general.get("installationFile", "").strip(),
            "repository": general.get("repository", "").strip(),
            "modid": general.get("modid", "").strip(),
            "version": general.get("version", "").strip(),
            "url": general.get("url", "").strip(),
            "comments": general.get("comments", "").strip(),
            "notes": general.get("notes", "").strip(),
            "game_name": general.get("gameName", "").strip(),
        }

    def build_mod_folder_meta_paths(self):
        """Build exact folder-name -> meta.ini InlineFile path mapping."""
        folder_meta_paths = {}
        for path in self.inline_file_sources.keys():
            normalized = path.replace("/", "\\")
            if not normalized.startswith("mods\\") or not normalized.endswith("\\meta.ini"):
                continue

            parts = normalized.split("\\")
            if len(parts) < 3:
                continue

            folder_name = parts[1].strip().lower()
            if folder_name:
                folder_meta_paths[folder_name] = normalized

        return folder_meta_paths

    def read_inline_text(self, target_path, zip_ref=None):
        """Read and cache a single InlineFile payload on demand."""
        normalized = str(target_path).replace("/", "\\").lower()
        if normalized in self.inline_text_cache:
            return self.inline_text_cache[normalized]

        source_data_id = self.inline_file_sources.get(normalized)
        if not source_data_id or not self.current_wabbajack_file:
            return None

        try:
            if zip_ref is None:
                with zipfile.ZipFile(self.current_wabbajack_file, "r") as local_zip_ref:
                    text = local_zip_ref.read(source_data_id).decode("utf-8", errors="replace")
            else:
                text = zip_ref.read(source_data_id).decode("utf-8", errors="replace")
        except Exception:
            return None

        self.inline_text_cache[normalized] = text
        return text

    def preload_folder_meta(self, parsed_entries):
        """Load all needed folder meta.ini files in one zip pass for large speed gains."""
        if not self.current_wabbajack_file:
            return

        folder_to_path = {}
        for entry in parsed_entries:
            folder_key = str(entry.get("raw_name", "")).strip().lower()
            if folder_key and folder_key not in self.mod_folder_meta_cache:
                meta_path = self.mod_folder_meta_paths.get(folder_key)
                if meta_path:
                    folder_to_path[folder_key] = meta_path

        if not folder_to_path:
            return

        items = list(folder_to_path.items())
        total = len(items)
        try:
            with zipfile.ZipFile(self.current_wabbajack_file, "r") as zip_ref:
                for index, (folder_key, meta_path) in enumerate(items, start=1):
                    if index % 100 == 0:
                        self.set_status(f"Reading folder metadata: {index}/{total}")
                    meta_text = self.read_inline_text(meta_path, zip_ref=zip_ref)
                    self.mod_folder_meta_cache[folder_key] = self.parse_meta_ini(meta_text) if meta_text else {}
        except Exception:
            pass

    def get_mod_folder_meta(self, raw_folder_name):
        """Load and cache a folder's meta.ini only when needed."""
        folder_key = str(raw_folder_name).strip().lower()
        if folder_key in self.mod_folder_meta_cache:
            return self.mod_folder_meta_cache[folder_key]

        meta_path = self.mod_folder_meta_paths.get(folder_key)
        if not meta_path:
            self.mod_folder_meta_cache[folder_key] = {}
            return self.mod_folder_meta_cache[folder_key]

        meta_text = self.read_inline_text(meta_path)
        self.mod_folder_meta_cache[folder_key] = self.parse_meta_ini(meta_text) if meta_text else {}
        return self.mod_folder_meta_cache[folder_key]

    def get_folder_source_archives(self, folder_name, meta):
        """Return all source archives that contributed files to a mod folder, primary first."""
        folder_key = str(folder_name).strip().lower()
        archive_hashes = list(self.mod_folder_archive_hashes.get(folder_key, []))
        archives = [self.archive_by_hash[archive_hash] for archive_hash in archive_hashes if archive_hash in self.archive_by_hash]
        embedded_archives = [self.synthetic_archive_by_hash[archive_hash] for archive_hash in archive_hashes if archive_hash in self.synthetic_archive_by_hash]

        installation_file = meta.get("installation_file", "") if meta else ""
        if installation_file:
            for index, archive in enumerate(archives):
                if archive.get("Name", "") == installation_file:
                    if index != 0:
                        archives.insert(0, archives.pop(index))
                    break

        return archives + embedded_archives

    def build_inline_archive_record(self, inline_hash, total_size=0):
        """Build a synthetic archive record for InlineFile payloads embedded in the .wabbajack."""
        folder_key = inline_hash.replace(self.INLINE_SOURCE_PREFIX, "", 1)
        display_folder = folder_key if folder_key else "mod folder"
        return {
            "Hash": inline_hash,
            "Name": f"Embedded files in .wabbajack ({display_folder})",
            "Size": int(total_size or 0),
            "State": {
                "$type": "InlineFile, EmbeddedInWabbajack",
                "Name": "Embedded in .wabbajack",
            },
        }

    def resolve_archive_reference(self, archive_hash, file_list=None):
        """Resolve archive hashes from real archives or synthetic embedded InlineFile sources."""
        if not archive_hash:
            return None
        if archive_hash in self.archive_by_hash:
            return self.archive_by_hash[archive_hash]
        if archive_hash in self.synthetic_archive_by_hash:
            return self.synthetic_archive_by_hash[archive_hash]
        if archive_hash.startswith(self.INLINE_SOURCE_PREFIX):
            total_size = 0
            if file_list:
                total_size = sum(
                    int(file_info.get("size", 0))
                    for file_info in file_list
                    if file_info.get("archive_hash", "") == archive_hash
                )
            archive = self.build_inline_archive_record(archive_hash, total_size=total_size)
            self.synthetic_archive_by_hash[archive_hash] = archive
            return archive
        return None

    @staticmethod
    def count_files_by_archive(file_list):
        """Count how many files each archive contributes to the selected folder."""
        counts = {}
        for file_info in file_list:
            archive_path = file_info.get("archive_path", "")
            archive_hash = file_info.get("archive_hash", "")
            if archive_hash:
                counts[archive_hash] = counts.get(archive_hash, 0) + 1
        return counts

    @staticmethod
    def build_archive_links(archive):
        """Build mod page and direct download links for an archive when possible."""
        if not archive:
            return {"page": "", "download": ""}

        state = archive.get("State", {})
        mod_type = state.get("$type", "")
        page_link = ""
        direct_link = ""

        if "NexusDownloader" in mod_type:
            mod_id = state.get("ModID")
            file_id = state.get("FileID")
            game_name = str(state.get("GameName", "skyrimspecialedition")).lower()
            if game_name == "skyrimspecialedition":
                game_name = "skyrimspecialedition"
            if mod_id:
                page_link = f"https://www.nexusmods.com/{game_name}/mods/{mod_id}"
            if mod_id and file_id:
                direct_link = f"https://www.nexusmods.com/{game_name}/mods/{mod_id}?tab=files&file_id={file_id}"
        elif "HttpDownloader" in mod_type:
            direct_link = state.get("Url", "")

        return {"page": page_link, "download": direct_link}

    def load_inline_file_sources(self, zip_ref):
        """Load InlineFile target path -> SourceDataID mapping without decoding payloads."""
        inline_sources_by_to = {}
        archive_entries = set(zip_ref.namelist())
        total = len(self.directives)

        for index, directive in enumerate(self.directives, start=1):
            if index % 2500 == 0:
                self.set_status(f"Indexing InlineFiles: {index}/{total}")

            if directive.get("$type") != "InlineFile":
                continue

            target_path = directive.get("To")
            source_data_id = directive.get("SourceDataID")
            if not target_path or not source_data_id:
                continue

            if source_data_id not in archive_entries:
                continue

            normalized = str(target_path).replace("/", "\\").lower()
            inline_sources_by_to[normalized] = source_data_id

        return inline_sources_by_to

    def load_profile_inline_data(self, preferred_profile=None, zip_ref=None):
        """Pick a profile modlist and load ini files for that profile."""
        self.active_profile = None
        self.profile_modlist_text = ""
        self.profile_ini_files = {}

        modlist_paths = sorted(
            path
            for path in self.inline_file_sources.keys()
            if path.endswith("\\modlist.txt")
        )
        self.all_profile_paths = modlist_paths

        if not modlist_paths:
            return

        if preferred_profile:
            match = next(
                (p for p in modlist_paths if p.rsplit("\\", 1)[0].split("\\")[-1] == preferred_profile),
                None,
            )
            selected_modlist_path = match if match else modlist_paths[0]
        else:
            selected_modlist_path = modlist_paths[0]

        self.profile_modlist_text = self.read_inline_text(selected_modlist_path, zip_ref=zip_ref) or ""
        self.active_profile = selected_modlist_path.rsplit("\\", 1)[0]

        for ini_name in ("skyrim.ini", "skyrimprefs.ini", "skyrimcustom.ini"):
            ini_path = f"{self.active_profile}\\{ini_name}"
            ini_text = self.read_inline_text(ini_path, zip_ref=zip_ref)
            if ini_text is not None:
                self.profile_ini_files[ini_name] = ini_text

    def on_profile_selected(self, event=None):
        """Switch to the selected profile."""
        selected = self.profile_var.get()
        self.load_profile_inline_data(preferred_profile=selected)
        self.update_ui_after_load()

    def update_ui_after_load(self, prepare_rows=True):
        """Update UI elements after loading modlist data."""
        if self.modlist_data:
            self.root.title(
                f"Wabbajack Manual Installation Guide - {self.modlist_data.get('Name', 'Unknown Modlist')}"
            )

        if hasattr(self, "info_text") and self.modlist_data:
            info_text = (
                f"Name: {self.modlist_data.get('Name', 'Unknown')}\n"
                f"Author: {self.modlist_data.get('Author', 'Unknown')}\n"
                f"Version: {self.modlist_data.get('Version', 'Unknown')}\n"
                f"Game: {self.modlist_data.get('GameType', 'Unknown')}\n"
                f"Description: {self.modlist_data.get('Description', 'No description available')}\n"
                f"Wabbajack Version: {self.modlist_data.get('WabbajackVersion', 'Unknown')}\n"
                f"Archives: {len(self.archives)}"
            )
            if self.active_profile:
                profile_name = self.active_profile.split("\\")[-1]
                info_text += f"\nProfile: {profile_name}"
            if self.loaded_from_saved_modlist:
                info_text += "\nLoaded from saved modlist"

            self.info_text.config(state=tk.NORMAL)
            self.info_text.delete(1.0, tk.END)
            self.info_text.insert(1.0, info_text)
            self.info_text.config(state=tk.DISABLED)
            self.update_info_height(info_text)

        self.refresh_ini_buttons()
        self.refresh_profile_selector()
        if prepare_rows:
            self.prepare_mod_rows()
        else:
            self.apply_filters_and_sort()

        if hasattr(self, "export_modlist_button"):
            self.export_modlist_button.config(state=tk.NORMAL if self.mod_rows else tk.DISABLED)
        if hasattr(self, "export_separators_button"):
            has_separators = bool(self.get_separator_folder_names()) if self.mod_rows else False
            self.export_separators_button.config(state=tk.NORMAL if has_separators else tk.DISABLED)
        if hasattr(self, "compare_modlist_button"):
            self.compare_modlist_button.config(state=tk.NORMAL if self.mod_rows else tk.DISABLED)
        if hasattr(self, "clear_compare_button"):
            has_compare = self.has_active_compare_colors()
            self.clear_compare_button.config(state=tk.NORMAL if has_compare else tk.DISABLED)
        if hasattr(self, "show_external_only_button"):
            has_external_only = self.has_external_only_rows()
            self.show_external_only_button.config(state=tk.NORMAL if has_external_only else tk.DISABLED)
            if not has_external_only:
                self.show_external_only_var.set(False)
        self.update_legend_visibility()

        if hasattr(self, "status_var"):
            self.status_var.set("Modlist loaded")

    def update_info_height(self, content_text):
        """Resize modlist info area to content lines."""
        if not hasattr(self, "info_text"):
            return
        line_count = max(4, len(str(content_text).splitlines()) + 1)
        self.info_text.configure(height=line_count)

    def toggle_modlist_info(self):
        """Collapse or expand modlist information frame."""
        if not hasattr(self, "info_frame"):
            return

        if self.info_frame.winfo_ismapped():
            self.info_frame.grid_remove()
            self.info_toggle_button.config(text="Show Modlist Info")
        else:
            self.info_frame.grid(row=1, column=0, sticky=(tk.W, tk.E), pady=(0, 10))
            self.info_toggle_button.config(text="Hide Modlist Info")

    def process_directives(self):
        """Process archive-backed and embedded directives for folder-level source relationships."""
        total = len(self.directives)
        for index, directive in enumerate(self.directives, start=1):
            directive_type = directive.get("$type")
            if directive_type not in self.SOURCE_DIRECTIVE_TYPES and directive_type != "InlineFile":
                continue

            if index % 2500 == 0:
                self.set_status(f"Processing directives: {index}/{total}")

            archive_hash = ""
            archive_path = ""
            source_data_id = ""
            if directive_type in self.SOURCE_DIRECTIVE_TYPES:
                archive_hash = (
                    directive.get("ArchiveHashPath", [""])[0]
                    if directive.get("ArchiveHashPath")
                    else ""
                )
                archive_path = (
                    directive.get("ArchiveHashPath", ["", ""])[1]
                    if len(directive.get("ArchiveHashPath", [])) > 1
                    else ""
                )
            elif directive_type == "InlineFile":
                source_data_id = str(directive.get("SourceDataID", "")).strip()
                if not source_data_id:
                    continue
                archive_path = f"[Embedded InlineFile] {source_data_id}"

            target_path = str(directive.get("To", "")).replace("/", "\\")
            if target_path.lower().startswith("mods\\"):
                if self.is_mod_meta_file_path(target_path):
                    continue
                parts = target_path.split("\\")
                if len(parts) >= 2:
                    folder_name = parts[1].strip().lower()
                    if folder_name:
                        if directive_type == "InlineFile":
                            archive_hash = f"{self.INLINE_SOURCE_PREFIX}{folder_name}"
                            if archive_hash not in self.synthetic_archive_by_hash:
                                self.synthetic_archive_by_hash[archive_hash] = self.build_inline_archive_record(
                                    archive_hash,
                                    total_size=directive.get("Size", 0),
                                )
                            else:
                                self.synthetic_archive_by_hash[archive_hash]["Size"] = int(
                                    self.synthetic_archive_by_hash[archive_hash].get("Size", 0)
                                ) + int(directive.get("Size", 0) or 0)

                        self.mod_folder_files.setdefault(folder_name, []).append(
                            {
                                "target_path": target_path,
                                "archive_path": archive_path,
                                "archive_hash": archive_hash,
                                "source_data_id": source_data_id,
                                "size": directive.get("Size", 0),
                                "directive_type": directive_type or "",
                            }
                        )
                        folder_hashes = self.mod_folder_archive_hashes.setdefault(folder_name, [])
                        if archive_hash and archive_hash not in folder_hashes:
                            folder_hashes.append(archive_hash)

    def parse_modlist_entries(self, modlist_text):
        """Parse modlist.txt entries and convert reverse order to display order."""
        lines = [line.strip() for line in modlist_text.splitlines() if line.strip()]
        entries = []

        for line in lines:
            prefix = line[0] if line and line[0] in "+-*" else "+"
            raw_name = line[1:].strip() if line and line[0] in "+-*" else line
            is_separator = raw_name.lower().endswith("_separator")
            display_name = raw_name[:-10] if is_separator else raw_name

            entries.append(
                {
                    "raw_line": line,
                    "name": display_name,
                    "raw_name": raw_name,
                    "active": prefix in "+*",
                    "activation_state": self.get_activation_state_from_prefix(prefix),
                    "is_separator": is_separator,
                }
            )

        # Wabbajack stores this list in reverse order for MO2 display purposes.
        entries.reverse()
        return entries

    def prepare_mod_rows(self):
        """Build table rows from profile modlist order, with archive metadata if available."""
        self.mod_rows = []
        self.external_only_compare_names = []
        self.external_compare_path = None
        if hasattr(self, "show_external_only_var"):
            self.show_external_only_var.set(False)

        if self.profile_modlist_text:
            parsed_entries = self.parse_modlist_entries(self.profile_modlist_text)
            self.set_status("Reading folder metadata...")
            self.preload_folder_meta(parsed_entries)
            total_entries = len(parsed_entries)
            for idx, entry in enumerate(parsed_entries, start=1):
                if idx % 100 == 0:
                    self.set_status(f"Building rows: {idx}/{total_entries}")

                folder_key = entry["raw_name"].strip().lower()
                folder_meta = self.get_mod_folder_meta(entry["raw_name"])
                source_archives = self.get_folder_source_archives(folder_key, folder_meta)
                archive = source_archives[0] if source_archives else self.find_archive_for_mod(entry["raw_name"])
                state = archive.get("State", {}) if archive else {}

                archive_hash = archive.get("Hash", "") if archive else ""
                archive_name = folder_meta.get("installation_file") or (archive.get("Name", "") if archive else entry["name"])
                author = (state.get("Author") or "-") if archive else "-"
                version = folder_meta.get("version") or state.get("Version", "")
                total_size = sum(source_archive.get("Size", 0) for source_archive in source_archives)
                size = total_size or (archive.get("Size", 0) if archive else 0)
                size_mb = f"{(size / (1024 * 1024)):.1f}" if size else ""
                folder_files = self.mod_folder_files.get(folder_key, [])
                source_file_counts = self.count_files_by_archive(folder_files)
                direct_link_count = sum(
                    1 for source_archive in source_archives if self.build_archive_links(source_archive)["download"]
                )

                self.mod_rows.append(
                    {
                        "number": idx,
                        "name": entry["name"],
                        "raw_name": entry["raw_name"],
                        "folder_key": folder_key,
                        "active": entry["active"],
                        "activation_state": entry.get("activation_state", "active"),
                        "is_separator": entry["is_separator"],
                        "author": author,
                        "version": version,
                        "size_mb": size_mb,
                        "direct_link_count": direct_link_count,
                        "archive_hash": archive_hash,
                        "archive_name": archive_name,
                        "folder_meta": folder_meta,
                        "source_archives": source_archives,
                        "folder_files": folder_files,
                        "source_file_counts": source_file_counts,
                        "archive": archive,
                        "compare_status": None,
                    }
                )
        else:
            # Fallback if modlist.txt is not available.
            for idx, archive in enumerate(self.archives, start=1):
                state = archive.get("State", {})
                name = state.get("Name", archive.get("Name", "Unknown Mod"))
                size = archive.get("Size", 0)
                self.mod_rows.append(
                    {
                        "number": idx,
                        "name": name,
                        "raw_name": name,
                        "active": True,
                        "activation_state": "active",
                        "is_separator": False,
                        "author": state.get("Author", "Unknown"),
                        "version": state.get("Version", ""),
                        "size_mb": f"{(size / (1024 * 1024)):.1f}" if size else "",
                        "direct_link_count": 1 if self.build_archive_links(archive)["download"] else 0,
                        "archive_hash": archive.get("Hash", ""),
                        "archive_name": archive.get("Name", ""),
                        "archive": archive,
                        "compare_status": None,
                    }
                )

        self.apply_filters_and_sort()

    def setup_ui(self):
        """Setup the main UI components."""
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))

        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        main_frame.columnconfigure(0, weight=1)
        main_frame.rowconfigure(2, weight=1)

        header_frame = ttk.Frame(main_frame)
        header_frame.grid(row=0, column=0, sticky=(tk.W, tk.E), pady=(0, 10))

        self.title_label = ttk.Label(
            header_frame,
            text="Wabbajack Manual Installation Guide",
            font=("Arial", 16, "bold"),
        )
        self.title_label.pack()

        button_frame = ttk.Frame(header_frame)
        button_frame.pack(fill=tk.X, pady=(5, 0))
        self.header_button_frame = button_frame
        self.header_toolbar_widgets = []

        load_button = ttk.Button(button_frame, text="Load Wabbajack File", command=self.load_wabbajack_file)
        self.header_toolbar_widgets.append((load_button, (0, 8)))
        self.add_tooltip(load_button, "Open a .wabbajack file and build the mod list from its embedded data.")

        load_cache_button = ttk.Button(button_frame, text="Load Modlist", command=self.load_saved_modlist)
        self.header_toolbar_widgets.append((load_cache_button, (0, 8)))
        self.add_tooltip(load_cache_button, "Load a saved viewer snapshot instead of rereading the archive.")

        save_cache_button = ttk.Button(button_frame, text="Save Modlist", command=self.save_modlist)
        self.header_toolbar_widgets.append((save_cache_button, (0, 8)))
        self.add_tooltip(save_cache_button, "Save the current loaded data to a compact snapshot file.")

        self.info_toggle_button = ttk.Button(
            button_frame,
            text="Hide Modlist Info",
            command=self.toggle_modlist_info,
        )
        self.header_toolbar_widgets.append((self.info_toggle_button, (0, 8)))
        self.add_tooltip(self.info_toggle_button, "Show or hide the modlist metadata pane.")

        self.export_modlist_button = ttk.Button(
            button_frame,
            text="Export modlist.txt",
            command=self.export_modlist_txt,
            state=tk.DISABLED,
        )
        self.header_toolbar_widgets.append((self.export_modlist_button, (0, 8)))
        self.add_tooltip(self.export_modlist_button, "Export the current load order as an MO2 modlist.txt file.")

        self.export_separators_button = ttk.Button(
            button_frame,
            text="Export Separators",
            command=self.export_separators_zip,
            state=tk.DISABLED,
        )
        self.header_toolbar_widgets.append((self.export_separators_button, (0, 8)))
        self.add_tooltip(self.export_separators_button, "Export separator folders as an empty zip scaffold.")

        self.compare_modlist_button = ttk.Button(
            button_frame,
            text="Compare modlist.txt",
            command=self.compare_with_external_modlist,
            state=tk.DISABLED,
        )
        self.header_toolbar_widgets.append((self.compare_modlist_button, (0, 8)))
        self.add_tooltip(self.compare_modlist_button, "Compare the loaded list with an external modlist.txt.")

        self.clear_compare_button = ttk.Button(
            button_frame,
            text="Clear Compare Colors",
            command=self.clear_compare_colors,
            state=tk.DISABLED,
        )
        self.header_toolbar_widgets.append((self.clear_compare_button, (0, 8)))
        self.add_tooltip(self.clear_compare_button, "Clear compare coloring and restore the normal view.")

        self.show_external_only_var = tk.BooleanVar(value=False)
        self.show_external_only_button = ttk.Checkbutton(
            button_frame,
            text="Show External-Only",
            variable=self.show_external_only_var,
            command=self.on_toggle_show_external_only,
            state=tk.DISABLED,
        )
        self.header_toolbar_widgets.append((self.show_external_only_button, (0, 8)))
        self.add_tooltip(self.show_external_only_button, "Show mods that only exist in the external comparison list.")

        self.profile_label = ttk.Label(button_frame, text="Profile:")
        self.header_toolbar_widgets.append((self.profile_label, (8, 2)))
        self.add_tooltip(self.profile_label, "Choose which profile folder inside the archive to load.")
        self.profile_var = tk.StringVar()
        self.profile_combobox = ttk.Combobox(
            button_frame, textvariable=self.profile_var, state="readonly", width=20
        )
        self.header_toolbar_widgets.append((self.profile_combobox, (0, 8)))
        self.add_tooltip(self.profile_combobox, "Select a different Wabbajack profile from the archive.")
        self.profile_combobox.bind("<<ComboboxSelected>>", self.on_profile_selected)
        self.profile_combobox.config(state=tk.DISABLED)

        self.ini_buttons = {}
        for ini_name in ("skyrim.ini", "skyrimprefs.ini", "skyrimcustom.ini"):
            button = ttk.Button(
                button_frame,
                text=f"View {ini_name}",
                command=lambda value=ini_name: self.open_ini_viewer(value),
                state=tk.DISABLED,
            )
            self.header_toolbar_widgets.append((button, (0, 8)))
            self.add_tooltip(button, f"Open the embedded {ini_name} from the loaded Wabbajack data.")
            self.ini_buttons[ini_name] = button

        self.layout_header_buttons()
        self.header_button_frame.bind("<Configure>", self.on_header_button_frame_configure)

        self.info_frame = ttk.LabelFrame(main_frame, text="Modlist Information", padding="5")
        self.info_frame.grid(row=1, column=0, sticky=(tk.W, tk.E), pady=(0, 10))

        self.info_text = scrolledtext.ScrolledText(self.info_frame, height=8, wrap=tk.WORD, font=("TkDefaultFont", 9))
        self.info_text.insert(1.0, "No modlist loaded. Click 'Load Wabbajack File' to begin.")
        self.info_text.config(state=tk.DISABLED)
        self.info_text.pack(fill=tk.BOTH, expand=False)
        self.update_info_height("No modlist loaded. Click 'Load Wabbajack File' to begin.")

        content_frame = ttk.Frame(main_frame)
        content_frame.grid(row=2, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        content_frame.columnconfigure(0, weight=1)
        content_frame.rowconfigure(2, weight=1)

        search_frame = ttk.Frame(content_frame, padding=(6, 4))
        search_frame.grid(row=0, column=0, sticky=(tk.W, tk.E))
        ttk.Label(search_frame, text="Search:").pack(side=tk.LEFT, padx=(0, 6))
        self.search_var = tk.StringVar()
        self.search_var.trace("w", self.filter_mods)
        search_entry = ttk.Entry(search_frame, textvariable=self.search_var, width=56)
        search_entry.pack(side=tk.LEFT)

        self.legend_frame = ttk.Frame(content_frame, padding=(6, 0, 6, 4))
        self.legend_frame.grid(row=1, column=0, sticky=(tk.W, tk.E))
        ttk.Label(self.legend_frame, text="Legend:").pack(side=tk.LEFT, padx=(0, 4))
        tk.Label(self.legend_frame, text="Green = in external and in order", fg="#1f7a1f").pack(side=tk.LEFT, padx=(0, 8))
        tk.Label(self.legend_frame, text="Yellow = in external but wrong order", fg="#b8860b").pack(side=tk.LEFT, padx=(0, 8))
        tk.Label(self.legend_frame, text="Red = missing from external", fg="#b22222").pack(side=tk.LEFT)
        tk.Label(self.legend_frame, text="Blue = external-only (not in Wabbajack)", fg="#1f4d99").pack(side=tk.LEFT, padx=(8, 0))
        self.legend_frame.grid_remove()

        paned_window = ttk.PanedWindow(content_frame, orient=tk.VERTICAL)
        paned_window.grid(row=2, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))

        mod_frame = ttk.LabelFrame(paned_window, text="Mod List", padding="10")
        mod_frame.columnconfigure(0, weight=1)
        mod_frame.rowconfigure(0, weight=1)

        self.tree = ttk.Treeview(
            mod_frame,
            columns=("active", "name", "sources", "author", "version", "size"),
            show="tree headings",
        )
        self.tree.heading("#0", text="#", command=lambda: self.sort_by("number"))
        self.tree.heading("active", text="Active", command=lambda: self.sort_by("active"))
        self.tree.heading("name", text="Mod Name", command=lambda: self.sort_by("name"))
        self.tree.heading("sources", text="Sources", command=lambda: self.sort_by("sources"))
        self.tree.heading("author", text="Author", command=lambda: self.sort_by("author"))
        self.tree.heading("version", text="Version", command=lambda: self.sort_by("version"))
        self.tree.heading("size", text="Size (MB)", command=lambda: self.sort_by("size"))

        self.tree.column("#0", width=50, minwidth=40, anchor=tk.CENTER, stretch=False)
        self.tree.column("active", width=55, minwidth=50, anchor=tk.CENTER, stretch=False)
        self.tree.column("name", width=330, minwidth=180)
        self.tree.column("sources", width=70, minwidth=55, anchor=tk.CENTER, stretch=False)
        self.tree.column("author", width=130, minwidth=80)
        self.tree.column("version", width=80, minwidth=50)
        self.tree.column("size", width=70, minwidth=55, anchor=tk.E, stretch=False)

        tree_scroll = ttk.Scrollbar(mod_frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=tree_scroll.set)

        self.tree.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        tree_scroll.grid(row=0, column=1, sticky=(tk.N, tk.S))

        self.separator_font = tkfont.Font(font="TkDefaultFont")
        self.separator_font.configure(size=self.separator_font.cget("size") + 1, weight="bold")
        self.tree.tag_configure("separator", font=self.separator_font)
        self.tree.tag_configure("compare_green", foreground="#1f7a1f")
        self.tree.tag_configure("compare_yellow", foreground="#b8860b")
        self.tree.tag_configure("compare_red", foreground="#b22222")
        self.tree.tag_configure("compare_external_only", foreground="#1f4d99")

        details_frame = ttk.LabelFrame(paned_window, text="Mod Details", padding="10")
        details_frame.columnconfigure(0, weight=1)
        details_frame.rowconfigure(0, weight=1)

        self.notebook = ttk.Notebook(details_frame)
        self.notebook.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))

        overview_frame = ttk.Frame(self.notebook)
        self.notebook.add(overview_frame, text="Overview")

        self.overview_text = scrolledtext.ScrolledText(overview_frame, wrap=tk.WORD)
        self.overview_text.pack(fill=tk.BOTH, expand=True)

        self.overview_text.tag_configure("link", foreground="blue", underline=True)
        self.overview_text.tag_bind("link", "<Button-1>", self.open_link)
        self.overview_text.tag_bind("link", "<Enter>", lambda e: self.overview_text.config(cursor="hand2"))
        self.overview_text.tag_bind("link", "<Leave>", lambda e: self.overview_text.config(cursor=""))

        sources_frame = ttk.Frame(self.notebook)
        self.notebook.add(sources_frame, text="Sources")

        sources_search_frame = ttk.Frame(sources_frame)
        sources_search_frame.grid(row=0, column=0, sticky=(tk.W, tk.E), pady=(0, 6))
        ttk.Label(sources_search_frame, text="Search Sources:").pack(side=tk.LEFT, padx=(0, 6))
        self.sources_search_var = tk.StringVar()
        self.sources_search_var.trace("w", self.on_sources_search_changed)
        ttk.Entry(sources_search_frame, textvariable=self.sources_search_var, width=48).pack(side=tk.LEFT)
        self.sources_hide_mohidden_var = tk.BooleanVar(value=False)
        self.sources_hide_mohidden_check = ttk.Checkbutton(
            sources_search_frame,
            text="Hide .mohidden",
            variable=self.sources_hide_mohidden_var,
            command=self.on_sources_filter_changed,
        )
        self.sources_hide_mohidden_check.pack(side=tk.LEFT, padx=(12, 0))

        self.sources_tree = ttk.Treeview(
            sources_frame,
            columns=("target_path", "source_path", "size", "kind"),
            show="tree headings",
        )
        self.sources_tree.heading("#0", text="Source Archive", command=lambda: self.on_sources_sort_requested("source"))
        self.sources_tree.heading("target_path", text="Install Path", command=lambda: self.on_sources_sort_requested("target_path"))
        self.sources_tree.heading("source_path", text="Source Path", command=lambda: self.on_sources_sort_requested("source_path"))
        self.sources_tree.heading("size", text="Size (bytes)", command=lambda: self.on_sources_sort_requested("size"))
        self.sources_tree.heading("kind", text="Directive", command=lambda: self.on_sources_sort_requested("kind"))

        self.sources_tree.column("#0", width=260, minwidth=180)
        self.sources_tree.column("target_path", width=360, minwidth=240)
        self.sources_tree.column("source_path", width=420, minwidth=260)
        self.sources_tree.column("size", width=110, minwidth=90, anchor=tk.E)
        self.sources_tree.column("kind", width=110, minwidth=90, anchor=tk.CENTER)

        sources_scroll = ttk.Scrollbar(sources_frame, orient=tk.VERTICAL, command=self.sources_tree.yview)
        self.sources_tree.configure(yscrollcommand=sources_scroll.set)

        self.sources_tree.grid(row=1, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        sources_scroll.grid(row=1, column=1, sticky=(tk.N, tk.S))

        sources_frame.columnconfigure(0, weight=1)
        sources_frame.rowconfigure(1, weight=1)
        self.sources_tree.bind("<Double-1>", self.on_source_activate)

        files_frame = ttk.Frame(self.notebook)
        self.notebook.add(files_frame, text="Files")

        files_search_frame = ttk.Frame(files_frame)
        files_search_frame.grid(row=0, column=0, sticky=(tk.W, tk.E), pady=(0, 6))
        ttk.Label(files_search_frame, text="Search Files:").pack(side=tk.LEFT, padx=(0, 6))
        self.files_search_var = tk.StringVar()
        self.files_search_var.trace("w", self.on_files_search_changed)
        ttk.Entry(files_search_frame, textvariable=self.files_search_var, width=48).pack(side=tk.LEFT)
        self.files_hide_mohidden_var = tk.BooleanVar(value=False)
        self.files_hide_mohidden_check = ttk.Checkbutton(
            files_search_frame,
            text="Hide .mohidden",
            variable=self.files_hide_mohidden_var,
            command=self.on_files_filter_changed,
        )
        self.files_hide_mohidden_check.pack(side=tk.LEFT, padx=(12, 0))

        self.files_tree = ttk.Treeview(files_frame, columns=("source_mod", "source_path", "size"), show="tree headings")
        self.files_tree.heading("#0", text="Target Path", command=lambda: self.on_files_sort_requested("target_path"))
        self.files_tree.heading("source_mod", text="Source Mod", command=lambda: self.on_files_sort_requested("source_mod"))
        self.files_tree.heading("source_path", text="Source Path", command=lambda: self.on_files_sort_requested("source_path"))
        self.files_tree.heading("size", text="Size (bytes)", command=lambda: self.on_files_sort_requested("size"))

        self.files_tree.column("#0", width=300, minwidth=200)
        self.files_tree.column("source_mod", width=210, minwidth=140)
        self.files_tree.column("source_path", width=300, minwidth=200)
        self.files_tree.column("size", width=100, minwidth=80)

        files_scroll = ttk.Scrollbar(files_frame, orient=tk.VERTICAL, command=self.files_tree.yview)
        self.files_tree.configure(yscrollcommand=files_scroll.set)

        self.files_tree.grid(row=1, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        files_scroll.grid(row=1, column=1, sticky=(tk.N, tk.S))

        self.add_tooltip(self.sources_hide_mohidden_check, "Hide source file entries whose path ends with .mohidden.")
        self.add_tooltip(self.files_hide_mohidden_check, "Hide file entries whose path ends with .mohidden.")

        files_frame.columnconfigure(0, weight=1)
        files_frame.rowconfigure(1, weight=1)

        paned_window.add(mod_frame, weight=2)
        paned_window.add(details_frame, weight=1)

        self.tree.bind("<<TreeviewSelect>>", self.on_mod_select)
        self.tree.bind("<Button-3>", self.on_mod_right_click)
        self.tree.bind("<Control-c>", self.copy_tree_selection)
        self.tree.bind("<Control-C>", self.copy_tree_selection)

        self.populate_mod_list()
        self.update_sort_indicators()

        status_frame = ttk.Frame(main_frame)
        status_frame.grid(row=3, column=0, sticky=(tk.W, tk.E), pady=(10, 0))

        self.status_var = tk.StringVar()
        self.status_var.set("No modlist loaded")
        ttk.Label(status_frame, textvariable=self.status_var).pack(side=tk.LEFT)

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        ttk.Label(status_frame, text=f"Generated: {timestamp}").pack(side=tk.RIGHT)

        def set_sash_position():
            try:
                paned_window.sashpos(0, 420)
            except Exception:
                pass

        self.root.after(100, set_sash_position)

    def on_header_button_frame_configure(self, event=None):
        """Reflow toolbar buttons whenever header width changes."""
        self.layout_header_buttons()

    def layout_header_buttons(self):
        """Wrap toolbar widgets to new rows when the window is narrow."""
        if not hasattr(self, "header_toolbar_widgets") or not hasattr(self, "header_button_frame"):
            return

        frame = self.header_button_frame
        frame.update_idletasks()
        available_width = max(320, int(frame.winfo_width() or frame.winfo_reqwidth() or 320))

        for widget, _pad in self.header_toolbar_widgets:
            widget.grid_forget()

        row_index = 0
        col_index = 0
        used_width = 0
        row_height = 0
        row_spacing = 4

        for widget, pad in self.header_toolbar_widgets:
            pad_left, pad_right = pad
            widget_width = int(widget.winfo_reqwidth()) + pad_left + pad_right
            widget_height = int(widget.winfo_reqheight())

            if col_index > 0 and used_width + widget_width > available_width:
                row_index += 1
                col_index = 0
                used_width = 0
                row_height = 0

            widget.grid(row=row_index, column=col_index, padx=pad, pady=(0, row_spacing), sticky=tk.W)
            used_width += widget_width
            row_height = max(row_height, widget_height)
            col_index += 1

    def has_active_compare_colors(self):
        """Return True when any row currently has a compare color status."""
        return any(row.get("compare_status") in {"green", "yellow", "red"} for row in self.mod_rows) or bool(self.external_only_compare_names)

    def has_external_only_rows(self):
        """Return True when compare has unmatched external entries."""
        return bool(self.external_only_compare_names)

    def on_toggle_show_external_only(self):
        """Re-render list when the external-only visibility toggle changes."""
        if self.show_external_only_var.get() and self.has_external_only_rows():
            if self.sort_state_before_external_only is None:
                self.sort_state_before_external_only = (self.sort_column, self.sort_reverse)
            self.sort_column = "number"
            self.sort_reverse = False
            self.set_status("Sorting is locked while Show External-Only is enabled (showing compare placement order).")
        else:
            if self.sort_state_before_external_only is not None:
                self.sort_column, self.sort_reverse = self.sort_state_before_external_only
                self.sort_state_before_external_only = None
        self.apply_filters_and_sort()

    def is_external_only_sort_locked(self):
        """Return True when sort changes should be blocked to preserve compare placement."""
        return bool(
            getattr(self, "show_external_only_var", None)
            and self.show_external_only_var.get()
            and self.has_external_only_rows()
        )

    def update_legend_visibility(self):
        """Show legend only when compare colors are active."""
        if not hasattr(self, "legend_frame"):
            return
        if self.has_active_compare_colors():
            self.legend_frame.grid()
        else:
            self.legend_frame.grid_remove()

    def refresh_profile_selector(self):
        """Populate and enable/disable the profile combobox."""
        if not hasattr(self, "profile_combobox"):
            return
        profiles = getattr(self, "all_profile_paths", [])
        profile_names = [p.rsplit("\\", 1)[0].split("\\")[-1] for p in profiles]
        if len(profile_names) > 1:
            self.profile_combobox["values"] = profile_names
            self.profile_combobox.config(state="readonly")
            current = self.active_profile.split("\\")[-1] if self.active_profile else ""
            if current in profile_names:
                self.profile_var.set(current)
            else:
                self.profile_var.set(profile_names[0])
        else:
            self.profile_combobox["values"] = profile_names
            self.profile_combobox.config(state=tk.DISABLED)
            if profile_names:
                self.profile_var.set(profile_names[0])

    def refresh_ini_buttons(self):
        """Enable/disable ini viewer buttons based on loaded profile files."""
        for ini_name, button in self.ini_buttons.items():
            if ini_name in self.profile_ini_files:
                button.config(state=tk.NORMAL)
            else:
                button.config(state=tk.DISABLED)

    def open_ini_viewer(self, ini_name):
        """Open a read-only window with the selected ini file contents."""
        content = self.profile_ini_files.get(ini_name)
        if content is None:
            messagebox.showwarning("INI Not Found", f"{ini_name} was not found in InlineFile profile data.")
            return

        window = tk.Toplevel(self.root)
        profile_name = self.active_profile.split("\\")[-1] if self.active_profile else "Unknown"
        window.title(f"{ini_name} - {profile_name}")
        window.geometry("900x700")

        text = scrolledtext.ScrolledText(window, wrap=tk.NONE)
        text.pack(fill=tk.BOTH, expand=True)
        text.insert(1.0, content)
        text.config(state=tk.DISABLED)

    def populate_mod_list(self):
        """Populate the treeview with current filtered/sorted rows."""
        self.apply_filters_and_sort()

    def apply_filters_and_sort(self):
        """Apply text filter and sorting, then render list rows."""
        search_text = self.search_var.get().lower().strip()

        rows = list(self.mod_rows)
        showing_external_only = bool(
            getattr(self, "show_external_only_var", None)
            and self.show_external_only_var.get()
            and self.external_only_compare_names
        )
        if showing_external_only and (self.sort_column != "number" or self.sort_reverse):
            self.sort_column = "number"
            self.sort_reverse = False
        external_render_order = {}
        if showing_external_only:
            inserts_by_index = {}
            for entry in self.external_only_compare_names:
                insert_index = int(entry.get("insert_index", len(self.mod_rows)))
                inserts_by_index.setdefault(insert_index, []).append(entry)

            interleaved_rows = []
            wb_index = 0
            external_counter = 0
            for row in self.mod_rows:
                if not row.get("is_separator"):
                    pending_entries = inserts_by_index.get(wb_index, [])
                    for entry in pending_entries:
                        external_counter += 1
                        name = str(entry.get("name", ""))
                        display_name, is_separator = self.parse_external_entry_name(name)
                        interleaved_rows.append(
                            {
                                "number": f"E{external_counter}",
                                "name": display_name,
                                "raw_name": name,
                                "folder_key": self.normalize_mod_key(name),
                                "active": False,
                                "is_separator": is_separator,
                                "author": "-",
                                "version": "",
                                "size_mb": "",
                                "direct_link_count": 0,
                                "archive_hash": "",
                                "archive_name": "",
                                "archive": None,
                                "folder_meta": {},
                                "source_archives": [],
                                "folder_files": [],
                                "source_file_counts": {},
                                "compare_status": "external_only",
                                "external_only": True,
                            }
                        )
                    wb_index += 1

                interleaved_rows.append(row)

            for entry in inserts_by_index.get(wb_index, []):
                external_counter += 1
                name = str(entry.get("name", ""))
                display_name, is_separator = self.parse_external_entry_name(name)
                interleaved_rows.append(
                    {
                        "number": f"E{external_counter}",
                        "name": display_name,
                        "raw_name": name,
                        "folder_key": self.normalize_mod_key(name),
                        "active": False,
                        "is_separator": is_separator,
                        "author": "-",
                        "version": "",
                        "size_mb": "",
                        "direct_link_count": 0,
                        "archive_hash": "",
                        "archive_name": "",
                        "archive": None,
                        "folder_meta": {},
                        "source_archives": [],
                        "folder_files": [],
                        "source_file_counts": {},
                        "compare_status": "external_only",
                        "external_only": True,
                    }
                )

            for index, row in enumerate(interleaved_rows):
                external_render_order[id(row)] = index
            rows = interleaved_rows

        def row_matches_search(row):
            if not search_text:
                return True
            return (
                search_text in (row["name"] or "").lower()
                or search_text in (row["author"] or "").lower()
                or search_text in (row["archive_name"] or "").lower()
            )

        def sort_key(item):
            if self.sort_column == "number":
                if showing_external_only:
                    return external_render_order.get(id(item), 0)
                number = item.get("number", 0)
                if isinstance(number, str) and number.startswith("E"):
                    try:
                        return (1, int(number[1:]))
                    except Exception:
                        return (1, 0)
                try:
                    return (0, int(number))
                except Exception:
                    return (0, 0)
            if self.sort_column == "active":
                state = item.get("activation_state") or ("active" if item.get("active") else "inactive")
                if state == "always":
                    return 2
                if state == "active":
                    return 1
                return 0
            if self.sort_column == "name":
                return str(item.get("name", "")).lower()
            if self.sort_column == "sources":
                return len(item.get("source_archives", []))
            if self.sort_column == "author":
                return str(item.get("author", "")).lower()
            if self.sort_column == "version":
                return str(item.get("version", "")).lower()
            if self.sort_column == "size":
                try:
                    return float(item.get("size_mb") or 0)
                except Exception:
                    return 0.0
            return item.get("number", 0)

        if self.sort_column == "number" or showing_external_only:
            sections = []
            current_header = None
            current_children = []
            current_header_visible = False

            def flush_section():
                nonlocal current_header, current_children, current_header_visible
                if current_header is None and not current_children:
                    return
                if current_header is None:
                    section_header = None
                elif current_header_visible or current_children:
                    section_header = current_header
                else:
                    section_header = None
                sections.append((section_header, list(current_children)))
                current_header = None
                current_children = []
                current_header_visible = False

            for row in rows:
                if row["is_separator"]:
                    flush_section()
                    current_header = row if row_matches_search(row) else row
                    current_header_visible = row_matches_search(row)
                    continue

                if row_matches_search(row):
                    current_children.append(row)

            flush_section()
        else:
            filtered_rows = [row for row in rows if row_matches_search(row)]
            sections = [(None, sorted(filtered_rows, key=sort_key, reverse=self.sort_reverse))]

        self.tree.delete(*self.tree.get_children())
        self.row_by_item_id = {}

        visible_count = 0
        for header, children in sections:
            if header is None:
                parent_id = ""
            else:
                active_mark = self.get_activation_mark_for_row(header)
                values = (active_mark, header["name"], "", header["author"], header["version"], header["size_mb"])
                tags = ["separator"]
                compare_status = header.get("compare_status")
                if compare_status == "green":
                    tags.append("compare_green")
                elif compare_status == "yellow":
                    tags.append("compare_yellow")
                elif compare_status == "red":
                    tags.append("compare_red")
                elif compare_status == "external_only":
                    tags.append("compare_external_only")
                if header["archive_hash"]:
                    tags.append(header["archive_hash"])
                parent_id = self.tree.insert("", "end", text=str(header["number"]), values=values, tags=tuple(tags), open=True)
                self.row_by_item_id[parent_id] = header
                visible_count += 1

            for row in children:
                active_mark = self.get_activation_mark_for_row(row)
                sources_value = "" if row["is_separator"] else str(len(row.get("source_archives", [])))
                values = (active_mark, row["name"], sources_value, row["author"], row["version"], row["size_mb"])
                tags = ["separator"] if row["is_separator"] else []

                compare_status = row.get("compare_status")
                if compare_status == "green":
                    tags.append("compare_green")
                elif compare_status == "yellow":
                    tags.append("compare_yellow")
                elif compare_status == "red":
                    tags.append("compare_red")
                elif compare_status == "external_only":
                    tags.append("compare_external_only")

                if row["archive_hash"]:
                    tags.append(row["archive_hash"])

                item_id = self.tree.insert(parent_id, "end", text=str(row["number"]), values=values, tags=tuple(tags))
                self.row_by_item_id[item_id] = row
                visible_count += 1

        total_count = len(self.mod_rows) + (len(self.external_only_compare_names) if showing_external_only else 0)
        if hasattr(self, "status_var"):
            if self.active_profile:
                profile_name = self.active_profile.split("\\")[-1]
                self.status_var.set(f"Profile '{profile_name}': showing {visible_count} of {total_count} entries")
            else:
                self.status_var.set(f"Showing {visible_count} of {total_count} entries")

        self.update_sort_indicators()

    def sort_by(self, column):
        """Sort list by number or name."""
        if self.is_external_only_sort_locked():
            self.set_status("Sorting is disabled while Show External-Only is enabled.")
            return
        if column == self.sort_column:
            self.sort_reverse = not self.sort_reverse
        else:
            self.sort_column = column
            self.sort_reverse = False
        self.apply_filters_and_sort()

    def filter_mods(self, *args):
        """Filter mods based on search text."""
        self.apply_filters_and_sort()

    def on_sources_search_changed(self, *args):
        """Rebuild sources tab with current filter text."""
        if self.current_selected_row:
            self.update_sources_tab(self.current_selected_row)

    def on_sources_filter_changed(self):
        """Rebuild sources tab when the hide .mohidden option changes."""
        if self.current_selected_row:
            self.update_sources_tab(self.current_selected_row)

    def on_files_search_changed(self, *args):
        """Rebuild files tab with current filter text."""
        if self.current_selected_row:
            self.update_files_tab(self.current_selected_row)

    def on_files_filter_changed(self):
        """Rebuild files tab when the hide .mohidden option changes."""
        if self.current_selected_row:
            self.update_files_tab(self.current_selected_row)

    def on_mod_select(self, event):
        """Handle mod selection and show details."""
        selection = self.tree.selection()
        if not selection:
            return

        row = self.row_by_item_id.get(selection[0])
        if not row:
            return

        self.current_selected_row = row

        self.overview_text.delete(1.0, tk.END)

        if row["is_separator"]:
            overview = (
                f"Separator: {row['name']}\n"
                f"Order: {row['number']}\n"
                "\n"
                "This is a modlist section separator (_separator), not a downloadable archive."
            )
            self.overview_text.insert(1.0, overview)
            self.update_sources_tab(row)
            self.files_tree.delete(*self.files_tree.get_children())
            self.files_tree.insert("", "end", text="No files for separators.")
            self.notebook.select(0)
            return

        if row.get("external_only"):
            overview = (
                f"Mod Name: {row['name']}\n"
                f"Order: {row['number']}\n"
                "\n"
                "This mod exists in the external modlist but not in the loaded Wabbajack modlist."
            )
            self.overview_text.insert(1.0, overview)
            self.update_sources_tab(row)
            self.files_tree.delete(*self.files_tree.get_children())
            self.files_tree.insert("", "end", text="No file details available for external-only entries.")
            self.notebook.select(0)
            return

        archive = row.get("archive")
        folder_meta = row.get("folder_meta", {})
        source_archives = row.get("source_archives", [])

        if not archive and not folder_meta and not source_archives:
            overview = (
                f"Mod Name: {row['name']}\n"
                f"Order: {row['number']}\n"
                f"Active: {self.get_activation_label_for_row(row)}\n\n"
                "No matching archive metadata was found for this modlist entry."
            )
            self.overview_text.insert(1.0, overview)
            self.update_sources_tab(row)
            self.files_tree.delete(*self.files_tree.get_children())
            self.files_tree.insert("", "end", text="No file details available.")
            self.notebook.select(0)
            return

        state = archive.get("State", {}) if archive else {}
        installation_file_meta = folder_meta.get("installation_file", "")
        filename = installation_file_meta or (archive.get("Name", row["raw_name"]) if archive else row["raw_name"])
        mod_author = state.get("Author", "Unknown") if archive else "Unknown"
        mod_description = state.get("Description", "") if archive else ""
        mod_version = folder_meta.get("version") or state.get("Version", "")
        actual_mod_name = state.get("Name", filename) if archive else row["raw_name"]
        total_archive_size = sum(source_archive.get("Size", 0) for source_archive in source_archives)

        overview = f"Mod Folder: {row['raw_name']}\n"
        overview += f"Display Name: {row['name']}\n"
        overview += f"Order: {row['number']}\n"
        overview += f"Active: {self.get_activation_label_for_row(row)}\n"
        overview += f"Primary Source: {actual_mod_name}\n"
        overview += f"Installation File (meta.ini): {filename}\n"
        if installation_file_meta:
            overview += "Note: meta.ini installation file is metadata and is not counted as a source archive.\n"
        overview += f"Author: {mod_author}\n"
        if mod_version:
            overview += f"Version: {mod_version}\n"
        if folder_meta.get("repository"):
            overview += f"Repository: {folder_meta['repository']}\n"
        if folder_meta.get("modid"):
            overview += f"Mod ID: {folder_meta['modid']}\n"

        size_mb = total_archive_size / (1024 * 1024) if total_archive_size > 0 else 0
        overview += f"Source Archive Size: {size_mb:.1f} MB\n\n"

        if mod_description:
            overview += f"Description: {mod_description}\n\n"

        if source_archives:
            direct_links_total = sum(
                1 for source_archive in source_archives if self.build_archive_links(source_archive)["download"]
            )
            source_file_counts = row.get("source_file_counts", {})
            overview += f"Direct Links Available: {direct_links_total}\n"
            overview += f"Sources: {len(source_archives)}\n\n"
            for index, source_archive in enumerate(source_archives, start=1):
                source_state = source_archive.get("State", {})
                links = self.build_archive_links(source_archive)
                source_name = source_state.get("Name") or source_archive.get("Name", "Unknown")
                file_count = source_file_counts.get(source_archive.get("Hash", ""), 0)
                overview += f"Source {index}: {source_name}\n"
                overview += f"Files Contributed: {file_count}\n"
                overview += f"Archive File (directive->Archives.Name): {source_archive.get('Name', '')}\n"
                if source_archive.get("Hash"):
                    overview += f"Archive Hash: {source_archive.get('Hash')}\n"
                if source_state.get("$type"):
                    overview += f"Archive State Type: {source_state.get('$type')}\n"
                if source_state.get("Version"):
                    overview += f"Source Version: {source_state.get('Version')}\n"
                if links["page"]:
                    overview += f"Mod Page: {links['page']}\n"
                if links["download"]:
                    overview += f"Direct Download: {links['download']}\n"
                else:
                    overview += "Direct Download: unavailable\n"
                overview += "\n"

        file_list = row.get("folder_files", [])
        if file_list:
            overview += f"Files to be installed: {len(file_list)} files\n"
            total_size = sum(file_info["size"] for file_info in file_list)
            overview += f"Total file size: {total_size:,} bytes ({total_size / (1024 * 1024):.1f} MB)\n"
        else:
            overview += "No file details available for this mod.\n"

        self.overview_text.insert(1.0, overview)
        self.make_links_clickable()

        self.update_sources_tab(row)
        self.update_files_tab(row)
        self.notebook.select(0)

    def open_link(self, event):
        """Open link in default browser."""
        try:
            index = self.overview_text.index(f"@{event.x},{event.y}")
            line_start = self.overview_text.index(f"{index} linestart")
            line_end = self.overview_text.index(f"{index} lineend")
            line_text = self.overview_text.get(line_start, line_end)

            match = re.search(r"https?://\S+", line_text)
            if match:
                url = match.group(0)
                webbrowser.open(url)
        except Exception as e:
            messagebox.showerror("Error", f"Failed to open link: {e}")

    def organize_files_by_directory(self, file_list, folder_name=None):
        """Organize files into a directory tree structure."""
        tree = {}

        for file_info in file_list:
            target_path = str(file_info["target_path"]).replace("\\", "/")
            if folder_name:
                folder_prefix = f"mods/{folder_name}/"
                if target_path.lower().startswith(folder_prefix.lower()):
                    target_path = target_path[len(folder_prefix):]
            archive_path = file_info["archive_path"]
            size = file_info["size"]
            parts = target_path.split("/")

            current = tree
            for part in parts[:-1]:
                if part not in current:
                    current[part] = {"type": "dir", "children": {}}
                current = current[part]["children"]

            filename = parts[-1]
            current[filename] = {
                "type": "file",
                "size": size,
                "target_path": target_path,
                "archive_path": archive_path,
                "source_mod": file_info.get("source_mod", "Unknown"),
            }

        return tree

    def populate_files_tree(self, file_tree, parent=""):
        """Recursively populate the files treeview."""
        for name, item in file_tree.items():
            if item["type"] == "dir":
                dir_id = self.files_tree.insert(parent, "end", text=f"[DIR] {name}/", values=("", "", ""), tags=("directory",))
                self.populate_files_tree(item["children"], dir_id)
            else:
                size_str = f"{item['size']:,}" if item["size"] > 0 else "0"
                source_mod = item.get("source_mod", "Unknown")
                source_path = item.get("archive_path", "Unknown")
                self.files_tree.insert(
                    parent,
                    "end",
                    text=f"[FILE] {name}",
                    values=(source_mod, source_path, size_str),
                    tags=("file",),
                )

    def update_files_tab(self, row):
        """Update the files tab with organized file tree."""
        self.files_tree.delete(*self.files_tree.get_children())

        file_list = row.get("folder_files", [])
        source_names = {
            source_archive.get("Hash", ""): self.get_source_archive_label(source_archive)
            for source_archive in row.get("source_archives", [])
        }

        search_text = self.files_search_var.get().lower().strip() if hasattr(self, "files_search_var") else ""
        filtered_files = []
        hide_mohidden = bool(
            hasattr(self, "files_hide_mohidden_var") and self.files_hide_mohidden_var.get()
        )
        for file_info in file_list:
            if hide_mohidden and self.is_mohidden_file(file_info):
                continue
            enriched = dict(file_info)
            enriched["source_mod"] = source_names.get(file_info.get("archive_hash", ""), "Unknown")
            if search_text:
                haystack = " ".join(
                    [
                        str(enriched.get("target_path", "")),
                        str(enriched.get("archive_path", "")),
                        str(enriched.get("source_mod", "")),
                    ]
                ).lower()
                if search_text not in haystack:
                    continue
            filtered_files.append(enriched)

        filtered_files = self.sort_files_for_column(filtered_files)

        if filtered_files:
            file_tree = self.organize_files_by_directory(filtered_files, row.get("raw_name"))
            self.populate_files_tree(file_tree)
        else:
            self.files_tree.insert("", "end", text="No file details available for this mod.")

    def make_links_clickable(self):
        """Make URLs in the text widget clickable."""
        content = self.overview_text.get(1.0, tk.END)
        lines = content.split("\n")

        for i, line in enumerate(lines):
            for match in re.finditer(r"https?://\S+", line):
                start = f"{i + 1}.{match.start()}"
                end = f"{i + 1}.{match.end()}"
                self.overview_text.tag_add("link", start, end)

    def update_sources_tab(self, row):
        """Populate sources tab with per-source file installs for the selected mod folder."""
        self.sources_tree.delete(*self.sources_tree.get_children())
        self.row_by_source_item_id = {}

        if row.get("is_separator"):
            self.sources_tree.insert("", "end", text="No sources for separators.")
            return

        source_archives = list(row.get("source_archives", []))
        if not source_archives:
            self.sources_tree.insert("", "end", text="No source archives available.")
            return

        folder_name = row.get("raw_name", "")
        folder_prefix = f"mods\\{folder_name}\\"
        search_text = self.sources_search_var.get().lower().strip() if hasattr(self, "sources_search_var") else ""
        files_by_archive = {}
        hide_mohidden = bool(
            hasattr(self, "sources_hide_mohidden_var") and self.sources_hide_mohidden_var.get()
        )
        for file_info in row.get("folder_files", []):
            if hide_mohidden and self.is_mohidden_file(file_info):
                continue
            archive_hash = file_info.get("archive_hash", "")
            files_by_archive.setdefault(archive_hash, []).append(file_info)

        source_groups = []
        for source_archive in source_archives:
            archive_hash = source_archive.get("Hash", "")
            source_files = files_by_archive.get(archive_hash, [])
            source_groups.append((source_archive, source_files))

        source_groups.sort(
            key=lambda item: self.get_source_sort_value(item[0], item[1], self.sources_sort_column),
            reverse=self.sources_sort_reverse,
        )

        for index, (source_archive, source_files) in enumerate(source_groups, start=1):
            source_state = source_archive.get("State", {})
            links = self.build_archive_links(source_archive)
            source_name = self.get_source_archive_label(source_archive)
            archive_hash = source_archive.get("Hash", "")
            source_files = self.sort_files_for_column(list(source_files))

            if search_text:
                matched_files = []
                for file_info in source_files:
                    haystack = " ".join(
                        [
                            str(file_info.get("target_path", "")),
                            str(file_info.get("archive_path", "")),
                            str(source_name),
                            str(file_info.get("directive_type", "")),
                        ]
                    ).lower()
                    if search_text in haystack:
                        matched_files.append(file_info)
                source_files = matched_files

            if search_text and not source_files:
                continue

            parent_text = f"Source {index}: {source_name} ({len(source_files)} files)"
            item_id = self.sources_tree.insert("", "end", text=parent_text, values=("", "", "", ""))
            self.row_by_source_item_id[item_id] = {"archive": source_archive, "links": links}

            for file_info in source_files:
                target_path = str(file_info.get("target_path", ""))
                if target_path.lower().startswith(folder_prefix.lower()):
                    install_path = target_path[len(folder_prefix):]
                else:
                    install_path = target_path

                self.sources_tree.insert(
                    item_id,
                    "end",
                    text="",
                    values=(
                        install_path,
                        file_info.get("archive_path", ""),
                        f"{file_info.get('size', 0):,}",
                        file_info.get("directive_type", ""),
                    ),
                )

            self.sources_tree.item(item_id, open=True)

    def on_source_activate(self, event):
        """Open the most useful link for the selected source archive."""
        selection = self.sources_tree.selection()
        if not selection:
            return

        source_row = self.row_by_source_item_id.get(selection[0])
        if not source_row:
            return

        links = source_row.get("links", {})
        url = links.get("download") or links.get("page")
        if url:
            webbrowser.open(url)



def main():
    root = tk.Tk()
    app = WabbajackGuideApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
