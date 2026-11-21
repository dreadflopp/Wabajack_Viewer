"""Stable Treeview widths, with explicit content fitting and saved manual choices."""
import tkinter as tk
from tkinter import font, ttk


class TableColumns:
    def __init__(self, tree, widths, save=lambda: None, cap=420):
        self.tree, self.widths, self.save, self.cap = tree, widths, save, cap
        self.mode, self.dragging = None, False
        self.data = []
        self.columns = ('#0', *tree['columns'])
        for column in self.columns:
            tree.column(column, stretch=False, minwidth=32)
        tree.bind('<ButtonPress-1>', self.press, add='+')
        tree.bind('<ButtonRelease-1>', self.release, add='+')
        tree.bind('<Double-1>', self.double_click, add='+')
        tree.bind('<Button-3>', self.menu, add='+')

    def configure(self, mode, columns=None, data=None):
        self.mode = mode
        if columns is not None:
            self.tree.configure(displaycolumns=columns)
        if data is not None:
            self.data = data
        saved = self.widths.get(mode, {})
        shared = self.widths.get('mod_common', {}) if mode in ('mods', 'comparison') else {}
        for column in self.visible_columns():
            value = shared.get(column, saved.get(column))
            if not isinstance(value, int) or not 32 <= value <= 4000:
                value = self.content_width(column)
            self.tree.column(column, width=value, stretch=(column == 'name' and mode in ('mods', 'comparison')
                                                       and column not in shared and column not in saved))

    def visible_columns(self):
        columns = self.tree['displaycolumns']
        return ('#0', *(self.tree['columns'] if columns == ('#all',) else columns))

    def content_width(self, column, limited=True):
        heading = self.tree.heading(column, 'text').replace(' ▴', '').replace(' ▾', '')
        heading_font = font.Font(font=ttk.Style(self.tree).lookup('Treeview.Heading', 'font') or 'TkDefaultFont')
        body_font = font.Font(font=ttk.Style(self.tree).lookup('Treeview', 'font') or 'TkDefaultFont')
        values = sorted({str(row.get(column, '')) for row in self.data} - {'', '-', '—'}, key=len, reverse=True)[:30]
        body_width = max((body_font.measure(value) for value in values), default=0)
        return max(40, heading_font.measure(heading) + 24, min((min(self.cap, 180) if column == 'author' else self.cap) if limited else 4000, body_width + 24))

    def fit(self, column=None):
        # Explicit fitting also covers sources/files, whose values live in the tree.
        if not self.data:
            self.data = self.tree_data()
        for key in (column,) if column else self.visible_columns():
            width = self.content_width(key, limited=False)
            self.tree.column(key, width=width, stretch=False)
            self.widths.setdefault(self.mode, {})[key] = width
            self.remember_common(key, width)
        self.save()

    def reset(self):
        """Restore content defaults and automatic name expansion for this view."""
        self.widths.pop(self.mode, None)
        shared = self.widths.get('mod_common', {}) if self.mode in ('mods', 'comparison') else {}
        for column in self.visible_columns():
            shared.pop(column, None)
        self.configure(self.mode)
        self.save()

    def tree_data(self):
        rows = []
        def visit(parent=''):
            for item in self.tree.get_children(parent):
                rows.append(dict(zip(self.columns, (self.tree.item(item, 'text'), *self.tree.item(item, 'values')))))
                visit(item)
        visit()
        return rows

    def press(self, event):
        self.dragging = self.tree.identify_region(event.x, event.y) == 'separator'

    def release(self, _event):
        if self.dragging:
            self.widths[self.mode] = {column: self.tree.column(column, 'width') for column in self.visible_columns()}
            for column, width in self.widths[self.mode].items():
                self.remember_common(column, width)
            # A manual name width takes precedence over automatic expansion.
            if self.mode in ('mods', 'comparison'):
                self.tree.column('name', stretch=False)
            self.save()
        self.dragging = False

    def remember_common(self, column, width):
        if self.mode in ('mods', 'comparison') and column in ('#0', 'name', 'active', 'version'):
            self.widths.setdefault('mod_common', {})[column] = width

    def double_click(self, event):
        if self.tree.identify_region(event.x, event.y) == 'separator':
            self.fit(self.visible_columns()[int(self.tree.identify_column(event.x)[1:])])
            return 'break'

    def menu(self, event):
        if self.tree.identify_region(event.x, event.y) in ('heading', 'separator'):
            menu = tk.Menu(self.tree, tearoff=False)
            menu.add_command(label='Fit columns to contents', command=self.fit)
            menu.add_command(label='Reset column widths', command=self.reset)
            try:
                menu.tk_popup(event.x_root, event.y_root)
            finally:
                menu.grab_release()
            return 'break'
