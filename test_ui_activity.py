"""Long-running session operations stay responsive and cancel without publishing partial data."""
import gzip
import json
import threading
import time
from unittest.mock import patch
import unittest

import test_workflow as workflow
from wabbajack_viewer import write_snapshot


class ActivityTests(unittest.TestCase):
    setUp = workflow.WorkflowTests.setUp
    open_package = workflow.WorkflowTests.open_package
    wait = workflow.WorkflowTests.wait

    def heartbeat(self):
        ticks = []
        self.root.after(1, lambda: ticks.append(True))
        self.wait(lambda: not ticks)
        return ticks

    def test_snapshot_restore_can_be_cancelled_while_window_keeps_responding(self):
        self.open_package()
        snapshot = self.fixture.root / 'saved.wvmodlist.json.gz'
        write_snapshot(snapshot, self.app.snapshot_payload())
        original_rows, original_model = self.app.mod_rows, self.app.package_model
        started, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        real_open = gzip.open
        def delayed(*args, **kwargs):
            started.set()
            release.wait(5)
            return real_open(*args, **kwargs)
        with patch('wabbajack_viewer.gzip.open', side_effect=delayed):
            self.app.start_snapshot_load(snapshot)
            self.assertTrue(started.wait(2))
            self.heartbeat()
            self.assertEqual(self.app.activity.owner, 'load')
            self.assertEqual(self.app.workspace.winfo_manager(), 'grid')
            self.app.activity.cancel_button.invoke()
            self.assertTrue(self.app.load_cancel.is_set())
            release.set()
            self.wait(lambda: self.app.loading_package)
        self.assertIs(self.app.mod_rows, original_rows)
        self.assertIs(self.app.package_model, original_model)
        self.assertIsNone(self.app.activity.owner)

    def test_initial_restore_hides_empty_workspace_and_reveals_it_on_success(self):
        self.open_package()
        snapshot = self.fixture.root / 'saved.wvmodlist.json.gz'
        write_snapshot(snapshot, self.app.snapshot_payload())
        # Recreate startup state without discarding the fixture snapshot.
        self.app.modlist_data = None
        self.app.last_package_title = 'Example 1'
        started, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        real_open = gzip.open
        def delayed(*args, **kwargs):
            started.set()
            release.wait(5)
            return real_open(*args, **kwargs)
        with patch('wabbajack_viewer.gzip.open', side_effect=delayed):
            self.app.start_snapshot_load(snapshot, automatic=True)
            self.assertTrue(started.wait(2))
            self.heartbeat()
            self.assertFalse(self.app.workspace.winfo_manager())
            self.assertFalse(self.app.header_frame.winfo_manager())
            self.assertFalse(self.app.status_frame.winfo_manager())
            self.assertEqual(self.app.activity.title.get(), 'Restoring Example 1…')
            self.assertEqual(self.app.activity.cancel_button.winfo_manager(), 'grid')
            release.set()
            self.wait(lambda: self.app.loading_package)
        self.assertEqual(self.app.workspace.winfo_manager(), 'grid')
        self.assertEqual(self.app.header_frame.winfo_manager(), 'grid')
        self.assertEqual(self.app.status_frame.winfo_manager(), 'grid')
        self.assertEqual(self.app.modlist_data['Name'], 'Example')

    def test_failed_initial_open_restores_controls(self):
        with patch('wabbajack_viewer.messagebox.showerror'):
            self.app.start_package_load(self.fixture.root / 'missing.wabbajack')
            self.wait(lambda: self.app.loading_package)
        self.assertEqual(self.app.workspace.winfo_manager(), 'grid')
        self.assertEqual(self.app.header_frame.winfo_manager(), 'grid')
        self.assertIsNone(self.app.activity.owner)
        self.assertEqual(str(self.app.open_button['state']), 'normal')

    def test_saves_coalesce_and_close_waits_without_blocking_the_window(self):
        self.open_package()
        self.app.autosave_session(wait=True)
        started, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        saved = []
        def delayed(path, payload):
            if not saved:
                started.set()
                release.wait(5)
            write_snapshot(path, payload)
            saved.append(payload['ui_state']['search'])
        with patch('wabbajack_viewer.write_snapshot', side_effect=delayed), patch.object(self.root, 'destroy') as close:
            self.app.autosave_session()
            self.assertTrue(started.wait(2))
            self.assertEqual(self.app.activity.owner, 'save')
            self.app.search_var.set('Alpha')
            self.app.autosave_session()
            self.app.search_var.set('Beta')
            self.app.request_close()
            self.heartbeat()
            close.assert_not_called()
            # The previous complete file remains readable while a new save is pending.
            with gzip.open(self.app.session_snapshot_path) as stream:
                self.assertEqual(json.load(stream)['ui_state']['search'], '')
            release.set()
            self.wait(lambda: not close.called)
            close.assert_called_once()
        self.assertEqual(saved, ['', 'Beta'])
        with gzip.open(self.app.session_snapshot_path) as stream:
            self.assertEqual(json.load(stream)['ui_state']['search'], 'Beta')

    def test_profile_switch_is_cancellable_and_retains_the_previous_profile(self):
        self.open_package(profiles=('One', 'Two'))
        original_rows = self.app.mod_rows
        started, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def delayed(stage, *args, **kwargs):
            started.set()
            release.wait(5)
            stage.set_status('Reading the other profile')
        with patch('wabbajack_viewer.WabbajackGuideApp.load_profile_inline_data', delayed):
            self.app.profile_var.set('Two')
            self.app.on_profile_selected()
            self.assertTrue(started.wait(2))
            self.heartbeat()
            self.app.activity.cancel_button.invoke()
            release.set()
            self.wait(lambda: self.app.loading_package)
        self.assertEqual(self.app.selected_profile_name(), 'One')
        self.assertEqual(self.app.profile_var.get(), 'One')
        self.assertIs(self.app.mod_rows, original_rows)

    def test_requested_snapshot_is_preserved_while_autosaves_coalesce(self):
        self.open_package()
        started, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        calls = []
        def delayed(path, payload):
            if not calls:
                started.set()
                release.wait(5)
            write_snapshot(path, payload)
            calls.append(str(path))
        destination = self.fixture.root / 'named.wvmodlist.json.gz'
        with patch('wabbajack_viewer.write_snapshot', side_effect=delayed), \
             patch('wabbajack_viewer.filedialog.asksaveasfilename', return_value=str(destination)):
            self.app.autosave_session()
            self.assertTrue(started.wait(2))
            self.app.search_var.set('Alpha')
            self.app.save_modlist()
            self.app.search_var.set('Beta')
            self.app.autosave_session()
            self.heartbeat()
            release.set()
            self.wait(lambda: self.app._save_thread is not None)
        with gzip.open(destination) as stream:
            self.assertEqual(json.load(stream)['ui_state']['search'], 'Alpha')
        with gzip.open(self.app.session_snapshot_path) as stream:
            self.assertEqual(json.load(stream)['ui_state']['search'], 'Beta')
        self.assertEqual(self.app.last_saved_modlist_path, str(destination))
        self.assertEqual(len(calls), 3)


if __name__ == '__main__':
    unittest.main()
