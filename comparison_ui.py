"""Tk interface for direct package-to-installed comparisons."""
import json
from pathlib import Path
import queue
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk

from ui_components import AnimatedProgressbar
from profile_compare import ComparisonError, compare_paths, enabled_label, write_report


def format_comparison_details(row):
    lines = [f"Result: {row['status'].replace('Package only', 'Wabbajack only').replace('Local only', 'Installed only')}", f"Wabbajack mod: {row['package_name'] or 'Absent from selected Wabbajack profile'}",
             f"Installed mod: {row['local_name'] or 'No matched local entry'}",
             f"Wabbajack version: {row['package_version'] or 'Unknown'}", f"Installed version: {row['local_version'] or 'Unknown'}",
             f"Enabled in Wabbajack: {enabled_label(row['package_enabled'])}", f"Enabled locally: {enabled_label(row['local_enabled'])}"]
    if row["local_name"]:
        lines.append("Installed folder: " + (row.get('local_folder', '') if row["local_present"] else "Missing"))
    lines.append(f"Load-order position — Wabbajack: {row.get('package_order') or '—'}; installed: {row.get('local_order') or '—'}")
    if row["match_basis"]:
        lines.append("Matched by: " + row["match_basis"])
    if row["candidates"]:
        lines.append("Possible matches (manual review): " + ", ".join(row["candidates"]))
    lines.append("Priority order differs: " + enabled_label(row["order_differs"]))
    labels = {"mod_id": "Nexus mod ID", "file_id": "Nexus file ID", "game": "Game", "repository": "Repository",
              "installation_file": "Installation archive", "version": "Version"}
    lines.append("")
    for field in ("game", "mod_id", "file_id", "installation_file"):
        lines.append(f"{labels[field]} — Wabbajack: {row['package_meta'].get(field) or 'Unknown'}; installed: {row['local_meta'].get(field) or 'Unknown'}")
    if row["differences"]:
        lines.append("\nRecorded differences:")
        for item in row["differences"]:
            lines.append(f"• {labels.get(item['field'], item['field'])}: Wabbajack {item['package']} → installed {item['local']}")
    if row["unknown_metadata"]:
        lines.append("\nIncomplete metadata: " + ", ".join(labels.get(field, field) for field in row["unknown_metadata"]))
    content = row["content"]
    lines.append("\nContent verification: " + content["status"])
    if "checked" in content:
        lines.append(f"Hashed: {content['checked']}; expected files: {content['expected']}; unverified: {content['unverifiable']}")
    for item in content.get("differences", []):
        lines.append(f"• {item['path']}: {item['status']}" + (" — " + item["reason"] if item.get("reason") else ""))
        if item.get("package_hash"):
            lines.append(f"  Wabbajack hash: {item['package_hash']}; installed hash: {item['local_hash']}")
    if row["links"]:
        lines.append("\nSources:\n" + "\n".join(row["links"]))
    return "\n".join(lines)


class ComparisonJob:
    def set_package_context(self, model, profile, package=''):
        self.cancelled.set()
        if self.poll_id:
            self.window.after_cancel(self.poll_id)
            self.poll_id = None
        self.events = queue.Queue()
        self.cancelled = threading.Event()
        self.running = False
        self.model = model
        self.package.set(str(model.path) if model else package)
        self.profile.set(profile)
        self.set_inputs_enabled(True)
        self.clear_report('Open a Wabbajack to compare installed mods.' if not model and not package else
                          'Ready to compare.' if self.local.get() else 'Choose an installed profile to compare.')

    def start(self):
        if self.running:
            return
        if not self.package.get().strip() or not self.local.get().strip():
            messagebox.showwarning("Select profile", "Open a Wabbajack at the top of the app, then choose your installed profile folder.", parent=self.window.winfo_toplevel())
            return
        args = (self.model or self.package.get().strip(), self.local.get().strip(), self.profile.get().strip(),
                self.mods.get().strip() or None, self.instance.get().strip() or None, self.verify.get())
        self.running = True
        self.clear_report('Comparing installed mods…')
        self.set_inputs_enabled(False)
        self.status.set("Reading selected sources…")
        self.cancelled.clear()
        self.cancel_button.config(state=tk.NORMAL)
        events, cancelled = self.events, self.cancelled
        try:
            self.preferences_path.write_text(json.dumps({name: getattr(self, name).get() for name in ('local', 'mods', 'instance')}), encoding='utf-8')
        except OSError:
            pass

        def worker():
            last = 0

            def progress(message):
                nonlocal last
                if cancelled.is_set():
                    raise ComparisonError("Comparison cancelled.")
                now = time.monotonic()
                if now - last > 0.2:
                    last = now
                    events.put(("progress", message))
            try:
                result = compare_paths(*args, progress=progress)
                events.put(("result", result))
            except Exception as error:
                events.put(("error", str(error)))
        threading.Thread(target=worker, daemon=True).start()
        self.poll_id = self.window.after(100, self.poll)

    def export(self):
        if not self.report:
            return
        path = filedialog.asksaveasfilename(parent=self.window, defaultextension=".html", initialfile="mod_comparison.html",
                                          filetypes=[("HTML report", "*.html"), ("JSON report", "*.json")])
        if path:
            try:
                write_report(self.report, Path(path))
            except OSError as error:
                messagebox.showerror("Export failed", str(error), parent=self.window)


class InstalledComparisonControls(ComparisonJob):
    """Source selection and background comparison; results live in the main Mods table."""
    def __init__(self, parent, on_report, can_start=lambda: True):
        from profile_locations import default_profiles_folder
        self.window = ttk.Frame(parent, padding=(0, 4))
        self.on_report, self.can_start = on_report, can_start
        self.model = None
        self.events, self.cancelled = queue.Queue(), threading.Event()
        self.running, self.poll_id, self.report = False, None, None
        self.blocked = False
        self.preferences_path = Path(__file__).with_name('wabbajack_comparison.config.json')
        try:
            preferences = json.loads(self.preferences_path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            preferences = {}
        if not isinstance(preferences, dict):
            preferences = {}
        self.package, self.profile = tk.StringVar(), tk.StringVar()
        self.local = tk.StringVar()
        self.mods = tk.StringVar()
        self.instance = tk.StringVar()
        self.verify = tk.BooleanVar(value=False)
        self.folder = tk.StringVar()
        self.selected_profile = tk.StringVar()
        self.input_widgets = []
        frame = self.window
        frame.columnconfigure(0, weight=1)
        actions = ttk.Frame(frame)
        actions.grid(row=0, column=0, sticky='ew')
        self.settings_visible = False
        self.settings_button = ttk.Button(actions, text='Settings…', command=lambda: self.set_settings_visible(True))
        self.settings_button.pack(side=tk.RIGHT)
        self.settings_window = tk.Toplevel(parent)
        self.settings_window.title('Installed profile settings')
        self.settings_window.withdraw()
        self.settings_window.geometry('780x260')
        self.settings_window.minsize(650, 250)
        self.settings_window.transient(parent.winfo_toplevel())
        self.settings_window.protocol('WM_DELETE_WINDOW', lambda: self.set_settings_visible(False))
        self.settings_window.bind('<Escape>', lambda event: self.set_settings_visible(False))
        self.settings_body = ttk.Frame(self.settings_window, padding=12)
        body = self.settings_body
        body.columnconfigure(1, weight=1)
        self.settings_body.pack(fill=tk.BOTH, expand=True)
        ttk.Label(body, text='Select an Amethyst or Mod Organizer 2 profiles folder.', style='Muted.TLabel').grid(row=0, column=0, columnspan=3, sticky='w', pady=(0, 10))
        ttk.Label(body, text='Profiles folder:').grid(row=1, column=0, sticky='w', padx=(0, 6))
        entry = ttk.Entry(body, textvariable=self.folder)
        entry.grid(row=1, column=1, sticky='ew')
        choose = ttk.Button(body, text='Browse…', command=self.choose_profiles)
        choose.grid(row=1, column=2, padx=(6, 0))
        self.input_widgets += [entry, choose]
        ttk.Label(actions, text='Installed profile:').pack(side=tk.LEFT, padx=(0, 5))
        self.profile_box = ttk.Combobox(actions, textvariable=self.selected_profile, state='readonly', width=20)
        self.profile_box.pack(side=tk.LEFT, padx=(0, 6))
        self.compare_button = ttk.Button(actions, text='Compare', style='Accent.TButton', command=self.start)
        self.compare_button.pack(side=tk.LEFT, padx=(0, 5))
        self.cancel_button = ttk.Button(actions, text='Cancel', command=self.cancel)
        self.clear_button = ttk.Button(actions, text='Clear comparison', command=lambda: self.clear_report('Comparison cleared.'))
        self.clear_button.pack(side=tk.LEFT, padx=(0, 5))
        options = ttk.Frame(body)
        options.grid(row=3, column=0, columnspan=3, sticky='ew', pady=(6, 0))
        self.verify_check = ttk.Checkbutton(options, text='Check mod file contents (slower)', variable=self.verify)
        self.verify_check.pack(side=tk.LEFT)
        ttk.Button(body, text='Done', command=lambda: self.set_settings_visible(False)).grid(row=4, column=2, sticky='e', pady=(12, 0))
        self.report_actions = ttk.Frame(frame)
        self.report_actions.grid(row=2, column=0, sticky='ew', pady=(4, 0))
        self.export_button = ttk.Button(self.report_actions, text='Export report…', command=self.export)
        self.export_button.pack(side=tk.RIGHT)
        self.profile_files_button = ttk.Button(self.report_actions, text='Profile file differences…', command=self.show_profile_files)
        self.profile_files_button.pack(side=tk.RIGHT, padx=(0, 5))
        locations = ttk.Frame(body)
        locations.grid(row=2, column=0, columnspan=3, sticky='ew', pady=(4, 0))
        locations.columnconfigure(0, weight=1)
        self.mods_location = tk.StringVar()
        self.mods_label = ttk.Label(locations, textvariable=self.mods_location, wraplength=850)
        self.mods_label.grid(row=0, column=0, sticky='ew')
        self.mods_button = ttk.Button(locations, text='Choose mods folder…', command=self.choose_mods)
        self.mods_button.grid(row=0, column=1, padx=(6, 0))
        self.reset_mods_button = ttk.Button(locations, text='Use detected folder', command=lambda: self.mods.set(''))
        self.reset_mods_button.grid(row=0, column=2, padx=(6, 0))
        self.reset_mods_button.grid_remove()
        self.input_widgets += [self.mods_button, self.reset_mods_button]
        self.status = tk.StringVar(value='Open a Wabbajack and choose an installed profile.')
        label = ttk.Label(frame, textvariable=self.status, wraplength=1100, style='Muted.TLabel')
        label.grid(row=3, column=0, sticky='ew', pady=(5, 0))
        self.status.trace_add('write', lambda *_: label.grid_remove() if self.status.get() == 'Ready to compare.' else label.grid())
        def resize_labels(event):
            label.configure(wraplength=max(200, event.width - 20))
        frame.bind('<Configure>', resize_labels)
        body.bind('<Configure>', lambda event: self.mods_label.configure(wraplength=max(180, event.width - 350)))
        self.progress_bar = AnimatedProgressbar(frame, mode='indeterminate')
        self.progress_bar.grid(row=4, column=0, sticky='ew', pady=4)
        self.progress_bar.grid_remove()
        self._progress_spinning = False
        for variable in (self.local, self.mods, self.instance, self.verify):
            variable.trace_add('write', self.inputs_changed)
        self.mods.trace_add('write', self.refresh_mods_location)
        self.folder.trace_add('write', self.refresh_profiles)
        self.selected_profile.trace_add('write', self.select_profile)
        saved = preferences.get('local', '')
        if saved:
            self.folder.set(str(Path(saved).parent))
            if Path(saved).name in self.profile_box['values']:
                self.selected_profile.set(Path(saved).name)
        else:
            self.folder.set(default_profiles_folder())
        if preferences.get('local') == self.local.get():
            self.mods.set(preferences.get('mods', ''))
        self.set_package_context(None, '')
        self.set_settings_visible(False)

    def set_settings_visible(self, visible):
        self.settings_visible = visible
        if visible:
            self.settings_window.deiconify()
            self.settings_window.lift()
        else:
            self.settings_window.withdraw()

    def choose_profiles(self):
        value = filedialog.askdirectory(parent=self.window.winfo_toplevel(), title='Choose Amethyst or MO2 profiles folder',
                                        initialdir=self.folder.get() or None)
        if value:
            from profile_locations import profiles_folder
            folder, selected = profiles_folder(value)
            self.folder.set(str(folder))
            if selected:
                self.selected_profile.set(selected)

    def refresh_profiles(self, *_):
        from profile_locations import profile_names
        try:
            names = profile_names(self.folder.get()) if self.folder.get() else []
            self.profile_box['values'] = names
            if self.selected_profile.get() not in names:
                self.selected_profile.set(names[0] if names else '')
            self.select_profile()
        except OSError as error:
            self.profile_box['values'] = ()
            self.selected_profile.set('')
            self.status.set(str(error))

    def select_profile(self, *_):
        name = self.selected_profile.get()
        selected = str(Path(self.folder.get()).expanduser() / name) if name else ''
        if selected != self.local.get():
            self.mods.set('')
            self.instance.set('')
        self.local.set(selected)
        self.refresh_mods_location()
        self.set_inputs_enabled(not self.running)

    def choose_mods(self):
        value = filedialog.askdirectory(parent=self.window.winfo_toplevel(), title='Choose the folder containing installed mod folders',
            initialdir=self.mods.get() or None)
        if value:
            self.mods.set(value)

    def refresh_mods_location(self, *_):
        from profile_compare import resolve_mods_dir
        self.reset_mods_button.grid() if self.mods.get() else self.reset_mods_button.grid_remove()
        try:
            if not self.local.get():
                self.mods_location.set('Select a profile to locate its mods.')
                return
            root = resolve_mods_dir(Path(self.local.get()), self.mods.get() or None)
            prefix = 'Chosen mods folder: ' if self.mods.get() else 'Detected mods folder: '
            self.mods_location.set(prefix + str(root) + ('' if root.is_dir() else ' (not found — choose its location)'))
        except (OSError, ValueError, ComparisonError) as error:
            self.mods_location.set('Mods folder could not be detected: ' + str(error))

    def set_inputs_enabled(self, enabled):
        enabled = enabled and not self.blocked
        for widget in self.input_widgets:
            widget.configure(state=tk.NORMAL if enabled else tk.DISABLED)
        self.profile_box.configure(state='readonly' if enabled else tk.DISABLED)
        self.verify_check.configure(state=tk.NORMAL if enabled else tk.DISABLED)
        self.compare_button.configure(state=tk.NORMAL if enabled and self.model and self.profile.get() and self.local.get() else tk.DISABLED)
        self.clear_button.configure(state=tk.NORMAL if enabled and self.report else tk.DISABLED)
        self.export_button.configure(state=tk.NORMAL if enabled and self.report else tk.DISABLED)
        self.profile_files_button.configure(state=tk.NORMAL if enabled and self.report else tk.DISABLED)
        self.cancel_button.configure(state=tk.NORMAL if self.running and not self.cancelled.is_set() else tk.DISABLED)
        self.report_actions.grid() if self.report else self.report_actions.grid_remove()
        if self.report:
            self.clear_button.pack(side=tk.LEFT, padx=(0, 5))
        else:
            self.clear_button.pack_forget()
        if self.running and not self._progress_spinning:
            self.progress_bar.grid()
            self.progress_bar.start(15)
            self._progress_spinning = True
        elif not self.running and self._progress_spinning:
            self.progress_bar.stop()
            self.progress_bar.grid_remove()
            self._progress_spinning = False
        if not self.running:
            self.cancel_button.pack_forget()
        else:
            self.cancel_button.pack(side=tk.LEFT, after=self.compare_button, padx=(0, 5))

    def cancel(self):
        if self.running:
            self.cancelled.set()
            self.cancel_button.configure(state=tk.DISABLED)
            self.status.set('Cancelling comparison…')

    def set_blocked(self, blocked):
        self.blocked = blocked
        self.set_inputs_enabled(not self.running)

    def clear_report(self, message):
        self.report = None
        self.on_report(None)
        self.set_inputs_enabled(not self.running)
        self.status.set(message)

    def inputs_changed(self, *_):
        if self.running:
            # Also isolates jobs if a programmatic profile selection changes mid-read.
            self.set_package_context(self.model, self.profile.get())
        elif self.report is not None:
            self.clear_report('Profile or options changed. Compare again to refresh the results.')

    def start(self):
        if self.can_start() and self.model and self.profile.get() and self.local.get():
            super().start()

    def poll(self):
        self.poll_id = None
        while not self.events.empty():
            kind, value = self.events.get_nowait()
            if kind == 'progress':
                self.status.set(value)
                continue
            self.running = False
            if self.cancelled.is_set():
                self.status.set('Comparison cancelled.')
            elif kind == 'error':
                self.status.set('Comparison failed: ' + value)
                messagebox.showerror('Comparison failed', value, parent=self.window.winfo_toplevel())
            else:
                self.report = value
                self.status.set(f"File contents checked: {value['verification_stats']['bytes_read'] / 1024**2:,.0f} MiB read · {value['verification_stats']['cached_files']:,} cached hashes reused. Generated files may remain unverified." if value['file_verification'] else
                                'Metadata, mod order and enabled states compared. File contents not checked.')
                self.on_report(value)
            self.set_inputs_enabled(True)
        if self.running:
            self.poll_id = self.window.after(100, self.poll)

    def show_profile_files(self):
        if not self.report:
            return
        dialog = tk.Toplevel(self.window)
        dialog.title('Profile files — Wabbajack → installed')
        dialog.geometry('1000x700')
        ttk.Label(dialog, text='Active mod order, plugins and INI configuration. Backups and logs are excluded. Remapped/generated files remain unverified. Preview limit: 512 KiB.', wraplength=950).pack(fill=tk.X, padx=10, pady=8)
        panes = ttk.PanedWindow(dialog, orient=tk.VERTICAL)
        panes.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))
        table = ttk.Treeview(panes, columns=('status',), show='tree headings', height=7)
        table.heading('#0', text='Profile file')
        table.heading('status', text='Result')
        table.column('#0', width=600)
        text = scrolledtext.ScrolledText(panes, wrap=tk.NONE, state=tk.DISABLED)
        panes.add(table, weight=1)
        panes.add(text, weight=3)
        text.tag_configure('added', foreground='#1f7a1f')
        text.tag_configure('removed', foreground='#b22222')
        rows = self.report.get('profile_files', [])
        for index, row in enumerate(rows):
            table.insert('', 'end', iid=str(index), text=row['path'], values=(row['status'],))
        def selected(_=None):
            items = table.selection()
            if not items:
                return
            row = rows[int(items[0])]
            text.configure(state=tk.NORMAL)
            text.delete('1.0', tk.END)
            text.insert(tk.END, row['status'] + '\n' + row['local_path'] + '\n\n')
            for line in row['diff'].splitlines(keepends=True):
                text.insert(tk.END, line, 'added' if line.startswith('+') else 'removed' if line.startswith('-') else '')
            text.configure(state=tk.DISABLED)
        table.bind('<<TreeviewSelect>>', selected)
        if rows:
            table.selection_set('0')
            selected()
