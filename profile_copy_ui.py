"""Simple installed Amethyst profile selection and preview-first independent copying."""
import json
from pathlib import Path
import queue
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk

import clone_amethyst_profile as cloner
from profile_locations import amethyst_config_folder, default_profiles_folder, profile_names, profiles_folder
from ui_components import AnimatedProgressbar, ReadOnlyText, COLORS, style_text


class ProfileCopyView:
    def __init__(self, parent, *, folder='', selected='', busy_changed=lambda _: None, can_start=lambda: True, copied=lambda: None):
        self.window = ttk.Frame(parent, padding=10)
        self.busy_changed, self.can_start, self.copied = busy_changed, can_start, copied
        self.busy, self.poll_id, self.preview = False, None, None
        self.events, self.controls = queue.Queue(), []
        self.preferences_path = Path(__file__).with_name('wabbajack_copy.config.json')
        try:
            saved = json.loads(self.preferences_path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            saved = {}
        if not isinstance(saved, dict):
            saved = {}
        self.folder, self.source, self.name = tk.StringVar(), tk.StringVar(), tk.StringVar()
        self.game_root = tk.StringVar()
        self.config_dir = tk.StringVar(value=amethyst_config_folder())
        self.uses_root, self.source_error = False, ''
        self.copy_saves, self.copy_external = tk.BooleanVar(value=True), tk.BooleanVar(value=False)
        self._suggested_game = ''
        outer = self.window
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(1, weight=1)
        self.step_title = tk.StringVar(value='Set up copy')
        ttk.Label(outer, textvariable=self.step_title, font=('TkDefaultFont', 12, 'bold')).grid(
            row=0, column=0, sticky='w', pady=(0, 8))
        # Setup and review share the available space; actions stay below both.
        form_view = ttk.Frame(outer, padding=8)
        self.form_view = form_view
        form_view.grid(row=1, column=0, sticky='nsew')
        canvas = tk.Canvas(form_view, height=340, highlightthickness=0,
                           background=ttk.Style(parent).lookup('TFrame', 'background'))
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar = ttk.Scrollbar(form_view, orient=tk.VERTICAL, command=canvas.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        def update_scroll(first, last):
            scrollbar.set(first, last)
            if float(first) <= 0 and float(last) >= 1:
                scrollbar.pack_forget()
            else:
                scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        canvas.configure(yscrollcommand=update_scroll)
        form = ttk.Frame(canvas)
        form.columnconfigure(1, weight=1)
        item = canvas.create_window(0, 0, window=form, anchor='nw')
        form.bind('<Configure>', lambda _: canvas.configure(scrollregion=canvas.bbox('all')))
        canvas.bind('<Configure>', lambda event: canvas.itemconfigure(item, width=event.width))
        intro = ttk.Label(form, text='Create an independent copy of an installed Amethyst profile. Review the preview before creating it. Linux only; MO2 profile copying is not supported.', wraplength=1050)
        intro.grid(row=0, column=0, columnspan=3, sticky='ew', pady=(0, 6))
        self.field(form, 1, 'Amethyst profiles folder', self.folder, self.choose_profiles)
        ttk.Label(form, text='Source profile').grid(row=2, column=0, sticky='w', padx=(0, 8), pady=3)
        self.source_box = ttk.Combobox(form, textvariable=self.source, state='readonly')
        self.source_box.grid(row=2, column=1, sticky='ew', pady=3)
        self.controls.append(self.source_box)
        self.source_info = tk.StringVar()
        info = ReadOnlyText(form, wrap=tk.WORD, height=4, width=1)
        self.source_info_text = info
        info.configure(foreground=COLORS['muted'])
        info.grid(row=3, column=0, columnspan=3, sticky='ew', pady=(0, 8))
        def update_source_info(*_):
            info.delete('1.0', tk.END)
            info.insert('1.0', self.source_info.get())
        self.source_info.trace_add('write', update_source_info)
        self.name_entry = self.field(form, 4, 'New profile name', self.name)
        self.destination = tk.StringVar()
        ttk.Label(form, text='New profile folder', style='Muted.TLabel').grid(row=5, column=0, sticky='w', padx=(0, 8))
        destination_entry = ttk.Entry(form, textvariable=self.destination, state='readonly')
        destination_entry.grid(row=5, column=1, columnspan=2, sticky='ew', pady=3)
        self.controls.append(destination_entry)
        self.name_error = tk.StringVar()
        error_label = ttk.Label(form, textvariable=self.name_error, foreground=COLORS['red'], wraplength=1000)
        error_label.grid(row=6, column=0, columnspan=3, sticky='ew')
        self.root_fields = ttk.Frame(form)
        self.root_fields.columnconfigure(1, weight=1)
        self.root_fields.grid(row=7, column=0, columnspan=3, sticky='ew')
        self.field(self.root_fields, 0, 'New game installation copy', self.game_root, self.choose_game_parent)
        root_help = ttk.Label(self.root_fields, text='Copies the mod list’s game installation, including the game executable and Data folder. It may use a downgraded Skyrim version. Choose a destination folder that does not exist yet.', wraplength=1000)
        root_help.grid(row=1, column=0, columnspan=3, sticky='ew')
        self.root_fields.bind('<Configure>', lambda event: root_help.configure(wraplength=max(180, event.width - 12)))
        self.root_fields.grid_remove()
        options = ttk.Frame(form)
        options.grid(row=8, column=0, columnspan=3, sticky='w', pady=6)
        for label, variable in (('Copy saves', self.copy_saves), ('Also copy external custom tool folders', self.copy_external)):
            check = ttk.Checkbutton(options, text=label, variable=variable)
            check.pack(side=tk.LEFT, padx=(0, 16))
            self.controls.append(check)
        actions = ttk.Frame(outer, padding=8)
        actions.grid(row=2, column=0, sticky='ew', pady=6)
        self.preview_button = ttk.Button(actions, text='Preview copy', style='Accent.TButton', command=self.preview_copy)
        self.preview_button.pack(side=tk.LEFT, padx=(0, 6))
        self.back_button = ttk.Button(actions, text='Back', command=self.back_to_setup)
        self.create_button = ttk.Button(actions, text='Create standalone profile', style='Accent.TButton', command=self.create_copy, state=tk.DISABLED)
        self.create_button.pack(side=tk.LEFT)
        self.controls.extend([self.preview_button, self.back_button, self.create_button])
        self.copy_text = scrolledtext.ScrolledText(outer, wrap=tk.WORD, state=tk.DISABLED, height=12)
        style_text(self.copy_text)
        self.copy_text.grid(row=1, column=0, sticky='nsew')
        self.show_step('setup')
        self.status = tk.StringVar(value='Choose a source profile and a unique new name, then preview the copy.')
        status = ttk.Label(outer, textvariable=self.status, wraplength=1050)
        status.grid(row=3, column=0, sticky='ew', pady=(6, 0))
        self.progress_bar = AnimatedProgressbar(outer, mode='indeterminate')
        self.progress_bar.grid(row=4, column=0, sticky='ew', pady=(6, 0))
        self.progress_bar.grid_remove()
        def scroll_form(event):
            direction = (-1 if event.num == 4 else 1) if event.num in (4, 5) else (-1 if event.delta > 0 else 1)
            canvas.yview_scroll(direction, 'units')
            return 'break'
        def bind_scroll(widget):
            if not isinstance(widget, (ttk.Entry, ttk.Combobox)):
                for event in ('<MouseWheel>', '<Button-4>', '<Button-5>'):
                    widget.bind(event, scroll_form, add='+')
            for child in widget.winfo_children():
                bind_scroll(child)
        bind_scroll(form_view)
        def resize(event):
            for label in (intro, status, error_label):
                label.configure(wraplength=max(200, event.width - 55))

        self.window.bind('<Configure>', resize)
        self.folder.trace_add('write', self.refresh_profiles)
        self.source.trace_add('write', self.source_changed)
        self.name.trace_add('write', self.suggest_game)
        self.config_dir.trace_add('write', self.source_changed)
        for variable in (self.folder, self.source, self.name, self.game_root, self.config_dir, self.copy_saves, self.copy_external):
            variable.trace_add('write', self.invalidate_preview)
        self.folder.set(folder or saved.get('folder') or default_profiles_folder())
        initial = selected or saved.get('source', '')
        if initial in self.source_box['values']:
            self.source.set(initial)

    def show_step(self, step):
        self.step = step
        self.step_title.set('Set up copy' if step == 'setup' else 'Review copy')
        if step == 'setup':
            self.copy_text.grid_remove()
            self.form_view.grid()
            self.back_button.pack_forget()
            self.create_button.pack_forget()
            self.preview_button.pack(side=tk.RIGHT)
        else:
            self.form_view.grid_remove()
            self.copy_text.grid()
            self.preview_button.pack_forget()
            self.back_button.pack(side=tk.LEFT)
            self.create_button.pack(side=tk.RIGHT)

    def back_to_setup(self):
        if not self.busy:
            self.invalidate_preview()
            self.status.set('Adjust the settings, then preview the copy again.')

    def field(self, parent, row, title, variable, browse=None):
        ttk.Label(parent, text=title).grid(row=row, column=0, sticky='w', padx=(0, 8), pady=3)
        entry = ttk.Entry(parent, textvariable=variable)
        entry.grid(row=row, column=1, sticky='ew', pady=3)
        self.controls.append(entry)
        if browse:
            button = ttk.Button(parent, text='Choose parent…' if variable is self.game_root else 'Choose…', command=browse)
            button.grid(row=row, column=2, padx=(6, 0), pady=3)
            self.controls.append(button)
        return entry

    def choose_profiles(self):
        value = filedialog.askdirectory(parent=self.window, title='Choose Amethyst profiles folder', initialdir=self.folder.get() or None)
        if value:
            folder, selected = profiles_folder(value)
            self.folder.set(str(folder))
            if selected:
                self.source.set(selected)

    def choose_game_parent(self):
        value = filedialog.askdirectory(parent=self.window, title='Choose parent folder for the new root folder',
                                        initialdir=str(Path(self.game_root.get()).parent) if self.game_root.get() else None)
        if value:
            self.game_root.set(str(Path(value) / (self.name.get().strip() + ' Game Root')))

    def refresh_profiles(self, *_):
        try:
            names = profile_names(self.folder.get()) if self.folder.get() else []
            self.source_box['values'] = names
            if self.source.get() not in names:
                self.source.set(names[0] if names else '')
            self.source_changed()
            self.suggest_game()
        except OSError as error:
            self.source_box['values'] = ()
            self.source.set('')
            self.source_info.set(str(error))

    def source_changed(self, *_):
        self.source_error, self.uses_root = '', False
        if not self.source.get():
            self.source_info.set('No profiles containing modlist.txt found in this folder.')
            self.source_error = 'Choose a source profile.'
        else:
            try:
                source = Path(self.folder.get()).expanduser() / self.source.get()
                detected = cloner.discover_profile(source, self.config_dir.get())
                settings = detected['state']['profile_settings']
                self.uses_root = detected['private_root']
                wj = settings.get('wabbajack_directory', '')
                lines = ['Wabbajack installation: ' + wj] if wj else ['Ordinary Amethyst profile']
                if self.uses_root:
                    lines.append('Source root folder: ' + str(detected['game']))
                    if not detected['game'].is_dir():
                        self.source_error = 'The saved root folder is missing. Correct its path in Amethyst before copying.'
                else:
                    lines.append('Uses Amethyst’s shared game folder; no game copy is needed.')
                self.source_info.set('\n'.join(lines))
            except (OSError, cloner.CloneError) as error:
                self.source_error = str(error)
                self.source_info.set(self.source_error)
        self.root_fields.grid() if self.uses_root else self.root_fields.grid_remove()
        self.suggest_game()
        self.validate_inputs()

    def suggest_game(self, *_):
        self.destination.set(str(Path(self.folder.get()).expanduser() / self.name.get().strip())
                             if self.folder.get() and self.name.get().strip() else '')
        current = self.game_root.get()
        if not self.uses_root:
            self._suggested_game = ''
            if current:
                self.game_root.set('')
        elif not current or current == self._suggested_game:
            name = self.name.get().strip()
            self._suggested_game = str(Path(self.folder.get()).expanduser().parent.parent / (name + ' Game Root')) if name and self.folder.get() else ''
            if self.game_root.get() != self._suggested_game:
                self.game_root.set(self._suggested_game)

    def validate_inputs(self):
        error = self.source_error
        if not error:
            try:
                cloner.validate_destination_name(Path(self.folder.get()).expanduser(), self.name.get())
                if self.uses_root:
                    target = Path(self.game_root.get()).expanduser()
                    if not self.game_root.get() or not target.is_absolute():
                        raise cloner.CloneError('Choose an absolute path for the new root folder.')
                    if target.exists() or target.is_symlink():
                        raise cloner.CloneError('The new root folder already exists. Choose a new location.')
            except (OSError, cloner.CloneError) as problem:
                error = str(problem)
        self.name_error.set(error if self.name.get() or self.source_error else '')
        self.name_entry.state(['invalid'] if error and self.name.get() else ['!invalid'])
        self.preview_button.configure(state=tk.NORMAL if not error and not self.busy else tk.DISABLED)
        return error

    def clone_args(self):
        error = self.validate_inputs()
        if error:
            raise cloner.CloneError(error)
        source = Path(self.folder.get()).expanduser().absolute() / self.source.get()
        if not self.source.get() or self.source.get() not in self.source_box['values']:
            raise cloner.CloneError('Choose an installed source profile.')
        values = ['--source', str(source), '--staging', str(source.parent.parent), '--name=' + self.name.get(),
                  '--config-dir', self.config_dir.get(), '--copy-mode', 'auto']
        if self.uses_root:
            values += ['--game-root', self.game_root.get()]
        if not self.copy_saves.get():
            values += ['--no-saves']
        if self.copy_external.get():
            values += ['--copy-external-tools']
        return cloner.parser().parse_args(values)

    def invalidate_preview(self, *_):
        self.show_step('setup')
        self.preview = None
        self.create_button.configure(state=tk.DISABLED)
        self.text('Preview the copy to review its destination folders, disk usage and any warnings.')
        self.validate_inputs()

    def text(self, value):
        self.copy_text.configure(state=tk.NORMAL)
        self.copy_text.delete('1.0', tk.END)
        self.copy_text.insert('1.0', value)
        self.copy_text.configure(state=tk.DISABLED)

    def fail(self, error):
        self.status.set(str(error))
        messagebox.showerror('Profile copy failed', str(error), parent=self.window)

    def run(self, operation, done):
        if self.busy:
            return
        if not self.can_start():
            self.status.set('Wait for Wabbajack loading or installed comparison to finish.')
            return
        self.busy = True
        self.progress_bar.grid()
        self.progress_bar.start(20)
        self.busy_changed(True)
        states = [(widget, widget.cget('state')) for widget in self.controls]
        for widget, _ in states:
            widget.configure(state=tk.DISABLED)
        self.status.set('Working…')
        events = self.events
        def worker():
            last = 0
            def progress(message):
                nonlocal last
                if time.monotonic() - last > .15:
                    last = time.monotonic()
                    events.put(('progress', message))
            try:
                events.put(('done', operation(progress)))
            except Exception as error:
                events.put(('error', str(error)))
        threading.Thread(target=worker, daemon=False).start()
        def poll():
            self.poll_id = None
            while not events.empty():
                kind, value = events.get_nowait()
                if kind == 'progress':
                    self.status.set(value)
                    continue
                self.busy = False
                self.progress_bar.stop()
                self.progress_bar.grid_remove()
                for widget, state in states:
                    widget.configure(state=state)
                try:
                    if kind == 'error':
                        self.fail(value)
                    else:
                        done(value)
                finally:
                    self.create_button.configure(state=tk.NORMAL if self.preview else tk.DISABLED)
                    self.validate_inputs()
                    self.busy_changed(False)
                return
            self.poll_id = self.window.after(100, poll)
        self.poll_id = self.window.after(100, poll)

    def preview_copy(self):
        try:
            args = self.clone_args()
        except Exception as error:
            return self.fail(error)
        self.invalidate_preview()
        def done(preview):
            self.preview = args, preview
            lines = [f"Source profile: {preview['source_profile']}", f"New profile: {preview['destination_profile']}"]
            if preview['copies_game_root']:
                lines += [f"Source game installation: {preview['source_game']}", f"New game-root copy: {preview['destination_game']}"]
            else:
                lines.append('Game folder: keeps Amethyst’s shared game setting; no game copy.')
            lines += [f"Files: {preview['files_to_copy']:,} · Logical size: {preview['logical_GiB']:.2f} GiB", '',
                'Copied files are independent. Paths are adjusted automatically. Efficient copies are used when supported.',
                'The original Wabbajack installation link is removed from the new profile.',
                'Close Amethyst and restore/undeploy the source before creating the copy.', '', *preview['warnings']]
            self.text('\n'.join(lines))
            self.show_step('review')
            self.status.set('Preview ready. Review the destinations, then Create standalone profile.')
        self.run(lambda _: cloner.build_plan(args).preview(), done)

    def create_copy(self):
        if not self.preview:
            return
        args, preview = self.preview
        self.preview = None
        def operation(progress):
            plan = cloner.build_plan(args)
            if plan.preview() != preview:
                raise cloner.CloneError('The source or destinations changed. Preview again before copying.')
            return cloner.apply_plan(plan, args, progress)
        def done(result):
            self.text(f"Profile created: {result['profile']}\n" + (f"Root folder: {result['game_root']}\n" if result['game_root'] else "Game: uses the shared Amethyst setting\n") + f"Files copied: {result['copied_files']:,}\n\n" + result['next_step'])
            self.step_title.set('Copy complete')
            self.status.set('Standalone profile created. It remains undeployed.')
            try:
                self.preferences_path.write_text(json.dumps({'folder': self.folder.get(), 'source': self.source.get()}), encoding='utf-8')
            except OSError:
                pass
            self.refresh_profiles()
            self.copied()
        self.run(operation, done)
