"""Shared presentation and activity feedback for the desktop viewer."""
import time
import tkinter as tk
from tkinter import scrolledtext, ttk

COLORS = {'background': '#f5f6f8', 'surface': '#ffffff', 'text': '#202631', 'muted': '#526071',
          'border': '#d9dee5', 'group': '#edf0f4', 'accent': '#245da8', 'selection': '#dceaff',
          'green': '#23643e', 'amber': '#855107', 'red': '#a52c3c', 'blue': '#265b91'}


def configure_style(root):
    style = ttk.Style(root)
    style.theme_use('clam')
    root.configure(background=COLORS['background'])
    style.configure('.', background=COLORS['background'], foreground=COLORS['text'], font='TkDefaultFont')
    style.configure('TLabel', padding=0)
    style.configure('Muted.TLabel', foreground=COLORS['muted'])
    style.configure('Menu.TMenubutton', padding=(8, 5), relief='flat', borderwidth=0,
                    background=COLORS['background'], foreground=COLORS['muted'],
                    arrowcolor=COLORS['muted'], focuscolor=COLORS['accent'])
    style.map('Menu.TMenubutton', background=[('pressed', COLORS['selection']), ('active', COLORS['group'])],
              foreground=[('disabled', '#818b99'), ('active', COLORS['text'])])
    style.configure('TButton', padding=(10, 5), relief='solid', borderwidth=1,
                    background=COLORS['surface'], bordercolor=COLORS['border'],
                    lightcolor=COLORS['surface'], darkcolor=COLORS['surface'], focuscolor=COLORS['accent'])
    style.map('TButton', background=[('disabled', COLORS['group']), ('pressed', COLORS['selection']),
                                    ('active', '#edf3fc')],
              foreground=[('disabled', '#818b99')],
              bordercolor=[('focus', COLORS['accent'])], relief=[('pressed', 'solid')])
    style.configure('Accent.TButton', background=COLORS['accent'], foreground='white',
                    lightcolor=COLORS['accent'], darkcolor=COLORS['accent'])
    style.map('Accent.TButton', background=[('disabled', '#dfe5ec'), ('pressed', '#194880'), ('active', '#1d5093')],
              foreground=[('disabled', '#818b99'), ('!disabled', 'white')])
    for widget in ('TEntry', 'TCombobox'):
        style.configure(widget, fieldbackground=COLORS['surface'], background=COLORS['surface'],
                        foreground=COLORS['text'], padding=4, borderwidth=1,
                        bordercolor=COLORS['border'], lightcolor=COLORS['surface'], darkcolor=COLORS['surface'],
                        arrowcolor=COLORS['muted'], selectbackground=COLORS['selection'], selectforeground=COLORS['text'])
        style.map(widget, fieldbackground=[('disabled', COLORS['group']), ('readonly', COLORS['surface'])],
                  background=[('disabled', COLORS['group']), ('readonly', COLORS['surface'])],
                  foreground=[('disabled', '#818b99'), ('readonly', COLORS['text'])],
                  bordercolor=[('disabled', COLORS['border']), ('invalid', COLORS['red']), ('focus', COLORS['accent'])],
                  lightcolor=[('focus', COLORS['surface'])], darkcolor=[('focus', COLORS['surface'])])
    root.option_add('*TCombobox*Listbox.background', COLORS['surface'])
    root.option_add('*TCombobox*Listbox.foreground', COLORS['text'])
    root.option_add('*TCombobox*Listbox.selectBackground', COLORS['selection'])
    root.option_add('*TCombobox*Listbox.selectForeground', COLORS['text'])
    style.configure('TCheckbutton', background=COLORS['background'], focuscolor=COLORS['accent'])
    style.map('TCheckbutton', background=[('active', COLORS['background'])],
              foreground=[('disabled', '#818b99')])
    style.configure('TLabelframe', bordercolor=COLORS['border'], relief='solid', borderwidth=1)
    style.configure('TLabelframe.Label', foreground=COLORS['muted'])
    style.configure('TNotebook', borderwidth=0, tabmargins=(0, 0, 0, 4),
                    lightcolor=COLORS['background'], darkcolor=COLORS['background'], bordercolor=COLORS['border'])
    style.configure('TNotebook.Tab', padding=(12, 7), background=COLORS['background'],
                    focuscolor=COLORS['accent'])
    style.map('TNotebook.Tab', background=[('selected', COLORS['background']), ('active', COLORS['group'])],
              foreground=[('selected', COLORS['accent']), ('disabled', '#818b99')])
    # Keep native tab keyboard navigation and its focus ring, with a simple underline.
    blank = tk.PhotoImage(master=root, width=1, height=3)
    underline = tk.PhotoImage(master=root, width=1, height=3)
    underline.put(COLORS['accent'], to=(0, 0, 1, 3))
    root._tab_images = blank, underline
    style.element_create('Viewer.tabline', 'image', blank, ('selected', underline), sticky='ew')
    style.layout('TNotebook.Tab', [('Notebook.padding', {'sticky': 'nsew', 'children': [
        ('Viewer.tabline', {'side': 'bottom', 'sticky': 'ew'}),
        ('Notebook.focus', {'side': 'top', 'sticky': 'nsew', 'children': [
            ('Notebook.label', {'sticky': 'nsew'})]})]})])
    style.configure('Treeview', rowheight=25, background=COLORS['surface'],
                    fieldbackground=COLORS['surface'], bordercolor=COLORS['border'], relief='flat')
    style.configure('Treeview.Heading', padding=(6, 6), background=COLORS['group'], relief='flat')
    style.map('Treeview.Heading', background=[('active', COLORS['border'])], relief=[('pressed', 'flat')])
    style.map('Treeview', background=[('selected', COLORS['selection'])],
              foreground=[('selected', COLORS['text'])], bordercolor=[('focus', COLORS['accent'])])
    style.configure('TScrollbar', background=COLORS['group'], troughcolor=COLORS['surface'],
                    bordercolor=COLORS['border'], lightcolor=COLORS['group'], darkcolor=COLORS['group'],
                    arrowcolor=COLORS['muted'], relief='flat', arrowsize=14)
    style.map('TScrollbar', background=[('active', COLORS['border']), ('pressed', COLORS['muted'])])
    style.configure('TPanedwindow', background=COLORS['border'], sashwidth=8)
    style.configure('Horizontal.TProgressbar', background=COLORS['accent'], troughcolor=COLORS['group'])
    return style


def style_text(widget):
    widget.configure(font='TkDefaultFont', background=COLORS['surface'], foreground=COLORS['text'],
                     selectbackground=COLORS['selection'], selectforeground=COLORS['text'],
                     relief='flat', borderwidth=0, padx=10, pady=8, insertbackground=COLORS['text'])
    if hasattr(widget, 'vbar'):
        widget.vbar.configure(background=COLORS['group'], activebackground=COLORS['border'],
                              troughcolor=COLORS['surface'], relief='flat', borderwidth=0,
                              elementborderwidth=0, highlightthickness=0, width=12)


class ReadOnlyText(scrolledtext.ScrolledText):
    """Allow the app to render text while protecting it from accidental edits."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        style_text(self)
        self.configure(state=tk.DISABLED)

    def insert(self, *args):
        self.configure(state=tk.NORMAL)
        try:
            return super().insert(*args)
        finally:
            self.configure(state=tk.DISABLED)

    def delete(self, *args):
        self.configure(state=tk.NORMAL)
        try:
            return super().delete(*args)
        finally:
            self.configure(state=tk.DISABLED)


class AnimatedProgressbar(ttk.Progressbar):
    """Keep animation timers owned by Python so destroying a view cancels them."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.timer = None
        self.bind('<Destroy>', lambda event: self.stop() if event.widget is self else None)

    def start(self, interval=50):
        self.stop()
        def advance():
            self.step(3)
            self.timer = self.after(max(30, interval), advance)
        advance()

    def stop(self):
        if self.timer:
            self.after_cancel(self.timer)
            self.timer = None


class ActivityBanner:
    def __init__(self, parent):
        self.frame = ttk.Frame(parent, padding=(12, 8))
        self.frame.columnconfigure(0, weight=1)
        self.title, self.phase, self.elapsed = tk.StringVar(), tk.StringVar(), tk.StringVar()
        ttk.Label(self.frame, textvariable=self.title, font=('TkDefaultFont', 10, 'bold')).grid(row=0, column=0, sticky='w')
        ttk.Label(self.frame, textvariable=self.elapsed, style='Muted.TLabel').grid(row=0, column=1, padx=12)
        self.cancel_button = ttk.Button(self.frame, text='Cancel', command=self.cancel)
        self.cancel_button.grid(row=0, column=2, rowspan=2, padx=(8, 0))
        self.phase_label = ttk.Label(self.frame, textvariable=self.phase, wraplength=1000, style='Muted.TLabel')
        self.phase_label.grid(row=1, column=0, columnspan=2, sticky='ew', pady=(3, 4))
        self.progress = AnimatedProgressbar(self.frame, mode='indeterminate')
        self.progress.grid(row=2, column=0, columnspan=3, sticky='ew')
        self.frame.bind('<Configure>', lambda event: self.phase_label.configure(wraplength=max(200, event.width - 140)))
        self.owner, self.after_id, self.callback = None, None, None
        self.cancel_button.grid_remove()

    def start(self, owner, title, cancel=None):
        self.finish()
        self.owner, self.callback, self.started = owner, cancel, time.monotonic()
        self.title.set(title)
        self.phase.set('Working in the background…')
        if cancel:
            self.cancel_button.configure(state=tk.NORMAL, text='Cancel')
            self.cancel_button.grid()
        self.frame.grid(row=1, column=0, sticky='ew', pady=(0, 8))
        self.progress.start(20)
        self.tick()

    def tick(self):
        seconds = int(time.monotonic() - self.started)
        self.elapsed.set(f'{seconds // 60}:{seconds % 60:02d} elapsed')
        self.after_id = self.frame.after(250, self.tick)

    def cancel(self):
        if self.callback:
            self.callback()
            self.cancel_button.configure(state=tk.DISABLED, text='Cancelling…')
            self.phase.set('Cancelling safely after the current step…')

    def finish(self, owner=None):
        if owner and owner != self.owner:
            return
        if self.after_id:
            try:
                self.frame.after_cancel(self.after_id)
            except tk.TclError:
                pass
            self.after_id = None
        if self.frame.winfo_exists():
            self.progress.stop()
            self.cancel_button.grid_remove()
            self.frame.grid_remove()
        self.owner = None
