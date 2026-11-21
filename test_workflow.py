"""Integration checks for the shared Wabbajack and unified comparison workflow and stale-result isolation."""
import threading
import time
import tkinter as tk
import unittest
from unittest.mock import patch

import test_profile_compare as fixtures
from package_model import PackageModel
from wabbajack_viewer import WabbajackGuideApp


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(str(error))
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        self.fixture = fixtures.ComparisonTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.temp.cleanup)
        with patch.object(WabbajackGuideApp, 'load_persisted_preferences'), patch.object(WabbajackGuideApp, 'auto_load_last_saved_modlist'):
            self.app = WabbajackGuideApp(self.root)
        self.app.config_path = self.fixture.root / 'viewer-preferences.json'

    def open_package(self, **kwargs):
        path = self.fixture.package(**kwargs)
        with patch('wabbajack_viewer.messagebox.showinfo'), patch('wabbajack_viewer.messagebox.showerror') as errors:
            self.assertTrue(self.app.extract_and_load_modlist(str(path)))
            errors.assert_not_called()
        return path

    def comparison(self):
        view = self.app.comparison_view
        view.preferences_path = self.fixture.root / 'preferences.json'
        view.folder.set(str(self.fixture.profile.parent))
        view.selected_profile.set(self.fixture.profile.name)
        return view

    def wait(self, predicate):
        deadline = time.monotonic() + 10
        while predicate() and time.monotonic() < deadline:
            self.root.update()
            time.sleep(.01)
        self.assertFalse(predicate(), 'UI work did not finish')

    def test_empty_state_and_primary_controls(self):
        labels = [str(w.cget('text')) for w, _ in self.app.header_toolbar_widgets if 'text' in w.keys()]
        self.assertEqual(labels, ['Open Wabbajack…', 'Wabbajack profile:'])
        self.assertEqual([self.app.workspace.tab(tab, 'text') for tab in self.app.workspace.tabs()], ['Mods', 'Create profile copy'])
        self.assertEqual(str(self.app.comparison_view.compare_button['state']), 'disabled')
        self.assertFalse(self.app.cancel_load_button.winfo_manager())
        self.assertIsNone(self.app.comparison_view.model)
        labels = [button.cget('text') for button in self.app.menu_buttons]
        self.assertEqual(labels, ['Export', 'Snapshots', 'Help'])
        help_button = self.app.menu_buttons[-1]
        help_menu = help_button.nametowidget(help_button.cget('menu'))
        with patch('wabbajack_viewer.messagebox.showinfo') as help_dialog:
            help_menu.invoke(0)
        help_text = help_dialog.call_args.args[1]
        self.assertIn('support both Amethyst and Mod Organizer 2 (MO2)', help_text)
        self.assertIn('MO2 profile copying is not supported', help_text)
        self.assertNotIn('Tools →', help_text)

    def test_comparison_reuses_package_and_profile_change_clears_results(self):
        self.open_package(profiles=('One', 'Two'))
        model = self.app.package_model
        view = self.comparison()
        self.assertIs(view.model, model)
        self.assertIsInstance(view.window, __import__('tkinter').ttk.Frame)
        with patch.object(PackageModel, '__init__', side_effect=AssertionError('Package was reparsed')):
            view.start()
            self.wait(lambda: view.running)
        self.assertEqual(view.report['package']['profile'], 'One')
        self.app.profile_var.set('two')
        self.app.on_profile_selected()
        self.wait(lambda: self.app.loading_package)
        self.assertIs(self.app.comparison_view, view)
        self.assertEqual(view.profile.get(), 'Two')
        self.assertIsNone(view.report)
        self.assertEqual(str(view.export_button['state']), 'disabled')
        self.assertIn('Detected mods folder:', view.mods_location.get())

    def test_old_worker_results_cannot_replace_new_context(self):
        self.open_package(profiles=('One', 'Two'))
        view = self.comparison()
        started, release, finished = threading.Event(), threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def blocked(*args, progress):
            started.set()
            release.wait(5)
            try:
                progress('Old package result')
            finally:
                finished.set()
        with patch('comparison_ui.compare_paths', side_effect=blocked):
            view.start()
            self.assertTrue(started.wait(2))
            old_cancel = view.cancelled
            self.app.profile_var.set('two')
            self.app.on_profile_selected()
            self.assertTrue(old_cancel.is_set())
            release.set()
            self.assertTrue(finished.wait(2))
            self.wait(lambda: self.app.loading_package)
            self.root.update()
        self.assertIsNone(view.report)
        self.assertFalse(view.running)
        view.start()
        self.wait(lambda: view.running)
        self.assertEqual(view.report['package']['profile'], 'Two')
        view.mods.set('/changed/mods')
        self.assertIsNone(view.report)
        self.assertEqual(str(view.export_button['state']), 'disabled')

    def test_snapshot_restores_comparison_when_original_package_is_unchanged(self):
        package = self.open_package()
        self.comparison()
        snapshot = self.fixture.root / 'saved.wvmodlist.json.gz'
        with patch('wabbajack_viewer.filedialog.asksaveasfilename', return_value=str(snapshot)), patch('wabbajack_viewer.messagebox.showinfo'), patch.object(self.app, 'remember_saved_modlist_path'):
            self.app.save_modlist(wait=True)
            self.assertTrue(self.app.load_saved_modlist_from_path(snapshot))
        self.assertIsNotNone(self.app.comparison_view.model)
        self.assertEqual([self.app.workspace.tab(tab, 'text') for tab in self.app.workspace.tabs()], ['Mods', 'Create profile copy'])
        self.assertEqual(str(self.app.comparison_view.compare_button['state']), 'normal')
        with patch('wabbajack_viewer.messagebox.showinfo'):
            self.assertTrue(self.app.extract_and_load_modlist(str(package)))
        self.assertEqual(str(self.app.comparison_view.compare_button['state']), 'normal')

    def test_cancel_button_uses_current_job_and_loading_blocks_saved_views(self):
        package = self.open_package()
        view = self.comparison()
        view.set_package_context(self.app.package_model, 'authored')
        view.start()
        view.cancel_button.invoke()
        self.assertTrue(view.cancelled.is_set())
        self.wait(lambda: view.running)
        self.app.start_package_load(str(package))
        self.assertEqual(self.app.saved_views_menu.entrycget(0, 'state'), 'disabled')
        self.app.load_cancel.set()
        self.wait(lambda: self.app.loading_package)
        self.assertEqual(self.app.saved_views_menu.entrycget(0, 'state'), 'normal')
        self.assertFalse(self.app.cancel_load_button.winfo_manager())

    def test_package_without_mod_profile_is_labeled_as_downloads(self):
        self.open_package(profiles=())
        self.assertEqual(self.app.workspace.tab(0, 'text'), 'Downloads')
        self.assertEqual(str(self.app.comparison_view.compare_button['state']), 'disabled')
        self.assertEqual(self.app.export_menu.entrycget(0, 'state'), 'disabled')

    def test_profile_copy_blocks_opening_and_closing_until_job_finishes(self):
        app = self.app
        app.workspace.select(app.workbench_page)
        app.on_workspace_selected()
        view = app.workbench_view
        started, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)

        def operation(_):
            started.set()
            release.wait(5)

        view.run(operation, lambda _: None)
        self.assertTrue(started.wait(2))
        self.assertEqual(str(app.open_button['state']), 'disabled')
        with patch('wabbajack_viewer.messagebox.showwarning') as warning:
            app.request_close()
            warning.assert_called_once()
            self.assertTrue(self.root.winfo_exists())
        with patch('wabbajack_viewer.filedialog.askopenfilename') as picker:
            app.load_wabbajack_file()
            picker.assert_not_called()
        release.set()
        self.wait(lambda: view.busy)
        self.assertEqual(str(app.open_button['state']), 'normal')


if __name__ == '__main__':
    unittest.main()
