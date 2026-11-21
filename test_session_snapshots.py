import gzip
import json
import tkinter as tk
import time
import unittest
from unittest.mock import patch

import profile_compare
import test_workflow as workflow
import test_preferred_sources as preferred
from viewer_snapshots import write_snapshot
from wabbajack_viewer import WabbajackGuideApp


class SessionSnapshotTests(unittest.TestCase):
    setUp = workflow.WorkflowTests.setUp
    open_package = workflow.WorkflowTests.open_package

    def wait_for(self, app, predicate):
        deadline = time.monotonic() + 10
        while predicate() and time.monotonic() < deadline:
            app.root.update()
            time.sleep(.01)
        self.assertFalse(predicate(), 'Background session work did not finish')

    def restore(self, app):
        app.auto_load_last_saved_modlist()
        self.wait_for(app, lambda: getattr(app, 'loading_package', False))

    def fresh_app(self):
        root = tk.Tk()
        root.withdraw()
        self.addCleanup(root.destroy)
        with patch.object(WabbajackGuideApp, 'load_persisted_preferences'), patch.object(WabbajackGuideApp, 'auto_load_last_saved_modlist'):
            app = WabbajackGuideApp(root)
        app.config_path = self.app.config_path
        app.downloads_folder = self.app.downloads_folder
        return app

    def test_automatic_restore_keeps_verified_sources_without_reading_archives(self):
        path = preferred.PreferredInterfaceTests.nonempty_package(self)
        with patch('wabbajack_viewer.messagebox.showinfo'):
            self.assertTrue(self.app.extract_and_load_modlist(str(path)))
        self.app.autosave_session(wait=True)
        restored = self.fresh_app()
        with patch('package_model.zipfile.ZipFile', side_effect=AssertionError('Original archive was reopened')), \
             patch('wabbajack_viewer.verify_preferred_sources', side_effect=AssertionError('Sources were rescanned')):
            self.restore(restored)
            self.assertIsNotNone(restored.comparison_view.model)
            inventory = profile_compare.read_package(restored.package_model)
            self.assertEqual(set(inventory['mods']), {'alpha', 'beta'})
        item = next(item for item, row in restored.row_by_item_id.items() if row['raw_name'] == 'Alpha')
        self.assertEqual(restored.tree.set(item, 'sources'), '1')
        self.assertFalse(restored._preferred_pending)

    def test_close_saves_profile_selection_and_browsing_state(self):
        self.open_package(profiles=('One', 'Two'))
        self.app.profile_var.set('Two')
        self.app.on_profile_selected()
        self.wait_for(self.app, lambda: getattr(self.app, 'loading_package', False))
        self.app.search_var.set('Beta')
        item = next(iter(self.app.row_by_item_id))
        self.app.tree.selection_set(item)
        self.app.on_mod_select(None)
        self.app.notebook.select(next(tab for tab in self.app.notebook.tabs()
                                      if self.app.notebook.tab(tab, 'text') == 'Output Files'))
        with patch.object(self.root, 'destroy') as close:
            self.app.request_close()
            self.wait_for(self.app, lambda: not close.called)
        close.assert_called_once()
        restored = self.fresh_app()
        self.restore(restored)
        self.assertEqual(restored.selected_profile_name(), 'Two')
        self.assertEqual(restored.search_var.get(), 'Beta')
        self.assertEqual(restored.current_selected_row['raw_name'], 'Beta')
        restored.root.update()
        self.assertEqual(restored.notebook.tab(restored.notebook.select(), 'text'), 'Output Files')
        self.assertEqual(str(restored.profile_combobox['state']), 'readonly')
        restored.profile_var.set('One')
        restored.on_profile_selected()
        self.wait_for(restored, lambda: getattr(restored, 'loading_package', False))
        self.assertEqual(restored.selected_profile_name(), 'One')
        self.assertTrue(all(row['folder_files'] for row in restored.mod_rows))

    def test_changed_or_missing_original_preserves_browsing_and_disables_comparison(self):
        path = self.open_package()
        self.app.autosave_session(wait=True)
        path.write_bytes(b'changed original package')
        restored = self.fresh_app()
        self.restore(restored)
        self.assertTrue(restored.loaded_from_saved_modlist)
        self.assertEqual(len(restored.mod_rows), 2)
        self.assertIsNone(restored.comparison_view.model)
        self.assertIn('missing or has changed', restored.source_var.get())
        path.unlink()
        restored.autosave_session(wait=True)
        again = self.fresh_app()
        self.restore(again)
        self.assertEqual(len(again.mod_rows), 2)

    def test_restore_can_be_disabled_and_corrupt_autosave_is_nonmodal(self):
        self.open_package()
        self.app.autosave_session(wait=True)
        restored = self.fresh_app()
        restored.restore_session_var.set(False)
        self.restore(restored)
        self.assertEqual(restored.mod_rows, [])
        restored.restore_session_var.set(True)
        restored.session_snapshot_path.write_bytes(b'incomplete file')
        with patch('wabbajack_viewer.messagebox.showerror') as error:
            self.restore(restored)
        error.assert_not_called()
        self.assertEqual(restored.mod_rows, [])

    def test_legacy_snapshots_still_open_without_original_package(self):
        path = self.open_package()
        payload = self.app.snapshot_payload()
        payload['snapshot_version'] = 1
        payload.pop('model_cache')
        write_snapshot(self.app.session_snapshot_path, payload)
        path.unlink()
        restored = self.fresh_app()
        self.restore(restored)
        self.assertTrue(restored.loaded_from_saved_modlist)
        self.assertEqual(len(restored.mod_rows), 2)

    def test_interrupted_snapshot_write_keeps_the_last_good_file(self):
        path = self.fixture.root / 'atomic.wvmodlist.json.gz'
        write_snapshot(path, {'previous': True})
        with patch('viewer_snapshots.json.JSONEncoder.iterencode', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                write_snapshot(path, {'next': True})
        with gzip.open(path) as stream:
            self.assertEqual(json.load(stream), {'previous': True})
        self.assertEqual(list(path.parent.glob(path.name + '.*.tmp')), [])


if __name__ == '__main__':
    unittest.main()
