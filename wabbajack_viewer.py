import argparse
import gzip
import json
from pathlib import Path
import re
import tkinter as tk
import tkinter.font as tkfont
from tkinter import filedialog, messagebox, scrolledtext, ttk
import webbrowser
import zipfile
import queue
import threading

from package_model import PackageModel
from profile_locations import default_downloads_folder
from viewer_snapshots import write_snapshot
from ui_components import ActivityBanner, COLORS, ReadOnlyText, configure_style, style_text
from directive_details import archive_contributions, contribution_summary, describe_directive
from preferred_sources import (presentation_row, signature, verify_preferred_sources,
                               preferred_source_candidates, SourceVerificationCancelled)

from profile_compare import (general_metadata, mod_entries, positive_id, safe_url, source_links, metadata)
from wabbajack_format import directive_type, downloader_type, package_path


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
    SOURCE_DIRECTIVE_TYPES = {"FromArchive", "PatchedFromArchive", "TransformedTexture"}
    INLINE_SOURCE_PREFIX = "INLINEFILE::"

    @staticmethod
    def sanitize_filename(value):
        """Convert text into a filesystem-friendly filename stem."""
        cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value).strip())
        return cleaned.strip("_") or "mod"

    def __init__(self, root):
        self.root = root
        self.root.title("Wabbajack Viewer")
        self.root.geometry("1300x850")
        self.root.minsize(760, 680)
        self.root.resizable(True, True)
        self.style = configure_style(root)
        self.details_split = .7
        self.details_hidden = False
        self.last_package_title = ''
        self._initial_load = False

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

        # UI list state
        self.mod_rows = []
        self.row_by_item_id = {}
        self.row_by_source_item_id = {}
        self.row_by_file_item_id = {}
        self.sort_column = "number"
        self.sort_reverse = False
        self.sources_sort_column = "source_path"
        self.sources_sort_reverse = False
        self.files_sort_column = "target_path"
        self.files_sort_reverse = False
        self.current_selected_row = None
        self.installed_report = None
        self.column_widths = {}
        self._tooltips = []

        self.current_wabbajack_file = None
        self.loaded_from_saved_modlist = False
        self.last_saved_modlist_path = ""
        self.restore_last_session = True
        self.restored_from_snapshot = False
        self._save_thread = None
        self._save_pending = None
        self._snapshot_requests = []
        self._save_events = queue.Queue()
        self._closing = False
        self.downloads_folder = ''
        self._preferred_cache = {}
        self._preferred_pending = set()
        self._preferred_events = queue.Queue()
        self._preferred_tasks = queue.Queue()
        self._preferred_generation = 0
        self._preferred_stop = threading.Event()
        self._preferred_batch_keys = set()
        self.config_path = Path(__file__).with_name("wabbajack_viewer.config.json")

        self.load_persisted_preferences()
        self.restore_session_var = tk.BooleanVar(value=self.restore_last_session)

        self.setup_ui()
        self.root.protocol('WM_DELETE_WINDOW', self.request_close)
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
            self.downloads_folder = str(payload.get('downloads_folder', ''))
            self.last_package_title = str(payload.get('last_package_title', ''))
            self.restore_last_session = bool(payload.get('restore_last_session', True))
            split = payload.get('details_split', .7)
            self.details_split = min(.75, max(.2, split)) if isinstance(split, (int, float)) else .7
            self.details_hidden = bool(payload.get('details_hidden', False))
            if isinstance(payload.get('column_widths'), dict):
                self.column_widths = {key: value for key, value in payload['column_widths'].items() if isinstance(value, dict)}
            candidate = payload.get("last_saved_modlist_path", "")
            if self.is_saved_modlist_path(candidate):
                self.last_saved_modlist_path = str(candidate)
        except Exception:
            self.last_saved_modlist_path = ""

    def persist_preferences(self):
        """Save app preferences to disk."""
        payload = {"last_saved_modlist_path": self.last_saved_modlist_path or "", 'column_widths': self.column_widths,
                   'downloads_folder': getattr(self, 'downloads_folder', ''),
                   'details_split': self.details_split,
                   'details_hidden': self.details_hidden,
                   'last_package_title': (' '.join(str(self.modlist_data.get(key, '')) for key in ('Name', 'Version')).strip()
                                          if getattr(self, 'modlist_data', None) else getattr(self, 'last_package_title', '')),
                   'restore_last_session': self.restore_session_var.get() if hasattr(self, 'restore_session_var') else True}
        try:
            self.config_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    def remember_saved_modlist_path(self, file_path):
        """Remember the selected saved modlist path for next startup."""
        if self.is_saved_modlist_path(file_path):
            self.last_saved_modlist_path = str(file_path)
            self.persist_preferences()

    @property
    def session_snapshot_path(self):
        return self.config_path.with_name('wabbajack_session.wvmodlist.json.gz')

    def autosave_session(self, *, wait=False):
        if not self.mod_rows:
            return
        payload = self.snapshot_payload()
        if wait:
            write_snapshot(self.session_snapshot_path, payload)
            return
        self._save_pending = (self.session_snapshot_path, payload)
        if self._save_thread is None:
            self.start_session_write()

    def start_session_write(self):
        if self._snapshot_requests:
            path, payload = self._snapshot_requests.pop(0)
            self._saving_manual_snapshot = True
        else:
            path, payload = self._save_pending
            self._save_pending = None
            self._saving_manual_snapshot = False
        self._saving_path = path
        if self._closing or self.activity.owner in (None, 'save'):
            title = 'Saving session before closing' if self._closing else 'Saving snapshot' if self._saving_manual_snapshot else 'Saving session'
            self.activity.start('save', title)
            self.set_status('Saving the parsed mod list in the background…')
        def worker():
            try:
                write_snapshot(path, payload)
                self._save_events.put(None)
            except Exception as error:
                self._save_events.put(str(error))
        self._save_thread = threading.Thread(target=worker, daemon=True)
        self._save_thread.start()
        self._save_poll_id = self.root.after(100, self.poll_session_write)

    def poll_session_write(self):
        self._save_poll_id = None
        if self._save_events.empty():
            self._save_poll_id = self.root.after(100, self.poll_session_write)
            return
        error = self._save_events.get_nowait()
        self._save_thread = None
        self.activity.finish('save')
        if self._saving_manual_snapshot:
            if error:
                messagebox.showerror('Snapshot not saved', f'Failed to save snapshot: {error}', parent=self.root)
            else:
                self.remember_saved_modlist_path(self._saving_path)
        if not getattr(self, 'loading_package', False) and self.activity.owner is None:
            self.set_status(f'Could not save snapshot: {error}' if error else
                            f'Snapshot saved: {Path(self._saving_path).name}' if self._saving_manual_snapshot else 'Last session saved')
        if self._snapshot_requests or self._save_pending:
            self.start_session_write()
        elif self._closing:
            self.root.destroy()

    def load_saved_modlist_from_path(self, file_path, *, quiet=False, remember=True):
        """Load a previously saved modlist snapshot from an explicit path."""
        if not file_path:
            return False

        path = Path(file_path)
        if not path.exists():
            return False

        previous = self.__dict__.copy()
        try:
            self.set_status("Restoring mod list snapshot...")
            with gzip.open(path, "rb") as handle:
                payload = json.load(handle)
            self._preferred_generation += 1
            self._preferred_pending = set()
            self._preferred_batch_keys = set()

            self.modlist_data = payload.get("modlist_data") or {}
            self.loaded_snapshot_path = str(path)
            self.restored_from_snapshot = True
            self.__dict__.pop("package_model", None)
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
            self.profile_modlist_text = ""
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
            self._preferred_cache = {}
            for row, serialized in zip(hydrated_rows, rows):
                saved = serialized.get('verified_sources')
                if not saved:
                    continue
                alternatives = {}
                for target, alternate in saved.get('alternatives', {}).items():
                    archive = self.archive_by_hash.get(alternate['archive_hash'])
                    if archive:
                        alternatives[target] = {'archive': archive, 'member': alternate['member']}
                self._preferred_cache[self.preferred_key(row)] = dict(saved, alternatives=alternatives,
                    signature=tuple(saved['signature']) if saved.get('signature') is not None else None,
                    _files=row['folder_files'])
            self.snapshot_model_cache = payload.get('model_cache')
            self.snapshot_restore_reason = ('The original package is missing or has changed.' if self.snapshot_model_cache
                                            else 'This older snapshot contains browsing data only.')
            if self.snapshot_model_cache and self.current_wabbajack_file:
                try:
                    self.package_model = PackageModel(self.current_wabbajack_file, self.set_status,
                        cached=dict(self.snapshot_model_cache, manifest=self.modlist_data))
                except (OSError, ValueError, KeyError):
                    self.__dict__.pop('package_model', None)
            self.loaded_from_saved_modlist = not hasattr(self, 'package_model')
            if hasattr(self, 'package_model'):
                self.inline_file_sources = {key.replace('/', '\\'): source for key, source in self.package_model.inline_sources.items()}
                self.inline_text_cache = {key.replace('/', '\\'): text for key, text in self.package_model.text_cache.items()}
                self.directives = self.modlist_data['Directives']
                self.mod_folder_files = {}
                self.process_directives()
                for row in hydrated_rows:
                    self.mod_folder_files[row.get('folder_key') or row['raw_name'].casefold()] = row['folder_files']
                self.mod_folder_meta_cache = {row.get('folder_key') or row['raw_name'].casefold(): row.get('folder_meta', {}) for row in hydrated_rows}
                self.mod_folder_meta_paths = self.build_mod_folder_meta_paths()
                self.profile_modlist_text = payload.get('profile_modlist_text', '')
            self.update_ui_after_load(prepare_rows=False, verify_sources=payload.get('snapshot_version', 1) < 2)
            self.restore_snapshot_ui(payload.get('ui_state', {}))
            if remember:
                self.remember_saved_modlist_path(str(path))
                self.autosave_session()
            self.set_status(f"Restored snapshot: {path.name}" + (' · browsing only' if self.loaded_from_saved_modlist else ''))
            return True
        except Exception as e:
            self.__dict__.update(previous)
            self.load_error = str(e)
            self.update_ui_after_load(prepare_rows=False, verify_sources=False)
            if quiet:
                self.set_status(f'Could not restore the last session: {e}')
            else:
                messagebox.showerror("Error", f"Failed to open snapshot: {e}")
            return False

    def auto_load_last_saved_modlist(self):
        """Load the last saved snapshot automatically when available."""
        if not self.restore_session_var.get():
            return
        paths = [self.session_snapshot_path] if self.session_snapshot_path.exists() else []
        if self.is_saved_modlist_path(self.last_saved_modlist_path) and Path(self.last_saved_modlist_path).exists():
            paths.append(Path(self.last_saved_modlist_path))
        if paths:
            self.start_snapshot_load(paths[0], fallback=paths[1:], automatic=True)

    def load_wabbajack_file(self):
        """Open file dialog to select and load Wabbajack file."""
        if self._closing or getattr(self, 'loading_package', False):
            return
        if getattr(self, 'workbench_view', None) and self.workbench_view.busy:
            return
        file_path = filedialog.askopenfilename(
            title="Select Wabbajack File",
            initialdir=default_downloads_folder(),
            filetypes=[("Wabbajack files", "*.wabbajack"), ("All files", "*.*")],
        )

        if file_path:
            self.start_package_load(file_path)

    def start_package_load(self, path):
        self.start_document_load(path)

    def start_snapshot_load(self, path, *, fallback=(), automatic=False):
        self.start_document_load(path, snapshot=True, fallback=fallback, automatic=automatic)

    def start_document_load(self, path, *, snapshot=False, fallback=(), automatic=False, profile=None):
        if self._closing or getattr(self, 'loading_package', False):
            return
        if self.workbench_view and self.workbench_view.busy:
            return
        self.loading_package = True
        initial_load = not self.modlist_data
        self._initial_load = initial_load
        if initial_load:
            self.header_frame.grid_remove()
            self.workspace.grid_remove()
            self.status_frame.grid_remove()
        self._preferred_generation += 1
        self._preferred_pending.clear()
        self.comparison_view.set_blocked(True)
        self.load_cancel = threading.Event()
        events = queue.Queue()
        restore_title = f'Restoring {self.last_package_title}…' if self.last_package_title else 'Restoring last session…'
        self.activity.start('load', f'Switching to profile: {profile}' if profile else restore_title if automatic else 'Opening snapshot' if snapshot else 'Opening Wabbajack', self.load_cancel.set)
        self.set_status('Restoring cached mod list…' if snapshot else 'Reading and verifying package files…')
        previous_states = []
        menu_states = []
        for menu in (self.saved_views_menu, self.export_menu):
            for index in range(menu.index('end') + 1):
                menu_states.append((menu, index, menu.entrycget(index, 'state')))
                menu.entryconfigure(index, state=tk.DISABLED)
        for widget in [widget for widget, _ in self.header_toolbar_widgets] + [self.source_downloads_button]:
            if isinstance(widget, ttk.Label):
                continue
            try:
                previous_states.append((widget, widget.cget('state')))
                widget.configure(state=tk.DISABLED)
            except tk.TclError:
                pass
        self.cancel_load_button.config(state=tk.NORMAL)
        self.layout_header_buttons()
        stage = WabbajackGuideApp.__new__(WabbajackGuideApp)
        stage.__dict__ = self.__dict__.copy()
        stage._preferred_cache = dict(self._preferred_cache) if profile else {}
        stage.mod_folder_meta_cache = dict(self.mod_folder_meta_cache)
        stage.inline_text_cache = dict(self.inline_text_cache)
        stage.synthetic_archive_by_hash = dict(self.synthetic_archive_by_hash)
        stage.update_ui_after_load = lambda **kwargs: None
        stage.restore_snapshot_ui = lambda state: setattr(stage, '_restored_ui_state', state)
        def progress(message):
            if self.load_cancel.is_set():
                raise SourceVerificationCancelled('Opening cancelled.')
            if events.qsize() < 4:
                events.put(('progress', message))
        stage.set_status = progress
        def worker():
            if profile:
                stage.load_profile_inline_data(preferred_profile=profile)
                success = True
            elif snapshot:
                success = False
                for candidate in (path, *fallback):
                    if stage.load_saved_modlist_from_path(candidate, quiet=True, remember=False):
                        success = True
                        break
            else:
                success = stage.extract_and_load_modlist(path, notify=False)
            if success and not snapshot:
                for key, item in stage.package_model.destinations.items():
                    if key.startswith('mods/') and key.endswith('/meta.ini') and key in stage.package_model.inline_sources:
                        progress('Reading mod metadata…')
                        stage.mod_folder_meta_cache[key.split('/')[1]] = stage.parse_meta_ini(stage.package_model.read_text(item))
                stage.prepare_mod_rows(render=False)
                stage.verify_sources_before_display()
            events.put(('done', success))
        def guarded_worker():
            try:
                worker()
            except Exception as error:
                stage.load_error = str(error)
                events.put(('done', False))
        threading.Thread(target=guarded_worker, daemon=True).start()
        def poll():
            self._load_poll_id = None
            done = False
            while not events.empty():
                kind, value = events.get_nowait()
                if kind == 'progress':
                    self.set_status(value)
                else:
                    done = True
                    self.loading_package = False
                    self.comparison_view.set_blocked(False)
                    for widget, state in previous_states:
                        widget.configure(state=state)
                    for menu, index, state in menu_states:
                        menu.entryconfigure(index, state=state)
                    self.cancel_load_button.config(state=tk.DISABLED)
                    self.activity.finish('load')
                    self._initial_load = False
                    if initial_load:
                        self.header_frame.grid()
                        self.workspace.grid()
                        self.status_frame.grid()
                    self.layout_header_buttons()
                    if value and not self.load_cancel.is_set():
                        fields = ('modlist_data archives directives archive_lookup archive_by_hash archive_name_index mod_folder_index archive_normalized_index mod_folder_meta_cache mod_folder_meta_paths mod_folder_files mod_folder_archive_hashes synthetic_archive_by_hash inline_file_sources inline_text_cache active_profile all_profile_paths profile_modlist_text current_wabbajack_file loaded_from_saved_modlist package_model mod_rows _preferred_cache _preferred_batch_keys restored_from_snapshot loaded_snapshot_path snapshot_model_cache snapshot_restore_reason').split()
                        for field in fields:
                            if hasattr(stage, field):
                                setattr(self, field, getattr(stage, field))
                            else:
                                self.__dict__.pop(field, None)
                        self.update_ui_after_load(prepare_rows=False, verify_sources=False)
                        if snapshot:
                            self.restore_snapshot_ui(getattr(stage, '_restored_ui_state', {}))
                            self.set_status('Last session restored' if automatic else 'Snapshot opened')
                            if not automatic:
                                self.remember_saved_modlist_path(str(path))
                        if not automatic:
                            self.autosave_session()
                    else:
                        self.refresh_profile_selector()
                        self.set_status(getattr(stage, 'load_error', 'Opening cancelled.'))
            if not done:
                self._load_poll_id = self.root.after(100, poll)
        self._load_poll_id = self.root.after(100, poll)

    def on_root_destroy(self, event):
        if event.widget is not self.root:
            return
        if hasattr(self, 'load_cancel'):
            self.load_cancel.set()
        self._preferred_stop.set()
        self.activity.finish()
        if self.comparison_view:
            self.comparison_view.cancelled.set()
        timers = [getattr(self, '_load_poll_id', None),
                  getattr(self, '_preferred_poll_id', None),
                  getattr(self, '_save_poll_id', None),
                  getattr(self.comparison_view, 'poll_id', None),
                  getattr(getattr(self, 'workbench_view', None), 'poll_id', None)]
        for timer in timers:
            if timer:
                self.root.tk.call('after', 'cancel', timer)

    def set_status(self, message):
        """Update status text and flush pending UI updates."""
        if hasattr(self, "status_var"):
            self.status_var.set(message)
            if hasattr(self, 'activity') and self.activity.owner:
                self.activity.phase.set(message)
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
        if "/" in folder_name or "\\" in folder_name or folder_name in ("", ".", ".."):
            raise ValueError("Invalid mod folder name for installer export.")
        package_path(f"mods/{folder_name}/placeholder")
        folder_prefix = f"mods\\{folder_name}\\"
        archive_names = {
            archive.get("Hash", ""): archive.get("Name", "")
            for archive in row.get("source_archives", [])
            if not str(archive.get("State", {}).get("$type", "")).startswith("InlineFile")
        }

        archive_groups = {}
        embedded_files = []
        for file_info in row.get("folder_files", []):
            directive_type = file_info.get("directive_type", "")
            if directive_type not in {"FromArchive", "InlineFile"}:
                raise ValueError(
                    f"Cannot replay {directive_type or 'unknown'} directives in a simple extraction script. "
                    "Use Wabbajack for mods requiring patches, generated archives or path substitutions."
                )
            if file_info.get("archive_depth", 2) > 2:
                raise ValueError("Nested archive extraction requires Wabbajack; this mod cannot be exported as a simple installer.")
            if directive_type == "FromArchive" and "archive_depth" not in file_info:
                raise ValueError("This older snapshot lacks archive nesting information. Reload the original .wabbajack before exporting installers.")
            archive_hash = file_info.get("archive_hash", "")
            target_path = str(file_info.get("target_path", ""))
            package_path(target_path)
            target_relative = target_path[len(folder_prefix):] if target_path.lower().startswith(folder_prefix.lower()) else target_path
            if not target_path.lower().startswith(folder_prefix.lower()):
                raise ValueError("Installer target is outside the selected mod folder.")

            if str(archive_hash).startswith(self.INLINE_SOURCE_PREFIX):
                source_data_id = str(file_info.get("source_data_id", "")).strip()
                if not source_data_id:
                    archive_path = str(file_info.get("archive_path", ""))
                    if archive_path.startswith("[Embedded InlineFile] "):
                        source_data_id = archive_path[len("[Embedded InlineFile] "):].strip()
                if source_data_id:
                    if not re.fullmatch(r"[A-Za-z0-9._-]+", source_data_id) or source_data_id in (".", ".."):
                        raise ValueError("Invalid embedded payload name for installer export.")
                    embedded_files.append(
                        {
                            "source_data_id": source_data_id,
                            "target_relative": target_relative,
                        }
                    )
                continue

            archive_filename = archive_names.get(archive_hash, "")
            if not archive_filename:
                raise ValueError("Source archive metadata is missing; refusing an incomplete installer.")

            package_path(file_info.get("archive_path", ""))
            package_path(archive_filename)

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
        values = [folder_name, wabbajack_name] + list(archive_groups)
        values += [value for items in archive_groups.values() for item in items for value in item.values()]
        values += [value for item in embedded_files for value in item.values()]
        if any(any(character in value for character in '!"%\r\n&|<>^') for value in values):
            raise ValueError("These paths contain characters unsupported by the batch exporter. Use Wabbajack instead.")
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
            'goto :install_start',
            '',
            ':resolve_archive',
            'set "FOUND_ARCHIVE_PATH="',
            'for %%D in ("%ARCHIVE_SEARCH_DIRS:;=" "%") do (',
            '  if not defined FOUND_ARCHIVE_PATH if exist "%%~fD\\%ARCHIVE_FILE%" set "FOUND_ARCHIVE_PATH=%%~fD\\%ARCHIVE_FILE%"',
            ')',
            'exit /b',
            '',
            ':install_start',
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
        for items in archive_groups.values():
            for item in items:
                item["archive_path"] = item["archive_path"].replace("\\", "/")
                item["target_relative"] = item["target_relative"].replace("\\", "/")
        for item in embedded_files:
            item["target_relative"] = item["target_relative"].replace("\\", "/")
        wabbajack_name = Path(self.current_wabbajack_file).name if self.current_wabbajack_file else ""
        lines = [
            '#!/usr/bin/env bash',
            'set +e',
            '',
            'SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"',
            f'OUTPUT_DIR="$SCRIPT_DIR"/{self.quote_bash(folder_name)}',
            'TEMP_ROOT="$(mktemp -d "$SCRIPT_DIR/.extract_temp.XXXXXX")" || exit 1',
            'trap \'rm -rf -- "$TEMP_ROOT"\' EXIT',
            'LOG_PATH="$SCRIPT_DIR/install_errors.log"',
            f'WABBAJACK_PATH="$SCRIPT_DIR"/{self.quote_bash(wabbajack_name)}' if wabbajack_name else 'WABBAJACK_PATH=""',
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
                "#0": "WJ #" if self.installed_report else '#',
                "active": "WJ active" if self.installed_report else 'Active',
                "name": "Wabbajack mod" if self.installed_report else 'Mod name',
                "sources": "Archives",
                "author": "Author",
                "version": "WJ version" if self.installed_report else 'Version',
                "size": "Downloads (MiB)",
                'result': 'Result', 'local_name': 'Installed mod', 'local_version': 'Installed version',
                'local_order': 'Installed #', 'local_active': 'Installed active',
            }
            for column, label in mod_headings.items():
                is_active = (column == "#0" and self.sort_column == "number") or (column != "#0" and self.sort_column == column)
                reverse = self.sort_reverse if is_active else False
                self.tree.heading(column, text=self.format_sort_heading(label, is_active, reverse))

        if hasattr(self, "sources_tree"):
            source_headings = {
                "#0": ("Source (recorded title)", "source"),
                "archive_filename": ("Archive filename", "archive_filename"),
                "target_path": ("Install path", "target_path"),
                "source_path": ("Source path", "source_path"),
                "size": ("Size (bytes)", "size"),
                "kind": ("Directive type", "kind"),
            }
            for column, (label, sort_key) in source_headings.items():
                is_active = self.sources_sort_column == sort_key
                reverse = self.sources_sort_reverse if is_active else False
                self.sources_tree.heading(column, text=self.format_sort_heading(label, is_active, reverse))

        if hasattr(self, "files_tree"):
            file_headings = {
                "#0": ("Output path", "target_path"),
                "source_mod": ("Source archive", "source_mod"),
                "source_path": ("Source path", "source_path"),
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
        for url in (row.get('comparison') or {}).get('links', []):
            if url not in seen:
                seen.add(url)
                urls.append(({'Name': 'Source page'}, url))
        for source_archive in row.get("source_archives", []):
            page = self.build_archive_links(source_archive).get("page")
            if page and page not in seen:
                seen.add(page)
                urls.append((source_archive, page))

        return urls

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
        if column == 'archive_filename':
            return str(source_archive.get('Name', '')).casefold()
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
            with Path(output_path).open("w", encoding="utf-8", newline="") as output:
                output.write(script_text)
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
                try:
                    self.build_grouped_install_data(row)
                except ValueError:
                    menu.add_command(label='Extraction scripts unavailable (requires Wabbajack)', state=tk.DISABLED)
                else:
                    scripts = tk.Menu(menu, tearoff=False)
                    scripts.add_command(label='Windows batch…', command=lambda: self.export_install_script_for_selected('bat'))
                    scripts.add_command(label='Bash…', command=lambda: self.export_install_script_for_selected('sh'))
                    menu.add_cascade(label='Export extraction script', menu=scripts)

        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def build_modlist_txt(self):
        """Export authored MO2 order, unaffected by browsing sort or search."""
        lines = []
        # MO2 stores mod order in reverse of display order.
        for row in reversed(self.mod_rows):
            prefix = self.get_activation_prefix_from_state(row.get("activation_state") or ("active" if row.get("active") else "inactive"))
            raw_name = row.get("raw_name") or row.get("name") or "mod"
            lines.append(f"{prefix}{raw_name}")
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
        """Use the same Yes/No convention on both sides of the comparison."""
        if row.get("is_separator"):
            return ""
        state = row.get("activation_state") or ("active" if row.get("active") else "inactive")
        return 'No' if state == 'inactive' else 'Yes'

    @staticmethod
    def get_activation_label_for_row(row):
        """Return a human-readable activation label for the row."""
        state = row.get("activation_state") or ("active" if row.get("active") else "inactive")
        if state == "always":
            return "Always active"
        if state == "inactive":
            return "No"
        return "Yes"

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
            if self.installed_report:
                columns = self.tree['columns']
                values = self.tree.item(item_id, 'values')
                lines.append('\t'.join([self.tree.item(item_id, 'text'),
                    *(str(values[columns.index(column)]) for column in self.tree['displaycolumns'])]))
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
        """Export the authored MO2 modlist.txt load-order file."""
        if not self.mod_rows:
            messagebox.showwarning("No Data", "Load a Wabbajack file first.")
            return
        output_path = filedialog.asksaveasfilename(
            title="Export modlist.txt",
            defaultextension=".txt",
            initialfile="modlist.txt",
            filetypes=[("MO2 modlist", "modlist.txt"), ("Text files", "*.txt"), ("All files", "*.*")],
        )
        if not output_path:
            return
        try:
            output = Path(output_path)
            output.parent.mkdir(parents=True, exist_ok=True)
            modlist_text = self.build_modlist_txt()
            with output.open("w", encoding="utf-8", newline="") as handle:
                handle.write(modlist_text)
            line_count = self.count_nonempty_lines(modlist_text)
            self.set_status(f"Exported modlist.txt: {output.name} ({line_count} lines)")
            messagebox.showinfo(
                "modlist.txt Exported",
                f"Saved authored modlist.txt to:\n{output}\n\n"
                f"Line count: {line_count}\n"
                "Order: MO2 file order (reverse of viewer top-to-bottom)."
            )
        except Exception as e:
            messagebox.showerror("Error", f"Failed to export modlist.txt: {e}")

    def get_separator_folder_names(self):
        """Return authored separator folder names in profile order."""
        seen = set()
        names = []
        for row in self.mod_rows:
            if not row.get("is_separator"):
                continue
            raw_name = str(row.get("raw_name") or "").strip()
            if not raw_name or raw_name in seen:
                continue
            seen.add(raw_name)
            names.append(raw_name)
        return names

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

    def save_modlist(self, *, wait=False):
        """Save loaded data to a compact compressed modlist snapshot for fast reloads."""
        if not self.mod_rows:
            messagebox.showwarning("No Data", "Load a Wabbajack file first.")
            return

        default_name = "wabbajack_modlist.wvmodlist.json.gz"
        output_path = filedialog.asksaveasfilename(
            title="Save mod list snapshot",
            defaultextension=".wvmodlist.json.gz",
            initialfile=default_name,
            filetypes=[
                ("Mod list snapshot", "*.wvmodlist.json.gz"),
                ("Legacy Viewer Cache", "*.wvjcache.json.gz"),
                ("All files", "*.*"),
            ],
        )
        if not output_path:
            return

        if not wait:
            self._snapshot_requests.append((output_path, self.snapshot_payload()))
            if self._save_thread is None:
                self.start_session_write()
            return

        try:
            self.set_status('Saving mod list snapshot…')
            write_snapshot(output_path, self.snapshot_payload())
            self.remember_saved_modlist_path(output_path)
            self.set_status(f'Snapshot saved: {Path(output_path).name}')
            messagebox.showinfo('Snapshot saved', f'Saved snapshot to:\n{output_path}')
        except Exception as error:
            messagebox.showerror('Error', f'Failed to save snapshot: {error}')

    def snapshot_payload(self):
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
            checked = self._preferred_cache.get(self.preferred_key(row))
            if checked:
                serialized_row['verified_sources'] = {
                    'path': checked['path'], 'signature': checked['signature'], 'message': checked['message'],
                    'alternatives': {target: {'archive_hash': alternate['archive']['Hash'], 'member': alternate['member']}
                                     for target, alternate in checked['alternatives'].items()}}
            cache_rows.append(serialized_row)

        payload = {
            "snapshot_version": 2,
            "source_wabbajack": self.current_wabbajack_file,
            "modlist_data": self.modlist_data,
            "archives": self.archives,
            "active_profile": self.active_profile,
            "all_profile_paths": self.all_profile_paths,
            "mod_rows": cache_rows,
            'profile_modlist_text': self.profile_modlist_text,
            'ui_state': self.snapshot_ui_state(),
        }
        model = getattr(self, 'package_model', None)
        if model:
            payload['model_cache'] = {'source_signature': model.source_signature, 'members': dict(model.members),
                                      'text_cache': dict(model.text_cache)}
        elif getattr(self, 'snapshot_model_cache', None):
            payload['model_cache'] = self.snapshot_model_cache
        return payload

    def snapshot_ui_state(self):
        return {'search': self.search_var.get(), 'sort_column': self.sort_column, 'sort_reverse': self.sort_reverse,
                'selected_mod': (self.current_selected_row or {}).get('raw_name'),
                'details_tab': self.notebook.tab(self.notebook.select(), 'text') if self.notebook.select() else 'Overview',
                'sources_search': self.sources_search_var.get(), 'files_search': self.files_search_var.get(),
                'sources_hide_mohidden': self.sources_hide_mohidden_var.get(),
                'files_hide_mohidden': self.files_hide_mohidden_var.get(),
                'comparison_settings_visible': self.comparison_view.settings_visible}

    def restore_snapshot_ui(self, state):
        if 'comparison_settings_visible' in state:
            self.comparison_view.set_settings_visible(bool(state['comparison_settings_visible']))
        self.sort_column = state.get('sort_column', 'number')
        self.sort_reverse = bool(state.get('sort_reverse', False))
        self.search_var.set(state.get('search', ''))
        self.sources_search_var.set(state.get('sources_search', ''))
        self.files_search_var.set(state.get('files_search', ''))
        self.sources_hide_mohidden_var.set(bool(state.get('sources_hide_mohidden', False)))
        self.files_hide_mohidden_var.set(bool(state.get('files_hide_mohidden', False)))
        self.apply_filters_and_sort()
        selected = next((item for item, row in self.row_by_item_id.items() if row['raw_name'] == state.get('selected_mod')), None)
        if selected:
            self.tree.selection_set(selected)
            self.tree.see(selected)
            self.on_mod_select(None)
        for tab in self.notebook.tabs():
            if self.notebook.tab(tab, 'text') == state.get('details_tab', 'Overview'):
                self.notebook.select(tab)
                break

    def load_saved_modlist(self):
        """Load a previously saved modlist snapshot without re-reading the full Wabbajack archive."""
        file_path = filedialog.askopenfilename(
            title="Open mod list snapshot",
            filetypes=[
                ("Mod list snapshot", "*.wvmodlist.json.gz"),
                ("Legacy Viewer Cache", "*.wvjcache.json.gz"),
                ("All files", "*.*"),
            ],
        )
        if not file_path:
            return
        self.start_snapshot_load(file_path)

    def extract_and_load_modlist(self, wabbajack_path, notify=True):
        """Load modlist and inline profile files directly from the Wabbajack archive."""
        previous = self.__dict__.copy()
        try:
            self.current_wabbajack_file = str(wabbajack_path)
            self.restored_from_snapshot = False
            self.loaded_from_saved_modlist = False
            self.set_status("Opening Wabbajack archive...")
            with zipfile.ZipFile(wabbajack_path, "r") as zip_ref:
                self.set_status("Reading modlist metadata...")
                self.package_model = PackageModel(wabbajack_path, self.set_status)
                self.modlist_data = self.package_model.manifest

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

                if notify:
                    messagebox.showinfo(
                    "Success",
                    f"Successfully loaded modlist from:\n{Path(wabbajack_path).name}",
                    )
                return True
        except Exception as e:
            self.__dict__.update(previous)
            self.update_ui_after_load(prepare_rows=False)
            self.load_error = str(e)
            if notify:
                messagebox.showerror("Error", f"Failed to load Wabbajack file: {e}")
            return False

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
        general = general_metadata(meta_text)
        return {
            "installation_file": general.get("installationfile", "").strip(),
            "repository": general.get("repository", "").strip(),
            "modid": general.get("modid", "").strip(),
            "version": general.get("version", "").strip(),
            "url": general.get("url", "").strip(),
            "comments": general.get("comments", "").strip(),
            "notes": general.get("notes", "").strip(),
            "game_name": general.get("gamename", "").strip(),
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
            if hasattr(self, "package_model"):
                text = self.package_model.read_text(normalized)
            elif zip_ref is None:
                with zipfile.ZipFile(self.current_wabbajack_file, "r") as local_zip_ref:
                    text = local_zip_ref.read(source_data_id).decode("utf-8-sig", errors="replace")
            else:
                text = zip_ref.read(source_data_id).decode("utf-8-sig", errors="replace")
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
            for source_hash in set(file_info.get("generated_source_hashes", []) + ([archive_hash] if archive_hash else [])):
                counts[source_hash] = counts.get(source_hash, 0) + 1
        return counts

    @staticmethod
    def build_archive_links(archive):
        """Build mod page and direct download links for an archive when possible."""
        if not archive:
            return {"page": "", "download": ""}

        state = archive.get("State", {})
        mod_type = downloader_type(state.get("$type"))
        page_link = ""
        direct_link = ""

        if mod_type == "Nexus":
            values = metadata(archive.get("Meta", ""), state.get("GameName", ""))
            values["mod_id"] = positive_id(state.get("ModID") or values["mod_id"])
            values["file_id"] = positive_id(state.get("FileID") or values["file_id"])
            links = source_links(values)
            page_link = links[0] if links else ""
            direct_link = next((link for link in links if "file_id=" in link), "")
        else:
            values = general_metadata(archive.get("Meta", ""))
            direct_link = safe_url(state.get("Url") or values.get("directurl"))

        return {"page": page_link, "download": direct_link}

    def load_inline_file_sources(self, zip_ref):
        """Load InlineFile target path -> SourceDataID mapping without decoding payloads."""
        if hasattr(self, 'package_model'):
            return {key.replace('/', '\\'): value for key, value in self.package_model.inline_sources.items()}
        inline_sources_by_to = {}
        archive_entries = set(zip_ref.namelist())
        total = len(self.directives)

        for index, directive in enumerate(self.directives, start=1):
            if index % 2500 == 0:
                self.set_status(f"Indexing InlineFiles: {index}/{total}")

            if directive_type(directive.get("$type")) not in {"InlineFile", "RemappedInlineFile", "PropertyFile"}:
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
        """Pick and load the authored profile modlist."""
        self.active_profile = None
        self.profile_modlist_text = ""

        modlist_paths = sorted(
            path
            for path in self.inline_file_sources.keys()
            if len(path.split("\\")) == 3 and path.startswith("profiles\\") and path.endswith("\\modlist.txt")
        )
        self.all_profile_paths = modlist_paths

        if not modlist_paths:
            return

        if preferred_profile:
            match = next(
                (p for p in modlist_paths if p.rsplit("\\", 1)[0].split("\\")[-1].casefold() == preferred_profile.casefold()),
                None,
            )
            selected_modlist_path = match if match else modlist_paths[0]
        else:
            selected_modlist_path = modlist_paths[0]

        self.profile_modlist_text = self.read_inline_text(selected_modlist_path, zip_ref=zip_ref) or ""
        self.active_profile = selected_modlist_path.rsplit("\\", 1)[0]

    def on_profile_selected(self, event=None):
        """Switch to the selected profile."""
        if self.loaded_from_saved_modlist:
            messagebox.showinfo("Saved profile", "Open the original .wabbajack file to select another authored profile.")
            return
        selected = self.profile_var.get()
        if selected.casefold() == self.selected_profile_name().casefold():
            self.refresh_profile_selector()
            return
        self.comparison_view.set_package_context(self.package_model, self.selected_profile_name())
        self.start_document_load(self.current_wabbajack_file, profile=selected)

    def update_ui_after_load(self, prepare_rows=True, verify_sources=True):
        """Update UI elements after loading modlist data."""
        self.clear_mod_details()
        if self.modlist_data:
            self.root.title(
                f"Wabbajack Viewer - {self.modlist_data.get('Name', 'Unknown Modlist')}"
            )

        self.refresh_profile_selector()
        if prepare_rows:
            self.prepare_mod_rows(render=False)
        if verify_sources:
            self.verify_sources_before_display()
        self.apply_filters_and_sort()
        first = next((item for item, row in self.row_by_item_id.items() if not row.get('is_separator')), None)
        if first:
            self.tree.selection_set(first)
            self.tree.see(first)
            self.on_mod_select(None)

        self.sync_workflow()
        if hasattr(self, "status_var"):
            self.status_var.set("Wabbajack loaded" if not self.loaded_from_saved_modlist else "Snapshot loaded")
        self.update_preferred_source_progress()

    def process_directives(self):
        """Process archive-backed and embedded directives for folder-level source relationships."""
        total = len(self.directives)
        for index, directive in enumerate(self.directives, start=1):
            kind = directive_type(directive.get("$type"))
            if not str(directive.get("To", "")).replace("/", "\\").lower().startswith("mods\\"):
                continue

            if index % 2500 == 0:
                self.set_status(f"Processing directives: {index}/{total}")

            archive_hash = ""
            archive_path = ""
            source_data_id = ""
            if kind in self.SOURCE_DIRECTIVE_TYPES:
                archive_hash = (
                    directive.get("ArchiveHashPath", [""])[0]
                    if directive.get("ArchiveHashPath")
                    else ""
                )
                archive_path = " → ".join(str(part) for part in (directive.get("ArchiveHashPath") or [])[1:])
            elif kind in {"InlineFile", "RemappedInlineFile", "PropertyFile"}:
                source_data_id = str(directive.get("SourceDataID", "")).strip()
                if not source_data_id:
                    continue
                archive_path = f"[Embedded InlineFile] {source_data_id}"

            target_path = str(directive.get("To", "")).replace("/", "\\")
            package_path(target_path)
            if target_path.lower().startswith("mods\\"):
                if self.is_mod_meta_file_path(target_path):
                    continue
                parts = target_path.split("\\")
                if len(parts) >= 2:
                    folder_name = parts[1].strip().lower()
                    if folder_name:
                        if kind in {"InlineFile", "RemappedInlineFile", "PropertyFile"}:
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
                                "directive_type": kind,
                                "hash": directive.get("Hash"),
                                "archive_depth": len(directive.get("ArchiveHashPath") or []),
                                "archive_hash_path": list(directive.get("ArchiveHashPath") or []),
                                "generated_source_hashes": self.package_model.source_hashes(directive) if hasattr(self, "package_model") else [],
                            }
                        )
                        folder_hashes = self.mod_folder_archive_hashes.setdefault(folder_name, [])
                        if archive_hash and archive_hash not in folder_hashes:
                            folder_hashes.append(archive_hash)
                        if hasattr(self, "package_model"):
                            for source_hash in self.package_model.source_hashes(directive):
                                if source_hash not in folder_hashes:
                                    folder_hashes.append(source_hash)

    def parse_modlist_entries(self, modlist_text):
        """Parse modlist.txt entries and convert reverse order to display order."""
        entries = []
        for parsed in mod_entries(modlist_text, strict=False):
            prefix, raw_name = parsed["prefix"], parsed["name"]
            is_separator = raw_name.lower().endswith("_separator")
            display_name = raw_name[:-10] if is_separator else raw_name

            entries.append(
                {
                    "raw_line": prefix + raw_name,
                    "name": display_name,
                    "raw_name": raw_name,
                    "active": prefix in "+*",
                    "activation_state": self.get_activation_state_from_prefix(prefix),
                    "is_separator": is_separator,
                }
            )

        return entries

    def prepare_mod_rows(self, render=True):
        """Build table rows from profile modlist order, with archive metadata if available."""
        self.mod_rows = []

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
                    }
                )

        if render:
            self.apply_filters_and_sort()

    def setup_ui(self):
        """Setup the main UI components."""
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.grid(row=1, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))

        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(1, weight=1)
        main_frame.columnconfigure(0, weight=1)
        main_frame.rowconfigure(2, weight=1)
        self.activity = ActivityBanner(main_frame)

        header_frame = ttk.Frame(main_frame)
        self.header_frame = header_frame
        header_frame.grid(row=0, column=0, sticky=(tk.W, tk.E), pady=(0, 10))
        header_frame.columnconfigure(0, weight=1)

        self.title_label = ttk.Label(
            header_frame,
            text="Wabbajack Viewer",
            font=("Arial", 16, "bold"), wraplength=360, justify=tk.LEFT,
        )
        self.title_label.grid(row=0, column=0, sticky='w', padx=(0, 20))

        button_frame = ttk.Frame(header_frame)
        button_frame.grid(row=0, column=1, sticky='ne')
        self.header_button_frame = button_frame
        self.header_toolbar_widgets = []

        load_button = ttk.Button(button_frame, text="Open Wabbajack…", style='Accent.TButton', command=self.load_wabbajack_file)
        self.open_button = load_button
        self.header_toolbar_widgets.append((load_button, (0, 8)))
        self.add_tooltip(load_button, "Open a .wabbajack file and build the mod list from its embedded data.")
        self.cancel_load_button = self.activity.cancel_button
        self.setup_menus()

        self.profile_frame = ttk.Frame(header_frame)
        self.profile_frame.grid(row=1, column=0, columnspan=2, sticky='w', pady=(4, 0))
        self.profile_label = ttk.Label(self.profile_frame, text="Wabbajack profile:", style='Muted.TLabel')
        self.header_toolbar_widgets.append((self.profile_label, (8, 2)))
        self.add_tooltip(self.profile_label, "Choose which profile folder inside the archive to load.")
        self.profile_var = tk.StringVar()
        self.profile_combobox = ttk.Combobox(
            self.profile_frame, textvariable=self.profile_var, state="readonly", width=20
        )
        self.header_toolbar_widgets.append((self.profile_combobox, (0, 8)))
        self.add_tooltip(self.profile_combobox, "Select a different Wabbajack profile from the archive.")
        self.profile_combobox.bind("<<ComboboxSelected>>", self.on_profile_selected)
        self.profile_combobox.config(state=tk.DISABLED)

        self.source_var = tk.StringVar(value='No Wabbajack open.')
        self.source_label = ttk.Label(header_frame, textvariable=self.source_var, wraplength=1100, justify=tk.LEFT)
        self.source_label.grid(row=2, column=0, columnspan=2, sticky='ew', pady=(6, 0))
        self.source_tooltip = Tooltip(self.title_label, '')
        header_frame.bind('<Configure>', lambda event: self.source_label.configure(wraplength=max(200, event.width - 20)))

        self.layout_header_buttons()
        self.header_button_frame.bind("<Configure>", self.on_header_button_frame_configure)

        self.workspace = ttk.Notebook(main_frame)
        self.workspace.grid(row=2, column=0, sticky='nsew')
        content_frame = ttk.Frame(self.workspace)
        self.workbench_page = ttk.Frame(self.workspace)
        self.workspace.add(content_frame, text='Mods')
        self.workspace.add(self.workbench_page, text='Create profile copy')
        self.mods_page = content_frame
        self.comparison_view = None
        self.workbench_view = None
        self.workspace.bind('<<NotebookTabChanged>>', self.on_workspace_selected)
        content_frame.columnconfigure(0, weight=1)
        content_frame.rowconfigure(3, weight=1)

        self.mods_toolbar = ttk.Frame(content_frame, padding=(6, 4))
        self.mods_toolbar.grid(row=1, column=0, sticky='ew')
        self.mods_toolbar.columnconfigure(0, weight=1)
        search_frame = ttk.Frame(self.mods_toolbar)
        self.search_frame = search_frame
        search_frame.grid(row=0, column=0, sticky='w', padx=(0, 12))
        search_frame.columnconfigure(1, weight=1)
        ttk.Label(search_frame, text="Find mods:").grid(row=0, column=0, padx=(0, 6))
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", self.filter_mods)
        search_entry = ttk.Entry(search_frame, textvariable=self.search_var, width=40)
        search_entry.grid(row=0, column=1, sticky='ew')
        search_entry.bind('<Escape>', lambda event: self.search_var.set(''))
        self.clear_search_button = ttk.Button(search_frame, text='×', width=3, command=lambda: self.search_var.set(''))
        self.clear_search_button.grid(row=0, column=2, padx=(4, 0))
        self.clear_search_button.grid_remove()
        self.add_tooltip(self.clear_search_button, 'Clear search (Escape)')
        self.list_count_var = tk.StringVar()
        ttk.Label(search_frame, textvariable=self.list_count_var, style='Muted.TLabel').grid(row=0, column=4, padx=(8, 0))
        from comparison_presenter import FILTERS
        self.comparison_filter = tk.StringVar(value='All mods')
        self.comparison_filter_box = ttk.Combobox(search_frame, textvariable=self.comparison_filter,
            values=FILTERS, state='readonly', width=21)
        self.comparison_filter_box.grid(row=0, column=3, padx=(8, 0))
        self.comparison_filter_box.grid_remove()
        self.comparison_filter.trace_add('write', self.filter_mods)


        self.legend_frame = ttk.Frame(content_frame, padding=(6, 0, 6, 4))
        self.legend_frame.grid(row=2, column=0, sticky=(tk.W, tk.E))
        self.legend_frame.grid_remove()

        paned_window = ttk.PanedWindow(content_frame, orient=tk.VERTICAL)
        self.mod_details_panes = paned_window
        paned_window.grid(row=3, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))

        mod_frame = ttk.Frame(paned_window, padding=(6, 0, 6, 0))
        mod_frame.columnconfigure(0, weight=1)
        mod_frame.rowconfigure(0, weight=1)

        self.tree = ttk.Treeview(
            mod_frame,
            columns=("active", "name", "sources", "author", "version", "size", 'result', 'local_name', 'local_version', 'local_order', 'local_active'),
            displaycolumns=('active', 'name', 'sources', 'author', 'version', 'size'),
            show="tree headings",
        )
        self.tree.heading("#0", text="#", command=lambda: self.sort_by("number"))
        self.tree.heading("active", text="Active", command=lambda: self.sort_by("active"))
        self.tree.heading("name", text="Mod name", anchor=tk.W, command=lambda: self.sort_by("name"))
        self.tree.heading("sources", text="Archives", command=lambda: self.sort_by("sources"))
        self.tree.heading("author", text="Author", anchor=tk.W, command=lambda: self.sort_by("author"))
        self.tree.heading("version", text="Version", anchor=tk.W, command=lambda: self.sort_by("version"))
        self.tree.heading("size", text="Downloads (MiB)", command=lambda: self.sort_by("size"))

        self.tree.column("#0", width=50, minwidth=40, anchor=tk.CENTER, stretch=False)
        self.tree.column("active", width=55, minwidth=50, anchor=tk.CENTER, stretch=False)
        self.tree.column("name", width=330, minwidth=180)
        self.tree.column("sources", width=70, minwidth=55, anchor=tk.CENTER, stretch=False)
        self.tree.column("author", width=130, minwidth=80)
        self.tree.column("version", width=80, minwidth=50)
        self.tree.column("size", width=70, minwidth=55, anchor=tk.E, stretch=False)
        for column, title, width in (('result', 'Result', 185), ('local_name', 'Installed mod', 245),
                ('local_version', 'Installed version', 105), ('local_order', 'Installed #', 82), ('local_active', 'Installed active', 100)):
            self.tree.heading(column, text=title, command=lambda column=column: self.sort_by(column))
            self.tree.column(column, width=width, minwidth=65)

        tree_scroll = ttk.Scrollbar(mod_frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=tree_scroll.set)

        self.tree.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        tree_scroll.grid(row=0, column=1, sticky=(tk.N, tk.S))
        horizontal = ttk.Scrollbar(mod_frame, orient=tk.HORIZONTAL, command=self.tree.xview)
        horizontal.grid(row=1, column=0, sticky='ew')
        self.mod_horizontal = horizontal
        def update_horizontal(first, last):
            horizontal.set(first, last)
            if float(first) <= 0 and float(last) >= 1:
                horizontal.grid_remove()
            else:
                horizontal.grid()
        self.tree.configure(xscrollcommand=update_horizontal)

        self.separator_font = tkfont.Font(font="TkDefaultFont")
        self.separator_font.configure(weight="bold")
        self.tree.tag_configure("separator", font=self.separator_font, background=COLORS['group'], foreground=COLORS['muted'])
        self.tree.tag_configure("compare_green", foreground=COLORS['green'])
        self.tree.tag_configure("compare_yellow", foreground=COLORS['amber'])
        self.tree.tag_configure("compare_red", foreground=COLORS['red'])
        self.tree.tag_configure("compare_external_only", foreground=COLORS['blue'])

        details_frame = ttk.Frame(paned_window, padding=(6, 6, 6, 0))
        self.details_frame = details_frame
        details_frame.columnconfigure(0, weight=1)
        details_frame.rowconfigure(1, weight=1)
        self.details_title = tk.StringVar(value='Select a mod')
        details_heading = ttk.Label(details_frame, textvariable=self.details_title,
                                    font=('TkDefaultFont', 11, 'bold'), wraplength=800)
        details_heading.grid(row=0, column=0, sticky='ew', pady=(0, 6))
        details_frame.bind('<Configure>', lambda event: details_heading.configure(
            wraplength=max(200, event.width - 12)))
        self.details_toggle = ttk.Button(details_frame, text='Hide details', command=self.toggle_details)
        self.details_toggle.grid(row=0, column=1, sticky='e', padx=(8, 0), pady=(0, 6))
        self.show_details_button = ttk.Button(search_frame, text='Show details', command=self.toggle_details)
        self.show_details_button.grid(row=0, column=5, padx=(8, 0))
        self.show_details_button.grid_remove()

        self.notebook = ttk.Notebook(details_frame)
        self.notebook.grid(row=1, column=0, columnspan=2, sticky=(tk.W, tk.E, tk.N, tk.S))

        overview_frame = ttk.Frame(self.notebook)
        self.notebook.add(overview_frame, text="Overview")

        self.overview_text = ReadOnlyText(overview_frame, wrap=tk.WORD)
        self.overview_text.pack(fill=tk.BOTH, expand=True)

        self.overview_text.tag_configure("link", foreground=COLORS['accent'], underline=True)
        self.overview_text.tag_bind("link", "<Button-1>", self.open_link)
        self.overview_text.tag_bind("link", "<Enter>", lambda e: self.overview_text.config(cursor="hand2"))
        self.overview_text.tag_bind("link", "<Leave>", lambda e: self.overview_text.config(cursor=""))

        self.comparison_detail_frame = ttk.Frame(self.notebook)
        self.comparison_detail_text = scrolledtext.ScrolledText(self.comparison_detail_frame, wrap=tk.WORD, state=tk.DISABLED)
        style_text(self.comparison_detail_text)
        self.comparison_detail_text.pack(fill=tk.BOTH, expand=True)
        self.open_local_button = ttk.Button(self.comparison_detail_frame, text='Open installed mod folder',
            command=self.open_installed_mod_folder, state=tk.DISABLED)
        self.open_local_button.pack(anchor='w', pady=4)

        sources_frame = ttk.Frame(self.notebook)
        self.notebook.add(sources_frame, text="Install Instructions")

        sources_search_frame = ttk.Frame(sources_frame)
        sources_search_frame.grid(row=0, column=0, sticky=(tk.W, tk.E), pady=(0, 6))
        sources_search_frame.columnconfigure(1, weight=1)
        ttk.Label(sources_search_frame, text="Find instructions:").grid(row=0, column=0, padx=(0, 6))
        self.sources_search_var = tk.StringVar()
        self.sources_search_var.trace_add("write", self.on_sources_search_changed)
        ttk.Entry(sources_search_frame, textvariable=self.sources_search_var, width=20).grid(row=0, column=1, sticky='ew')
        self.sources_hide_mohidden_var = tk.BooleanVar(value=False)
        self.sources_hide_mohidden_check = ttk.Checkbutton(
            sources_search_frame,
            text="Hide .mohidden",
            variable=self.sources_hide_mohidden_var,
            command=self.on_sources_filter_changed,
        )
        self.sources_hide_mohidden_check.grid(row=0, column=2, padx=(10, 0))
        self.source_details_button = ttk.Button(sources_search_frame, text='File details…', state=tk.DISABLED,
                                                command=lambda: self.show_file_details('sources'))
        self.source_details_button.grid(row=0, column=3, padx=(10, 0))
        source_footer = ttk.Frame(sources_frame)
        source_footer.grid(row=3, column=0, sticky='ew', pady=(4, 0))
        source_footer.columnconfigure(1, weight=1)
        self.source_downloads_button = ttk.Button(source_footer, text='Downloads folder…', command=self.choose_source_downloads)
        self.source_downloads_button.grid(row=0, column=0)
        self.preferred_source_status = tk.StringVar(value='Choose downloads to verify preferred file sources.')
        source_status = ttk.Label(source_footer, textvariable=self.preferred_source_status, wraplength=800, style='Muted.TLabel')
        source_status.grid(row=0, column=1, sticky='ew', padx=8)
        source_footer.bind('<Configure>', lambda event: source_status.configure(wraplength=max(180, event.width - 180)))

        self.sources_tree = ttk.Treeview(
            sources_frame,
            columns=("archive_filename", "target_path", "source_path", "size", "kind"),
            show="tree headings",
        )
        self.sources_tree.heading("#0", text="Source (recorded title)", command=lambda: self.on_sources_sort_requested("source"))
        self.sources_tree.heading('archive_filename', text='Archive filename', command=lambda: self.on_sources_sort_requested('archive_filename'))
        self.sources_tree.heading("target_path", text="Install path", command=lambda: self.on_sources_sort_requested("target_path"))
        self.sources_tree.heading("source_path", text="Source path", command=lambda: self.on_sources_sort_requested("source_path"))
        self.sources_tree.heading("size", text="Size (bytes)", command=lambda: self.on_sources_sort_requested("size"))
        self.sources_tree.heading("kind", text="Directive type", command=lambda: self.on_sources_sort_requested("kind"))

        self.sources_tree.column("#0", width=260, minwidth=180)
        self.sources_tree.column('archive_filename', width=320, minwidth=180)
        self.sources_tree.column("target_path", width=360, minwidth=240)
        self.sources_tree.column("source_path", width=420, minwidth=260)
        self.sources_tree.column("size", width=110, minwidth=90, anchor=tk.E)
        self.sources_tree.column("kind", width=110, minwidth=90, anchor=tk.CENTER)

        sources_scroll = ttk.Scrollbar(sources_frame, orient=tk.VERTICAL, command=self.sources_tree.yview)
        self.sources_tree.configure(yscrollcommand=sources_scroll.set)

        self.sources_tree.grid(row=1, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        sources_scroll.grid(row=1, column=1, sticky=(tk.N, tk.S))
        sources_horizontal = ttk.Scrollbar(sources_frame, orient=tk.HORIZONTAL, command=self.sources_tree.xview)
        sources_horizontal.grid(row=2, column=0, sticky=(tk.W, tk.E))
        self.sources_tree.configure(xscrollcommand=sources_horizontal.set)

        sources_frame.columnconfigure(0, weight=1)
        sources_frame.rowconfigure(1, weight=1)
        self.sources_tree.bind("<Double-1>", self.on_source_activate)
        self.sources_tree.bind('<<TreeviewSelect>>', lambda event: self.on_file_selection('sources'))

        files_frame = ttk.Frame(self.notebook)
        self.notebook.add(files_frame, text="Output Files")

        files_search_frame = ttk.Frame(files_frame)
        files_search_frame.grid(row=0, column=0, sticky=(tk.W, tk.E), pady=(0, 6))
        files_search_frame.columnconfigure(1, weight=1)
        ttk.Label(files_search_frame, text="Find files:").grid(row=0, column=0, padx=(0, 6))
        self.files_search_var = tk.StringVar()
        self.files_search_var.trace_add("write", self.on_files_search_changed)
        ttk.Entry(files_search_frame, textvariable=self.files_search_var, width=20).grid(row=0, column=1, sticky='ew')
        self.files_hide_mohidden_var = tk.BooleanVar(value=False)
        self.files_hide_mohidden_check = ttk.Checkbutton(
            files_search_frame,
            text="Hide .mohidden",
            variable=self.files_hide_mohidden_var,
            command=self.on_files_filter_changed,
        )
        self.files_hide_mohidden_check.grid(row=0, column=2, padx=(10, 0))
        self.file_details_button = ttk.Button(files_search_frame, text='File details…', state=tk.DISABLED,
                                              command=lambda: self.show_file_details('files'))
        self.file_details_button.grid(row=0, column=3, padx=(10, 0))
        output_help = ttk.Label(files_frame, text='Expected package output, arranged by destination. Double-click a file for installation details.', style='Muted.TLabel')
        output_help.grid(row=3, column=0, sticky='ew', pady=(4, 0))
        files_frame.bind('<Configure>', lambda event: output_help.configure(wraplength=max(200, event.width - 20)))

        self.files_tree = ttk.Treeview(files_frame, columns=("source_mod", "source_path", "size", "kind", "hash"), show="tree headings")
        self.files_tree.heading("#0", text="Output path", command=lambda: self.on_files_sort_requested("target_path"))
        self.files_tree.heading("source_mod", text="Source archive", command=lambda: self.on_files_sort_requested("source_mod"))
        self.files_tree.heading("source_path", text="Source path", command=lambda: self.on_files_sort_requested("source_path"))
        self.files_tree.heading("size", text="Size (bytes)", command=lambda: self.on_files_sort_requested("size"))
        self.files_tree.heading("kind", text="Directive type")
        self.files_tree.heading("hash", text="Recorded output hash")

        self.files_tree.column("#0", width=300, minwidth=200)
        self.files_tree.column("source_mod", width=210, minwidth=140)
        self.files_tree.column("source_path", width=300, minwidth=200)
        self.files_tree.column("size", width=100, minwidth=80)
        self.files_tree.column("kind", width=150, minwidth=110)
        self.files_tree.column("hash", width=160, minwidth=130)

        files_scroll = ttk.Scrollbar(files_frame, orient=tk.VERTICAL, command=self.files_tree.yview)
        self.files_tree.configure(yscrollcommand=files_scroll.set)

        self.files_tree.grid(row=1, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        files_scroll.grid(row=1, column=1, sticky=(tk.N, tk.S))
        files_horizontal = ttk.Scrollbar(files_frame, orient=tk.HORIZONTAL, command=self.files_tree.xview)
        files_horizontal.grid(row=2, column=0, sticky=(tk.W, tk.E))
        self.files_tree.configure(xscrollcommand=files_horizontal.set)
        self.files_tree.bind('<<TreeviewSelect>>', lambda event: self.on_file_selection('files'))
        self.files_tree.bind('<Double-1>', self.on_output_file_activate)

        self.add_tooltip(self.sources_hide_mohidden_check, "Hide source file entries whose path ends with .mohidden.")
        self.add_tooltip(self.files_hide_mohidden_check, "Hide file entries whose path ends with .mohidden.")

        files_frame.columnconfigure(0, weight=1)
        files_frame.rowconfigure(1, weight=1)

        paned_window.add(mod_frame, weight=1)
        paned_window.add(details_frame, weight=1)

        self.tree.bind("<<TreeviewSelect>>", self.on_mod_select)
        self.tree.bind('<Configure>', lambda event: self.tree.see(self.tree.selection()[0]) if self.tree.selection() else None)
        self.tree.bind("<Button-3>", self.on_mod_right_click)
        self.tree.bind("<Control-c>", self.copy_tree_selection)
        self.tree.bind("<Control-C>", self.copy_tree_selection)
        from table_columns import TableColumns
        self.mod_columns = TableColumns(self.tree, self.column_widths, self.persist_preferences)
        self.source_columns = TableColumns(self.sources_tree, self.column_widths, self.persist_preferences, cap=520)
        self.file_columns = TableColumns(self.files_tree, self.column_widths, self.persist_preferences, cap=520)
        self.source_columns.configure('sources')
        self.file_columns.configure('files')

        self.populate_mod_list()
        self.update_sort_indicators()

        status_frame = ttk.Frame(main_frame)
        self.status_frame = status_frame
        status_frame.grid(row=3, column=0, sticky=(tk.W, tk.E), pady=(10, 0))

        self.status_var = tk.StringVar()
        self.status_var.set("No Wabbajack open")
        status_label = ttk.Label(status_frame, textvariable=self.status_var, style='Muted.TLabel', wraplength=1100)
        status_label.pack(side=tk.LEFT, fill=tk.X, expand=True)
        status_frame.bind('<Configure>', lambda event: status_label.configure(wraplength=max(200, event.width - 20)))
        from comparison_ui import InstalledComparisonControls
        self.comparison_view = InstalledComparisonControls(self.mods_toolbar, self.set_installed_report,
            can_start=lambda: not self._closing and not getattr(self, 'loading_package', False) and not (self.workbench_view and self.workbench_view.busy))
        self.comparison_view.window.grid(row=0, column=1, sticky='ne')
        self.mods_toolbar.bind('<Configure>', self.layout_mods_toolbar)
        self.search_frame.bind('<Configure>', self.layout_mods_toolbar)
        self.layout_mods_toolbar()

        self._mods_pane_height = None
        def set_sash_position(event=None):
            if self.details_hidden:
                return
            height = paned_window.winfo_height()
            if height > 100 and (height != self._mods_pane_height or (event and event.widget is self.notebook)):
                self._mods_pane_height = height
                tab = self.notebook.tab(self.notebook.select(), 'text')
                reserve = 260 if tab in ('Install Instructions', 'Output Files') else 180
                position = max(min(145, height // 3), min(int(height * self.details_split), height - reserve))
                paned_window.sashpos(0, position)
        self._position_details = set_sash_position
        paned_window.bind('<Configure>', set_sash_position)
        self.notebook.bind('<<NotebookTabChanged>>', set_sash_position)
        def remember_sash(event):
            if not self.details_hidden and paned_window.winfo_height() > 100:
                self.details_split = min(.75, max(.2, paned_window.sashpos(0) / paned_window.winfo_height()))
                self.persist_preferences()
        paned_window.bind('<ButtonRelease-1>', remember_sash)
        if self.details_hidden:
            paned_window.forget(details_frame)
            self.show_details_button.grid()
        self.root.bind('<Destroy>', self.on_root_destroy, add='+')

    def toggle_details(self):
        """Collapse details without losing the selection, tab or divider preference."""
        self.details_hidden = not self.details_hidden
        if self.details_hidden:
            self.mod_details_panes.forget(self.details_frame)
        else:
            self.mod_details_panes.add(self.details_frame, weight=1)
            self.mod_details_panes.update_idletasks()
            self._mods_pane_height = None
            self._position_details()
        if self.details_hidden:
            self.show_details_button.grid()
        else:
            self.show_details_button.grid_remove()
        self.persist_preferences()

    def on_header_button_frame_configure(self, event=None):
        """Reflow toolbar buttons whenever header width changes."""
        self.layout_header_buttons()

    def layout_header_buttons(self):
        """Keep the package profile below its title and Open at the right."""
        if not hasattr(self, "header_toolbar_widgets") or not hasattr(self, "header_button_frame"):
            return

        self.open_button.grid(row=0, column=0, sticky='e')
        self.profile_label.grid(row=0, column=0, sticky='w', padx=(0, 6))
        if len(self.profile_combobox['values']) > 1 and not self.loaded_from_saved_modlist:
            self.profile_combobox.grid(row=0, column=1, sticky='w')
        else:
            self.profile_combobox.grid_remove()
        if self.modlist_data:
            self.profile_frame.grid()
        else:
            self.profile_frame.grid_remove()

    def layout_mods_toolbar(self, event=None):
        if not self.comparison_view:
            return
        width = self.mods_toolbar.winfo_width()
        # Filters get their own line before the search field becomes cramped.
        narrow = width < 1100
        self.comparison_filter_box.grid_configure(row=1 if narrow else 0, column=1 if narrow else 3,
                                                  sticky='w', pady=(4, 0) if narrow else 0)
        if not self.installed_report:
            self.comparison_filter_box.grid_remove()
        needed = self.search_frame.winfo_reqwidth() + self.comparison_view.window.winfo_reqwidth() + 24
        self.comparison_view.window.grid_configure(row=1 if width < needed else 0,
                                                   column=0 if width < needed else 1,
                                                   sticky='ew' if width < needed else 'ne',
                                                   pady=(6, 0) if width < needed else 0)

    def update_legend_visibility(self):
        """Show the installed comparison legend while results are displayed."""
        if not hasattr(self, "legend_frame"):
            return
        for child in self.legend_frame.winfo_children():
            child.destroy()
        if not self.installed_report:
            self.legend_frame.grid_remove()
            return
        from collections import Counter
        from comparison_presenter import category
        counts = Counter(category(row) for row in self.installed_report['rows'])
        labels = [(f'Matches: {counts["Matches"]:,}', COLORS['green']),
                  (f'Differs / needs review: {counts["Different"] + counts["Needs review"]:,}', COLORS['amber']),
                  (f'Wabbajack only: {counts["Wabbajack only"]:,}', COLORS['red']),
                  (f'Installed only: {counts["Installed only"]:,}', COLORS['blue'])]
        for index, (label, color) in enumerate(labels):
            ttk.Label(self.legend_frame, text='● ' + label, foreground=color).grid(row=0, column=index, sticky='w', padx=(0, 12))
        self.legend_frame.grid()

    def refresh_profile_selector(self):
        """Populate and enable/disable the profile combobox."""
        if not hasattr(self, "profile_combobox"):
            return
        profiles = getattr(self, "all_profile_paths", [])
        profile_names = [p.rsplit("\\", 1)[0].split("\\")[-1] for p in profiles]
        model = getattr(self, 'package_model', None)
        if model:
            profile_names = [model.profiles.get(name.casefold(), name) for name in profile_names]
        if len(profile_names) > 1 and not self.loaded_from_saved_modlist:
            self.profile_label.configure(text='Wabbajack profile:')
            self.profile_combobox["values"] = profile_names
            self.profile_combobox.config(state="readonly")
            current = self.selected_profile_name()
            if current in profile_names:
                self.profile_var.set(current)
            else:
                self.profile_var.set(profile_names[0])
        else:
            self.profile_combobox["values"] = profile_names
            self.profile_combobox.config(state=tk.DISABLED)
            if profile_names:
                self.profile_var.set(self.selected_profile_name() or profile_names[0])
                self.profile_label.configure(text='Wabbajack profile: ' + self.profile_var.get())
            else:
                self.profile_var.set("")
                self.profile_label.configure(text='No Wabbajack profile' if self.modlist_data else 'Wabbajack profile:')
        self.layout_header_buttons()

    def selected_profile_name(self):
        name = self.active_profile.split('\\')[-1] if self.active_profile else ''
        model = getattr(self, 'package_model', None)
        return model.profiles.get(name.casefold(), name) if model else name

    def setup_menus(self):
        self.menu_bar = ttk.Frame(self.root)
        self.menu_bar.grid(row=0, column=0, sticky='ew', padx=10, pady=(4, 0))
        self.menu_buttons = []

        def dropdown(label):
            button = ttk.Menubutton(self.menu_bar, text=label, width=0,
                                    style='Menu.TMenubutton', direction='below')
            popup = tk.Menu(button, tearoff=False, background=COLORS['surface'], foreground=COLORS['text'],
                            activebackground=COLORS['selection'], activeforeground=COLORS['text'],
                            disabledforeground='#818b99', selectcolor=COLORS['accent'],
                            relief='solid', borderwidth=1, activeborderwidth=0, font='TkDefaultFont')
            button.configure(menu=popup)
            button.pack(side=tk.LEFT, padx=(0, 4))
            self.menu_buttons.append(button)
            return popup

        self.export_menu = dropdown('Export')
        self.export_menu.add_command(label='Mod order (modlist.txt)…', command=self.export_modlist_txt, state=tk.DISABLED)
        self.export_menu.add_command(label='Separator folders…', command=self.export_separators_zip, state=tk.DISABLED)
        self.saved_views_menu = dropdown('Snapshots')
        self.saved_views_menu.add_command(label='Open snapshot…', command=self.load_saved_modlist)
        self.saved_views_menu.add_command(label='Save snapshot…', command=self.save_modlist, state=tk.DISABLED)
        self.saved_views_menu.add_checkbutton(label='Restore last session on startup', variable=self.restore_session_var,
                                             command=self.persist_preferences)
        self.saved_views_menu.add_command(label='Recheck archive sources', command=self.start_preferred_source_checks)
        help_menu = dropdown('Help')
        help_menu.add_command(label='How to use', command=lambda: messagebox.showinfo('Wabbajack Viewer',
            '1. Open a Wabbajack and choose its profile.\n'
            '2. In Mods, choose an installed profile, then Compare. Settings… lets you choose its profiles folder and optional content checks.\n'
            '3. Select a row for Comparison evidence, Overview, Install Instructions or Output Files.\n'
            '4. Profile file differences… shows mod order, plugin and INI text differences.\n\n'
            'Comparison, file-content checks, profile-file differences and reports support both Amethyst and Mod Organizer 2 (MO2). '
            'For MO2, select its profiles folder manually. Use Choose mods folder… if automatic detection fails, '
            'including for custom profile locations or Windows paths accessed from Linux.\n\n'
            'Column widths are remembered. Right-click a heading to fit columns to their contents.\n\n'
            'The Activity panel shows progress, elapsed time and Cancel while opening or checking sources. Snapshot saving also runs in the background.\n\n'
            'Create profile copy supports Amethyst only and currently requires Linux; MO2 profile copying is not supported. '
            'A separate root folder is copied only when the source uses one. Shared games stay shared.\n'
            'Snapshots save the parsed mod list and verified source choices. The last session is saved automatically. '
            'Restore last session on startup can be turned off in Snapshots. '
            'Comparison resumes when the original Wabbajack is present and unchanged.'))
        self.root.bind('<Control-o>', lambda event: self.load_wabbajack_file())

    def sync_workflow(self):
        model = getattr(self, 'package_model', None)
        if self.loaded_from_saved_modlist:
            model = None
        selected = self.selected_profile_name()
        identity = (id(model), selected)
        changed = identity != getattr(self, '_workspace_context', None)
        self._workspace_context = identity
        if changed:
            self.comparison_view.set_package_context(model, selected)
        name = self.modlist_data.get('Name', '') if self.modlist_data else ''
        version = self.modlist_data.get('Version', '') if self.modlist_data else ''
        self.title_label.configure(text=f'{name} {version}'.strip() or 'Wabbajack Viewer')
        self.open_button.configure(style='TButton' if self.modlist_data else 'Accent.TButton')
        if self.loaded_from_saved_modlist:
            self.source_var.set(f"Mod list snapshot: {self.modlist_data.get('Name', 'Unknown')} · {selected or 'No profile'}\n{getattr(self, 'snapshot_restore_reason', '')} Open the original Wabbajack to compare installed mods.")
        elif model:
            self.source_var.set('')
            if not selected:
                self.source_var.set('No mod profile is included. Showing the download inventory.')
        else:
            self.source_var.set('No Wabbajack open.')
        if self.source_var.get():
            self.source_label.grid()
        else:
            self.source_label.grid_remove()
        self.source_tooltip.hide()
        self.source_tooltip.text = str(model.path) if model else getattr(self, 'loaded_snapshot_path', '') if self.loaded_from_saved_modlist else ''
        has_rows = bool(self.mod_rows)
        self.workspace.tab(0, text='Mods' if selected or not model else 'Downloads')
        self.export_menu.entryconfigure(0, state=tk.NORMAL if has_rows and selected else tk.DISABLED)
        self.export_menu.entryconfigure(1, state=tk.NORMAL if self.get_separator_folder_names() else tk.DISABLED)
        self.saved_views_menu.entryconfigure(1, state=tk.NORMAL if has_rows and selected else tk.DISABLED)
        self.update_legend_visibility()
        self.on_workspace_selected()

    def on_workspace_selected(self, event=None):
        if self._initial_load:
            self.status_frame.grid_remove()
            return
        if hasattr(self, 'status_frame'):
            self.status_frame.grid_remove() if self.workspace.select() == str(self.workbench_page) else self.status_frame.grid()
        if self.workspace.select() == str(self.workbench_page) and self.workbench_view is None:
            from profile_copy_ui import ProfileCopyView
            self.workbench_view = ProfileCopyView(self.workbench_page,
                folder=self.comparison_view.folder.get(), selected=self.comparison_view.selected_profile.get(),
                busy_changed=self.set_workbench_busy, can_start=lambda: not self._closing and not getattr(self, 'loading_package', False) and not self.comparison_view.running,
                copied=self.comparison_view.refresh_profiles)
            self.workbench_view.window.pack(fill=tk.BOTH, expand=True)

    def set_installed_report(self, report):
        self.installed_report = report
        if not hasattr(self, 'tree'):
            return
        self.clear_mod_details()
        if report:
            self.notebook.insert(0, self.comparison_detail_frame, text='Comparison')
            self.notebook.select(self.comparison_detail_frame)
            self.sort_column, self.sort_reverse = 'number', False
            self.comparison_filter_box.grid()
        else:
            if str(self.comparison_detail_frame) in self.notebook.tabs():
                self.notebook.hide(self.comparison_detail_frame)
            self.comparison_filter_box.grid_remove()
            self.comparison_filter.set('All mods')
        self.update_sort_indicators()
        self.configure_mod_columns()
        self.apply_filters_and_sort()
        self.update_legend_visibility()
        first = next((item for item, row in self.row_by_item_id.items() if not row.get('is_separator')), None)
        if first:
            self.tree.selection_set(first)
            self.on_mod_select(None)

    def configure_mod_columns(self):
        from comparison_presenter import display_rows, result_label
        from profile_compare import enabled_label
        report = self.installed_report
        rows = display_rows(self.mod_rows, report) if report else self.mod_rows
        data = []
        for row in rows:
            comparison = row.get('comparison') or {}
            data.append({'#0': row.get('number', ''), 'active': self.get_activation_mark_for_row(row),
                'name': '' if row.get('installed_only') else row['name'], 'sources': self.archive_count(row),
                'author': row.get('author', ''), 'version': comparison.get('package_version', row.get('version', '')),
                'size': self.preferred_row(row).get('size_mb', ''), 'result': result_label(comparison) if comparison else '',
                'local_name': comparison.get('local_name', ''), 'local_version': comparison.get('local_version', ''),
                'local_order': comparison.get('local_order') or '', 'local_active': enabled_label(comparison.get('local_enabled'))})
        self.mod_columns.configure('comparison' if report else 'mods',
            ('local_order', 'result', 'name', 'local_name', 'version', 'local_version', 'active', 'local_active') if report
            else ('active', 'name', 'sources', 'author', 'version', 'size'), data)

    def set_workbench_busy(self, busy):
        if busy:
            self.comparison_view.set_blocked(True)
            self._workbench_widget_states = []
            for widget, _ in self.header_toolbar_widgets:
                try:
                    self._workbench_widget_states.append((widget, widget.cget('state')))
                    widget.configure(state=tk.DISABLED)
                except tk.TclError:
                    pass
            self._workbench_menu_states = []
            for menu in (self.saved_views_menu, self.export_menu):
                for index in range(menu.index('end') + 1):
                    self._workbench_menu_states.append((menu, index, menu.entrycget(index, 'state')))
                    menu.entryconfigure(index, state=tk.DISABLED)
        else:
            for widget, state in self._workbench_widget_states:
                widget.configure(state=state)
            for menu, index, state in self._workbench_menu_states:
                menu.entryconfigure(index, state=state)
            self.sync_workflow()
            self.comparison_view.set_blocked(False)

    def request_close(self):
        if self.workbench_view and self.workbench_view.busy:
            messagebox.showwarning('Profile copy in progress',
                'Wait for profile preview or copying to finish before closing.', parent=self.root)
            return
        if self._closing:
            return
        self._closing = True
        self.comparison_view.set_blocked(True)
        self.cancel_preferred_source_checks()
        if hasattr(self, 'load_cancel'):
            self.load_cancel.set()
        self.autosave_session()
        if self._save_thread is None:
            self.root.destroy()
        else:
            self.activity.start('save', 'Saving session before closing')
            self.set_status('The window will close when the session has finished saving…')

    def populate_mod_list(self):
        """Populate the treeview with current filtered/sorted rows."""
        self.apply_filters_and_sort()

    def apply_filters_and_sort(self):
        """Apply text filter and sorting, then render list rows."""
        search_text = self.search_var.get().lower().strip()

        rows = list(self.mod_rows)
        report = getattr(self, 'installed_report', None)
        if report:
            from comparison_presenter import display_rows
            rows = display_rows(rows, report)

        def row_matches_search(row):
            comparison = row.get('comparison')
            if report:
                from comparison_presenter import matches_filter
                selected_filter = self.comparison_filter.get()
                if row.get('is_separator'):
                    if selected_filter != 'All mods':
                        return False
                elif not matches_filter(comparison, selected_filter):
                    return False
                if comparison and search_text:
                    return search_text in json.dumps(comparison, ensure_ascii=False).casefold()
            if not search_text:
                return True
            return (
                search_text in (row["name"] or "").lower()
                or search_text in (row["author"] or "").lower()
                or search_text in (row["archive_name"] or "").lower()
            )

        def sort_key(item):
            comparison = item.get('comparison') or {}
            if self.sort_column == 'result':
                from comparison_presenter import result_label
                return result_label(comparison) if comparison else ''
            if self.sort_column in ('local_name', 'local_version', 'local_order', 'local_active'):
                field = 'local_enabled' if self.sort_column == 'local_active' else self.sort_column
                value = comparison.get(field)
                return (value or 0) if field in ('local_order', 'local_enabled') else str(value or '').casefold()
            if self.sort_column == "number":
                number = item.get("number", 0)
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
                return self.archive_count(item)
            if self.sort_column == "author":
                return str(item.get("author", "")).lower()
            if self.sort_column == "version":
                return str(item.get("version", "")).lower()
            if self.sort_column == "size":
                try:
                    return float(self.preferred_row(item).get("size_mb") or 0)
                except Exception:
                    return 0.0
            return item.get("number", 0)

        if self.sort_column == "number":
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
        self.clear_mod_details()

        def values_for(row):
            from comparison_presenter import result_label
            from profile_compare import enabled_label
            comparison = row.get('comparison')
            active = '—' if row.get('installed_only') else self.get_activation_mark_for_row(row)
            name = '' if row.get('installed_only') else row['name']
            return (active, name, self.archive_count_label(row), row['author'],
                    comparison['package_version'] if comparison else row['version'], self.preferred_row(row).get('size_mb', ''), result_label(comparison) if comparison else '',
                    comparison['local_name'] if comparison else '', comparison['local_version'] if comparison else '',
                    comparison.get('local_order') or '' if comparison else '',
                    enabled_label(comparison['local_enabled']) if comparison else '')

        visible_count = 0
        for header, children in sections:
            if header is None:
                parent_id = ""
            else:
                values = values_for(header)
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
                values = values_for(row)
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

        total_count = len(rows)
        self.list_count_var.set(f'{total_count:,} mods' if visible_count == total_count else f'{visible_count:,} of {total_count:,} mods')
        if self.search_var.get():
            self.clear_search_button.grid()
        else:
            self.clear_search_button.grid_remove()
        if hasattr(self, "status_var"):
            if self.active_profile:
                profile_name = self.selected_profile_name()
                self.status_var.set(f"Profile '{profile_name}': showing {visible_count} of {total_count} entries")
            else:
                self.status_var.set(f"Showing {visible_count} of {total_count} entries")

        self.update_sort_indicators()

    def sort_by(self, column):
        """Sort list by number or name."""
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

    def raw_directive_for_file(self, file_info):
        key = str(file_info.get('target_path', '')).replace('\\', '/').casefold()
        model = getattr(self, 'package_model', None)
        if model:
            return model.destinations.get(key)
        manifest = getattr(self, 'modlist_data', None) or {}
        if getattr(self, '_detail_manifest', None) is not manifest:
            self._detail_manifest = manifest
            self._detail_directives = {
                str(item.get('To', '')).replace('\\', '/').casefold(): item
                for item in manifest.get('Directives', []) if isinstance(item, dict)}
        return self._detail_directives.get(key)

    def preferred_key(self, row):
        return (id(row.get('folder_files')), getattr(self, 'downloads_folder', ''))

    def preferred_row(self, row):
        result = getattr(self, '_preferred_cache', {}).get(self.preferred_key(row))
        return presentation_row(row, result)

    def archive_count_label(self, row):
        if row.get('is_separator'):
            return ''
        if self.preferred_key(row) in self._preferred_pending:
            return '…'
        return str(self.archive_count(row))

    def archive_count(self, row):
        return sum(not str(archive.get('Hash', '')).startswith(self.INLINE_SOURCE_PREFIX)
                   for archive in self.preferred_row(row).get('source_archives', []))

    def choose_source_downloads(self):
        folder = filedialog.askdirectory(title='Downloads containing the mod archives',
                                         initialdir=self.downloads_folder or str(Path.home() / 'Downloads'))
        if folder:
            self.downloads_folder = folder
            self.persist_preferences()
            self.start_preferred_source_checks()
            if self.current_selected_row:
                self.on_mod_select(None)

    def verify_sources_before_display(self):
        """Finish source decisions before publishing the staged package's rows."""
        self._preferred_batch_keys = set()
        if not self.downloads_folder:
            return
        rows = [row for row in self.mod_rows if preferred_source_candidates(row, self.archives)]
        inspections = {}
        for index, row in enumerate(rows, 1):
            key = self.preferred_key(row)
            self._preferred_batch_keys.add(key)
            def progress(message):
                self.set_status(f'Verifying archive sources: {index}/{len(rows)} — {row["raw_name"]}')
            progress('')
            cached = self._preferred_cache.get(key)
            if cached and signature(cached['path']) == cached['signature']:
                continue
            result = verify_preferred_sources(row, self.downloads_folder, self.archives,
                                              progress=progress, inspections=inspections)
            result['_files'] = row['folder_files']
            self._preferred_cache[key] = result

    def start_preferred_source_checks(self, force=True):
        """Check the entire authored list, including rows hidden by current filters."""
        if self._closing or getattr(self, 'loading_package', False) or (self.workbench_view and self.workbench_view.busy):
            return
        self._preferred_generation += 1
        self._preferred_pending.clear()
        self._preferred_batch_keys = set()
        while True:
            try:
                self._preferred_tasks.get_nowait()
            except queue.Empty:
                break
        if not self.downloads_folder:
            return
        for row in self.mod_rows:
            if preferred_source_candidates(row, self.archives):
                self._preferred_batch_keys.add(self.preferred_key(row))
                self.request_preferred_source_check(row, update_status=False, force=force)
        for item, row in self.row_by_item_id.items():
            self.tree.set(item, 'sources', self.archive_count_label(row))
        self.update_preferred_source_progress()

    def cancel_preferred_source_checks(self):
        self._preferred_generation += 1
        self._preferred_pending.clear()
        self._preferred_batch_keys.clear()
        for item, row in self.row_by_item_id.items():
            self.tree.set(item, 'sources', self.archive_count_label(row))
        self.activity.finish('sources')
        self.preferred_source_status.set('Source checks cancelled. Completed results are retained.')
        self.set_status('Archive source checks cancelled.')

    def update_preferred_source_progress(self):
        total = len(self._preferred_batch_keys)
        if total:
            remaining = len(self._preferred_batch_keys & self._preferred_pending)
            self.set_status(f'Checking archive sources: {total - remaining}/{total} mods' if remaining
                            else f'Archive source checks complete: {total} mods checked')
        if not self._preferred_pending:
            self.activity.finish('sources')

    def preferred_source_worker(self):
        """One worker bounds archive I/O even when the package contains many mods."""
        while not self._preferred_stop.is_set():
            try:
                generation, key, row, folder, archives = self._preferred_tasks.get(timeout=.2)
            except queue.Empty:
                continue
            if generation != self._preferred_generation:
                continue
            try:
                def progress(message):
                    if self._preferred_stop.is_set() or generation != self._preferred_generation:
                        raise SourceVerificationCancelled('Source verification cancelled.')
                checked = verify_preferred_sources(row, folder, archives, progress=progress)
            except Exception as error:
                checked = {'alternatives': {}, 'path': '', 'signature': signature(''), 'message': str(error)}
            checked['_files'] = row['folder_files']
            self._preferred_events.put((generation, key, checked))

    def request_preferred_source_check(self, row, update_status=True, force=False):
        folder = self.downloads_folder
        if not folder:
            if update_status:
                self.preferred_source_status.set('Choose downloads to verify preferred file sources.')
            return
        if not preferred_source_candidates(row, self.archives):
            if update_status:
                self.preferred_source_status.set('No alternate file sources to resolve for this mod.')
            return
        key = self.preferred_key(row)
        result = self._preferred_cache.get(key)
        if not force and result and signature(result['path']) == result['signature']:
            count = len(result['alternatives'])
            text = f'{count} file(s) verified in the mod archive. Recorded sources remain in file details.' if count else result['message'] or 'No verified source substitutions for this mod.'
            if update_status:
                self.preferred_source_status.set(text)
            return
        if update_status:
            self.preferred_source_status.set('Checking the mod archive for equivalent files…')
        if key in self._preferred_pending:
            return
        self._preferred_pending.add(key)
        if self.activity.owner not in ('sources', 'load'):
            self.activity.start('sources', 'Checking archive sources', self.cancel_preferred_source_checks)
            self.set_status('Verifying matching files in the downloads folder…')
        self._preferred_tasks.put((self._preferred_generation, key, row, folder, list(self.archives)))
        if not getattr(self, '_preferred_worker', None) or not self._preferred_worker.is_alive():
            self._preferred_worker = threading.Thread(target=self.preferred_source_worker, daemon=True)
            self._preferred_worker.start()
        if not getattr(self, '_preferred_poll_id', None):
            self._preferred_poll_id = self.root.after(100, self.poll_preferred_sources)

    def poll_preferred_sources(self):
        self._preferred_poll_id = None
        refresh = False
        updated = set()
        while not self._preferred_events.empty():
            generation, key, result = self._preferred_events.get_nowait()
            if generation != self._preferred_generation:
                continue
            self._preferred_pending.discard(key)
            self._preferred_cache[key] = result
            updated.add(key)
            refresh |= bool(self.current_selected_row and key == self.preferred_key(self.current_selected_row))
        for item, row in self.row_by_item_id.items():
            if self.preferred_key(row) in updated and not row.get('is_separator'):
                self.tree.set(item, 'sources', self.archive_count_label(row))
                self.tree.set(item, 'size', self.preferred_row(row).get('size_mb', ''))
        if refresh:
            self.on_mod_select(None)
        if updated:
            self.update_preferred_source_progress()
            if not self._preferred_pending:
                self.autosave_session()
        if self._preferred_pending and not self._preferred_poll_id:
            self._preferred_poll_id = self.root.after(100, self.poll_preferred_sources)

    def selected_detail_file(self, origin):
        tree = self.sources_tree if origin == 'sources' else self.files_tree
        selection = tree.selection()
        if not selection:
            return None
        if origin == 'sources':
            return self.row_by_source_item_id.get(selection[0], {}).get('file_info')
        return self.row_by_file_item_id.get(selection[0])

    def on_file_selection(self, origin):
        button = self.source_details_button if origin == 'sources' else self.file_details_button
        button.configure(state=tk.NORMAL if self.selected_detail_file(origin) else tk.DISABLED)

    def clear_file_details(self):
        self.row_by_source_item_id = {}
        self.row_by_file_item_id = {}
        for name in ('source_details_button', 'file_details_button'):
            if hasattr(self, name):
                getattr(self, name).configure(state=tk.DISABLED)
        window = getattr(self, 'file_detail_window', None)
        if window and window.winfo_exists():
            window.destroy()

    def show_file_details(self, origin):
        file_info = self.selected_detail_file(origin)
        if not file_info:
            return
        raw = self.raw_directive_for_file(file_info)
        groups = archive_contributions(self.preferred_row(self.current_selected_row or {}))
        group = next((item for item in groups if item['archive'].get('Hash') == file_info.get('archive_hash')), None)
        archive = group['archive'] if group else None
        description = describe_directive(file_info, raw, archive, group)
        model = getattr(self, 'package_model', None)
        if model and raw and directive_type(raw.get('$type')) == 'CreateBSA':
            inputs = model.ingredients(raw)
            description += f'\n\nTemporary input files: {len(inputs):,}'
            description += ''.join(f'\n{item["To"]}' for item in inputs)
        raw_text = (json.dumps(raw, indent=2, ensure_ascii=False) if raw is not None else
                    'The full directive is unavailable in this older snapshot. Open the original Wabbajack package to inspect it.')
        window = getattr(self, 'file_detail_window', None)
        if not window or not window.winfo_exists():
            window = self.file_detail_window = tk.Toplevel(self.root)
            window.geometry('850x600')
            notebook = ttk.Notebook(window)
            notebook.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
            self.file_detail_text = scrolledtext.ScrolledText(notebook, wrap=tk.WORD, state=tk.DISABLED)
            self.file_detail_raw = scrolledtext.ScrolledText(notebook, wrap=tk.WORD, state=tk.DISABLED)
            style_text(self.file_detail_text)
            self.file_detail_raw.configure(background=COLORS['surface'], foreground=COLORS['text'])
            notebook.add(self.file_detail_text, text='Installation details')
            notebook.add(self.file_detail_raw, text='Raw directive JSON')
            self.copy_directive_button = ttk.Button(window, text='Copy raw directive', command=self.copy_raw_directive)
            self.copy_directive_button.pack(anchor='w', padx=10, pady=(0, 10))
        window.title('File details — ' + str(file_info['target_path']).replace('\\', '/').rsplit('/', 1)[-1])
        for widget, text in ((self.file_detail_text, description), (self.file_detail_raw, raw_text)):
            widget.configure(state=tk.NORMAL)
            widget.delete('1.0', tk.END)
            widget.insert('1.0', text)
            widget.configure(state=tk.DISABLED)
        self.copy_directive_button.configure(state=tk.NORMAL if raw is not None else tk.DISABLED)
        window.lift()

    def copy_raw_directive(self):
        self.root.clipboard_clear()
        self.root.clipboard_append(self.file_detail_raw.get('1.0', 'end-1c'))

    def on_output_file_activate(self, event):
        if self.files_tree.identify_region(event.x, event.y) not in ('heading', 'separator'):
            self.show_file_details('files')

    def clear_mod_details(self):
        self.current_selected_row = None
        self.details_title.set('Select a mod')
        self.clear_file_details()
        if hasattr(self, 'comparison_detail_text'):
            self.comparison_detail_text.configure(state=tk.NORMAL)
            self.comparison_detail_text.delete('1.0', tk.END)
            self.comparison_detail_text.configure(state=tk.DISABLED)
            self.open_local_button.configure(state=tk.DISABLED)
        self.overview_text.delete('1.0', tk.END)
        self.overview_text.insert('1.0', 'Select a mod to see its details, install instructions and output files.')
        self.sources_tree.delete(*self.sources_tree.get_children())
        self.files_tree.delete(*self.files_tree.get_children())

    def open_installed_mod_folder(self):
        comparison = (self.current_selected_row or {}).get('comparison') or {}
        folder = comparison.get('local_folder')
        if folder and Path(folder).is_dir():
            webbrowser.open(Path(folder).absolute().as_uri())

    def on_mod_select(self, event):
        """Handle mod selection and show details."""
        selection = self.tree.selection()
        if not selection:
            self.clear_mod_details()
            return

        row = self.row_by_item_id.get(selection[0])
        if not row:
            return

        details_tab = self.notebook.select()
        self.current_selected_row = row
        self.details_title.set(row['name'])
        self.clear_file_details()
        self.request_preferred_source_check(row)
        display_row = self.preferred_row(row)
        self.tree.set(selection[0], 'sources', self.archive_count_label(row))
        self.tree.set(selection[0], 'size', display_row.get('size_mb', ''))

        comparison = row.get('comparison')
        if hasattr(self, 'comparison_detail_text'):
            from comparison_ui import format_comparison_details
            self.comparison_detail_text.configure(state=tk.NORMAL)
            self.comparison_detail_text.delete('1.0', tk.END)
            self.comparison_detail_text.insert('1.0', format_comparison_details(comparison) if comparison else 'Separators and unmanaged game entries are not compared.')
            self.comparison_detail_text.configure(state=tk.DISABLED)
            self.open_local_button.configure(state=tk.NORMAL if comparison and comparison.get('local_folder') and comparison['local_present'] else tk.DISABLED)

        if row.get('installed_only'):
            self.overview_text.delete('1.0', tk.END)
            self.overview_text.insert('1.0', 'This mod is listed in your installed profile and has no confirmed match in the selected Wabbajack profile.\n\n' + format_comparison_details(comparison))
            self.update_sources_tab(row)
            self.files_tree.delete(*self.files_tree.get_children())
            self.files_tree.insert('', 'end', text='Use Open installed mod folder to inspect its files and meta.ini.')
            self.notebook.select(self.comparison_detail_frame)
            return

        self.overview_text.delete(1.0, tk.END)
        if not self.active_profile and row.get('archive'):
            archive = row['archive']
            links = self.build_archive_links(archive)
            overview = f"Download archive: {archive.get('Name', row['name'])}\nSize: {archive.get('Size', 0):,} bytes\n"
            overview += '\nNo authored mod profile is available; this is a download record.\n'
            overview += '\n'.join(link for link in links.values() if link)
            self.overview_text.insert('1.0', overview)
            self.make_links_clickable()
            self.sources_tree.delete(*self.sources_tree.get_children())
            self.files_tree.delete(*self.files_tree.get_children())
            self.files_tree.insert('', 'end', text='File mappings are available for authored mod profiles only.')
            self.notebook.select(0)
            return

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

        archive = row.get("archive")
        folder_meta = row.get("folder_meta", {})
        source_archives = display_row.get("source_archives", [])

        if not archive and not folder_meta and not source_archives and not row.get("folder_files"):
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
        embedded_sources = [item for item in source_archives if str(item.get('Hash', '')).startswith(self.INLINE_SOURCE_PREFIX)]
        game_sources = [item for item in source_archives if downloader_type(item.get('State', {}).get('$type')) == 'GameFileSource']
        download_archives = [item for item in source_archives if item not in embedded_sources and item not in game_sources]
        total_archive_size = sum(item.get('Size', 0) for item in download_archives)

        active_label = self.get_activation_label_for_row(row)
        active_label = {'Yes': 'Active', 'No': 'Inactive'}.get(active_label, active_label)
        overview = ' · '.join(str(value) for value in (mod_author, mod_version, active_label) if value) + '\n'
        overview += f"Installation archive: {filename}\n"
        seen_names = {row['name'], filename}
        for label, value in (('Mod folder', row['raw_name']), ('Archive identity', actual_mod_name)):
            if value and value not in seen_names:
                overview += f"{label}: {value}\n"
                seen_names.add(value)
        overview += f"Order: {row['number']}\n"
        if folder_meta.get("repository"):
            overview += f"Repository: {folder_meta['repository']}\n"
        if folder_meta.get("modid"):
            overview += f"Mod ID: {folder_meta['modid']}\n"

        size_mb = total_archive_size / (1024 * 1024) if total_archive_size > 0 else 0
        overview += f"Combined archive download size: {size_mb:.1f} MiB\n\n"

        if mod_description:
            overview += f"Description: {mod_description}\n\n"

        if source_archives:
            direct_links_total = sum(
                1 for source_archive in source_archives if self.build_archive_links(source_archive)["download"]
            )
            contributions = archive_contributions(display_row)
            auxiliary_count = sum(group['auxiliary_empty'] for group in contributions)
            overview += f"Direct Links Available: {direct_links_total}\n"
            preferred_count = sum('preferred_source' in item for item in display_row.get('folder_files', []))
            archive_label = 'Preferred download archives' if preferred_count else 'Download archives used'
            overview += f"{archive_label}: {len(download_archives)}\n"
            verification = self._preferred_cache.get(self.preferred_key(row))
            if verification and verification.get('message'):
                overview += 'Source verification is incomplete; recorded archive mappings are shown.\n'
            if preferred_count:
                overview += f"Verified equivalent files shown under the mod archive: {preferred_count}\n"
                overview += 'Source groups use verified equivalents. File details and exports retain the recorded Wabbajack mapping.\n'
            if embedded_sources:
                overview += f"Embedded package file groups: {len(embedded_sources)}\n"
            if game_sources:
                overview += f"Game file sources used: {len(game_sources)}\n"
            if auxiliary_count:
                overview += f"Archives supplying only empty files: {auxiliary_count}\n"
                overview += ('Wabbajack can select identical empty files from other downloads. '
                             'These archive references do not establish mod dependencies.\n'
                             'Empty-file groups are collapsed in Install Instructions; search expands matching groups.\n')
            if any(group['role'] == 'Recorded extraction source' for group in contributions):
                overview += ('Source titles are recorded metadata. These archive references describe extraction '
                             'locations and do not establish mod dependencies.\n')
            overview += '\n'
            for index, source_archive in enumerate(source_archives, start=1):
                source_state = source_archive.get("State", {})
                links = self.build_archive_links(source_archive)
                source_name = source_state.get("Name") or source_archive.get("Name", "Unknown")
                group = next(item for item in contributions if item['archive'] is source_archive)
                overview += f"Archive {index} (recorded title): {source_name}\n"
                overview += f"Role: {group['role']}\n"
                overview += f"Contribution: {contribution_summary(group)}\n"
                overview += f"Archive filename: {source_archive.get('Name', '')}\n"
                if source_archive.get("Hash"):
                    overview += f"Archive Hash: {source_archive.get('Hash')}\n"
                if source_state.get("$type"):
                    overview += f"Archive State Type: {source_state.get('$type')}\n"
                if source_state.get("Version"):
                    overview += f"Archive Version: {source_state.get('Version')}\n"
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
            overview += f"Known declared file size: {total_size:,} bytes ({total_size / (1024 * 1024):.1f} MiB)\n"
            generated_unknown = sum(file_info.get("directive_type") == "CreateBSA" and not file_info.get("size") for file_info in file_list)
            if generated_unknown:
                overview += f"Generated archives with unknown final size: {generated_unknown}\n"
        else:
            overview += "No file details available for this mod.\n"

        self.overview_text.insert(1.0, overview)
        self.make_links_clickable()

        self.update_sources_tab(row)
        self.update_files_tab(row)
        self.notebook.select(details_tab or 0)

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
                "kind": file_info.get("directive_type", ""),
                "hash": file_info.get("hash", ""),
                "file_info": file_info,
            }

        return tree

    def populate_files_tree(self, file_tree, parent=""):
        """Recursively populate the files treeview."""
        for name, item in file_tree.items():
            if item["type"] == "dir":
                dir_id = self.files_tree.insert(parent, "end", text=f"{name}/", values=("", "", "", "", ""), tags=("directory",))
                self.populate_files_tree(item["children"], dir_id)
            else:
                size_str = f"{item['size']:,}" if item["size"] > 0 else "0"
                if item.get("kind") == "CreateBSA" and not item["size"]:
                    size_str = "Unknown"
                source_mod = item.get("source_mod", "Unknown")
                source_path = item.get("archive_path", "Unknown")
                item_id = self.files_tree.insert(
                    parent,
                    "end",
                    text=name,
                    values=(source_mod, source_path, size_str, item.get("kind", ""), item.get("hash") or "Unknown"),
                    tags=("file",),
                )
                self.row_by_file_item_id[item_id] = item['file_info']

    def update_files_tab(self, row):
        """Update the files tab with organized file tree."""
        row = self.preferred_row(row)
        self.files_tree.delete(*self.files_tree.get_children())
        self.row_by_file_item_id = {}
        self.file_details_button.configure(state=tk.DISABLED)

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
            fallback_source = "Generated archive" if file_info.get("directive_type") == "CreateBSA" else "Unknown"
            enriched["source_mod"] = source_names.get(file_info.get("archive_hash", ""), fallback_source)
            if search_text:
                haystack = " ".join(
                    [
                        str(enriched.get("target_path", "")),
                        str(enriched.get("archive_path", "")),
                        str(enriched.get("source_mod", "")),
                        str(enriched.get("directive_type", "")),
                        str(enriched.get("hash", "")),
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
        if hasattr(self, 'file_columns'):
            self.file_columns.configure('files', data=self.file_columns.tree_data())

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
        """Show recorded instructions by archive, distinguishing empty-only contributions."""
        row = self.preferred_row(row)
        self.sources_tree.delete(*self.sources_tree.get_children())
        self.row_by_source_item_id = {}
        self.source_details_button.configure(state=tk.DISABLED)

        if row.get("is_separator"):
            self.sources_tree.insert("", "end", text="No install instructions for separators.")
            return

        source_groups = archive_contributions(row)
        if not source_groups:
            self.sources_tree.insert("", "end", text="No install instructions available.")
            return

        folder_name = row.get("raw_name", "")
        folder_prefix = f"mods\\{folder_name}\\"
        search_text = self.sources_search_var.get().lower().strip() if hasattr(self, "sources_search_var") else ""
        hide_mohidden = bool(
            hasattr(self, "sources_hide_mohidden_var") and self.sources_hide_mohidden_var.get()
        )
        source_groups.sort(
            key=lambda group: self.get_source_sort_value(group['archive'], group['files'], self.sources_sort_column),
            reverse=self.sources_sort_reverse,
        )
        source_groups.sort(key=lambda group: (group['auxiliary_empty'], not group['primary']))

        for group in source_groups:
            source_archive = group['archive']
            links = self.build_archive_links(source_archive)
            source_name = self.get_source_archive_label(source_archive)
            source_files = self.sort_files_for_column([
                item for item in group['files'] if not (hide_mohidden and self.is_mohidden_file(item))])

            if search_text:
                matched_files = []
                for file_info in source_files:
                    haystack = " ".join(
                        [
                            str(file_info.get("target_path", "")),
                            str(file_info.get("archive_path", "")),
                            str(source_name),
                            str(source_archive.get('Name', '')),
                            group['role'],
                            str(file_info.get("directive_type", "")),
                        ]
                    ).lower()
                    if search_text in haystack:
                        matched_files.append(file_info)
                source_files = matched_files

            if (search_text or hide_mohidden) and not source_files:
                continue

            parent_text = f"{source_name} — {group['role']} ({contribution_summary(group, source_files)})"
            item_id = self.sources_tree.insert("", "end", text=parent_text, values=(source_archive.get('Name', ''), "", "", "", ""))
            self.row_by_source_item_id[item_id] = {"archive": source_archive, "links": links}

            for file_info in source_files:
                target_path = str(file_info.get("target_path", ""))
                if target_path.lower().startswith(folder_prefix.lower()):
                    install_path = target_path[len(folder_prefix):]
                else:
                    install_path = target_path

                file_id = self.sources_tree.insert(
                    item_id,
                    "end",
                    text="",
                    values=(
                        '',
                        install_path,
                        file_info.get("archive_path", ""),
                        "Unknown" if file_info.get("directive_type") == "CreateBSA" and not file_info.get("size") else f"{file_info.get('size', 0):,}",
                        file_info.get("directive_type", ""),
                    ),
                )
                self.row_by_source_item_id[file_id] = {'file_info': file_info, 'archive': source_archive, 'links': links}

            self.sources_tree.item(item_id, open=bool(search_text) or not group['auxiliary_empty'])
        if hasattr(self, 'source_columns'):
            self.source_columns.configure('sources', data=self.source_columns.tree_data())

    def on_source_activate(self, event):
        """Inspect a file, or open the selected archive's source page."""
        if self.sources_tree.identify_region(event.x, event.y) in ('heading', 'separator'):
            return
        selection = self.sources_tree.selection()
        if not selection:
            return

        source_row = self.row_by_source_item_id.get(selection[0])
        if not source_row:
            return
        if source_row.get('file_info'):
            self.show_file_details('sources')
            return

        links = source_row.get("links", {})
        url = links.get("download") or links.get("page")
        if url:
            webbrowser.open(url)



def reset_stored_state():
    """Remove disposable app state without touching user snapshots or profiles."""
    from hash_cache import default_cache_path

    viewer_dir = Path(__file__).parent
    paths = [viewer_dir / name for name in (
        'wabbajack_viewer.config.json',
        'wabbajack_comparison.config.json',
        'wabbajack_copy.config.json',
        'wabbajack_session.wvmodlist.json.gz',
    )]
    cache = default_cache_path()
    paths.extend(Path(str(cache) + suffix) for suffix in ('', '-wal', '-shm', '-journal'))
    for path in paths:
        path.unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description='Browse Wabbajack mod lists and compare installed profiles.')
    parser.add_argument('--clean', action='store_true',
                        help='delete saved session, all app preferences and file-hash cache before launching; '
                             'close other viewer windows first')
    args = parser.parse_args(argv)
    if args.clean:
        try:
            reset_stored_state()
        except OSError as exc:
            parser.exit(1, f'Could not clear stored app state: {exc}\n')
    root = tk.Tk()
    app = WabbajackGuideApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
