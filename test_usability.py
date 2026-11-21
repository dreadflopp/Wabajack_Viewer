"""User-visible column stability, source selection and conditional root copying."""
import json
from pathlib import Path
import time
import tkinter as tk
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import clone_amethyst_profile as cloner
from profile_copy_ui import ProfileCopyView
from table_columns import TableColumns
import test_clone_amethyst_profile as clones
import test_workflow as workflow
from wabbajack_viewer import WabbajackGuideApp


class TableUsabilityTests(unittest.TestCase):
    setUp = workflow.WorkflowTests.setUp
    open_package = workflow.WorkflowTests.open_package
    comparison = workflow.WorkflowTests.comparison
    wait = workflow.WorkflowTests.wait

    def compare(self):
        self.open_package()
        view = self.comparison()
        view.start()
        self.wait(lambda: view.running)
        return view

    def test_user_width_survives_window_resize_filters_and_recomparison(self):
        view = self.compare()
        app = self.app
        app.config_path = self.fixture.root / 'viewer-config.json'
        self.root.deiconify()
        self.root.geometry('1300x850')
        self.root.update()
        app.tree.column('name', width=375)
        app.tree.column('local_name', width=295)
        app.mod_columns.dragging = True  # The widths already changed during the separator drag.
        app.mod_columns.release(SimpleNamespace())
        self.root.geometry('850x720')
        self.root.update()
        app.comparison_filter.set('Installed only')
        app.search_var.set('Personal')
        view.start()
        self.wait(lambda: view.running)
        self.assertEqual(app.tree.column('name', 'width'), 375)
        self.assertEqual(app.tree.column('local_name', 'width'), 295)
        self.assertTrue(all(not app.tree.column(column, 'stretch') for column in app.mod_columns.visible_columns()))
        # Persisted choices are also available after restarting.
        restored = WabbajackGuideApp.__new__(WabbajackGuideApp)
        restored.config_path, restored.column_widths = app.config_path, {}
        restored.load_persisted_preferences()
        self.assertEqual(restored.column_widths['comparison']['name'], 375)
        view.clear_button.invoke()
        self.assertEqual(app.tree.column('name', 'width'), 375)
        view.start()
        self.wait(lambda: view.running)
        self.assertEqual(app.tree.column('name', 'width'), 375)

    def test_default_widths_fit_headings_and_empty_columns_stay_compact(self):
        self.open_package()
        app = self.app
        app.configure_mod_columns()
        self.assertLess(app.tree.column('author', 'width'), 100)
        self.assertEqual(app.tree.column('version', 'width'), app.mod_columns.content_width('version'))
        app.mod_columns.fit()
        self.assertGreaterEqual(app.tree.column('name', 'width'), app.mod_columns.content_width('name'))
        self.assertFalse(app.sources_tree.column('#0', 'stretch'))
        self.assertFalse(app.files_tree.column('#0', 'stretch'))
        app.mod_columns.data = [{'name': 'A long mod name ' * 30}]
        app.mod_columns.fit('name')
        self.assertGreater(app.tree.column('name', 'width'), app.mod_columns.cap)

    def test_default_name_expands_but_manual_width_remains_fixed(self):
        self.open_package()
        app = self.app
        self.root.deiconify()
        self.root.geometry('1100x920')
        self.root.update()
        initial = app.tree.column('name', 'width')
        self.root.geometry('1350x920')
        self.root.update()
        self.assertGreater(app.tree.column('name', 'width'), initial)
        self.assertEqual(str(app.tree.heading('name', 'anchor')), 'w')
        self.assertEqual(str(app.tree.heading('author', 'anchor')), 'w')
        app.mod_columns.fit('name')
        width = app.tree.column('name', 'width')
        self.root.geometry('1450x920')
        self.root.update()
        self.assertEqual(app.tree.column('name', 'width'), width)

    def test_details_toggle_preserves_selection_tab_split_and_preferences(self):
        self.open_package()
        app = self.app
        self.root.deiconify()
        self.root.geometry('1300x920')
        self.root.update()
        item = next(item for item, row in app.row_by_item_id.items() if row['name'] == 'Alpha')
        app.tree.selection_set(item)
        self.root.update()
        app.notebook.select(2)
        self.root.update()
        tab, split = app.notebook.select(), app.details_split
        self.assertEqual(app.details_title.get(), 'Alpha')
        app.details_toggle.invoke()
        self.root.update()
        self.assertEqual(len(app.mod_details_panes.panes()), 1)
        self.assertEqual(app.tree.selection(), (item,))
        self.assertTrue(json.loads(app.config_path.read_text())['details_hidden'])
        app.details_toggle.invoke()
        self.root.update()
        self.assertEqual(len(app.mod_details_panes.panes()), 2)
        self.assertEqual(app.notebook.select(), tab)
        self.assertEqual(app.details_split, split)
        self.assertAlmostEqual(app.mod_details_panes.sashpos(0) / app.mod_details_panes.winfo_height(), split, delta=.1)
        self.assertFalse(json.loads(app.config_path.read_text())['details_hidden'])
        self.assertEqual(app.title_label.cget('text'), 'Example 1')
        self.assertFalse(app.source_label.winfo_manager())
        self.assertTrue(app.list_count_var.get().endswith(' mods'))
        self.assertFalse(app.clear_search_button.winfo_manager())
        app.search_var.set('Alpha')
        self.assertEqual(app.clear_search_button.winfo_manager(), 'grid')
        app.clear_search_button.invoke()
        self.assertFalse(app.clear_search_button.winfo_manager())

    def test_toolbar_wraps_and_scrollbar_only_appears_for_overflow(self):
        self.open_package()
        app = self.app
        self.root.deiconify()
        self.root.geometry('1450x920')
        self.root.update()
        self.assertEqual(app.comparison_view.window.grid_info()['row'], 0)
        self.assertFalse(app.mod_horizontal.winfo_manager())
        self.assertEqual(app.open_button.cget('style'), 'TButton')
        self.assertGreater(app.open_button.winfo_rootx(), app.title_label.winfo_rootx())
        self.root.geometry('760x680')
        self.root.update()
        self.assertEqual(app.comparison_view.window.grid_info()['row'], 1)
        self.assertLessEqual(app.comparison_view.window.winfo_width(), app.mods_toolbar.winfo_width())
        app.tree.column('name', width=1500, stretch=False)
        self.root.update()
        self.assertEqual(app.mod_horizontal.winfo_manager(), 'grid')
        app.mod_columns.fit('name')
        self.root.geometry('1450x920')
        self.root.update()
        self.assertFalse(app.mod_horizontal.winfo_manager())
        app.details_toggle.invoke()
        self.root.update()
        self.assertTrue(app.show_details_button.winfo_viewable())
        app.show_details_button.invoke()
        self.root.update()
        self.assertTrue(app.details_toggle.winfo_viewable())
        self.assertFalse(app.show_details_button.winfo_manager())
        app.mod_columns.reset()
        self.assertTrue(app.tree.column('name', 'stretch'))
        self.assertFalse(app.column_widths.get('mod_common', {}).get('name'))

    def test_both_active_columns_use_yes_no_and_missing_is_a_dash(self):
        self.compare()
        rows = {r['raw_name']: item for item, r in self.app.row_by_item_id.items()}
        def values(name):
            return dict(zip(self.app.tree['columns'], self.app.tree.item(rows[name], 'values')))
        self.assertEqual((values('Alpha')['active'], values('Alpha')['local_active']), ('Yes', 'Yes'))
        self.assertEqual((values('Beta')['active'], values('Beta')['local_active']), ('No', '—'))
        self.assertEqual((values('Personal')['active'], values('Personal')['local_active']), ('—', 'No'))

    def test_mod_location_override_is_explicit_and_resets_with_source(self):
        view = self.compare()
        self.assertIn('Detected mods folder:', view.mods_location.get())
        with patch('comparison_ui.filedialog.askdirectory', return_value=str(self.fixture.mods)):
            view.choose_mods()
        self.assertIsNone(view.report)
        self.assertIn('Chosen mods folder:', view.mods_location.get())
        self.assertEqual(view.reset_mods_button.winfo_manager(), 'grid')
        view.reset_mods_button.invoke()
        self.assertIn('Detected mods folder:', view.mods_location.get())
        second = self.fixture.profile.parent / 'Second'
        second.mkdir()
        (second / 'modlist.txt').write_text('')
        view.mods.set(str(self.fixture.mods))
        view.refresh_profiles()
        view.selected_profile.set('Second')
        self.assertEqual(view.mods.get(), '')
        self.assertFalse(view.reset_mods_button.winfo_manager())


class CopyUsabilityTests(unittest.TestCase):
    def setUp(self):
        self.fixture = clones.CloneTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.no_app = patch.object(cloner, 'running_amethyst', return_value=[])
        self.no_app.start()
        self.addCleanup(self.no_app.stop)
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(str(error))
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        f = self.fixture
        self.view = ProfileCopyView(self.root, folder=str(f.source.parent), selected='Original')
        self.view.preferences_path = f.root / 'copy-preferences.json'
        self.view.config_dir.set(str(f.config))
        self.view.name.set('Copy')

    def wait(self):
        end = time.monotonic() + 10
        while self.view.busy and time.monotonic() < end:
            self.root.update()
            time.sleep(.01)
        self.assertFalse(self.view.busy)

    def use_shared_game(self):
        path = self.fixture.source / 'profile_state.json'
        state = json.loads(path.read_text())
        state['profile_settings'].pop('game_path')
        path.write_text(json.dumps(state))
        self.view.source_changed()

    def test_copy_steps_preserve_fields_and_require_a_new_preview_after_back(self):
        view = self.view
        self.assertEqual(view.step, 'setup')
        self.assertFalse(view.copy_text.frame.winfo_manager())
        self.assertFalse(view.create_button.winfo_manager())
        view.copy_saves.set(False)
        view.copy_external.set(True)
        before = tuple(variable.get() for variable in (view.folder, view.source, view.name, view.game_root,
                                                        view.copy_saves, view.copy_external))
        view.preview_copy()
        self.wait()
        self.assertEqual(view.step, 'review')
        self.assertFalse(view.form_view.winfo_manager())
        self.assertEqual(view.copy_text.frame.winfo_manager(), 'grid')
        self.assertEqual(str(view.create_button['state']), 'normal')
        view.back_button.invoke()
        self.assertEqual(view.step, 'setup')
        self.assertIsNone(view.preview)
        self.assertEqual(before, tuple(variable.get() for variable in (view.folder, view.source, view.name,
                                      view.game_root, view.copy_saves, view.copy_external)))
        self.assertEqual(view.destination.get(), str(Path(view.folder.get()) / view.name.get()))
        self.assertIn('Source root folder:', view.source_info_text.get('1.0', tk.END))
        view.preview_copy()
        self.wait()
        self.assertEqual(view.step, 'review')
        view.name.set('Different copy')
        self.assertEqual(view.step, 'setup')
        self.assertIsNone(view.preview)

    def test_review_rechecks_source_before_creating(self):
        view, fixture = self.view, self.fixture
        view.preview_copy()
        self.wait()
        self.assertEqual(view.step, 'review')
        (fixture.source / 'mods/One Mod/new-file.txt').write_text('Changed after preview')
        with patch.object(view, 'fail') as error:
            view.create_copy()
            self.wait()
            error.assert_called_once()
            self.assertIn('Preview again', str(error.call_args.args[0]))
        self.assertFalse((fixture.source.parent / 'Copy').exists())
        self.assertEqual(str(view.create_button['state']), 'disabled')
        view.back_button.invoke()
        self.assertEqual(view.step, 'setup')

    def test_shared_game_hides_root_and_copies_only_profile_files(self):
        self.use_shared_game()
        view, f = self.view, self.fixture
        self.assertFalse(view.root_fields.winfo_manager())
        self.assertFalse(view.game_root.get())
        self.assertIsNone(view.clone_args().game_root)
        game_files = {p.relative_to(f.game): p.read_bytes() for p in f.game.rglob('*') if p.is_file()}
        with patch.object(view, 'fail') as error:
            view.preview_copy()
            self.wait()
            error.assert_not_called()
            self.assertFalse(view.preview[1]['copies_game_root'])
            self.assertIn('no game copy', view.copy_text.get('1.0', tk.END))
            view.create_copy()
            self.wait()
            error.assert_not_called()
        destination = f.staging / 'profiles/Copy'
        self.assertTrue(destination.is_dir())
        self.assertFalse((f.staging.parent / 'Copy Game Root').exists())
        settings = cloner.read_state(destination)['profile_settings']
        self.assertNotIn('game_path', settings)
        self.assertNotEqual((destination / 'mods/One Mod/test.esp').stat().st_ino,
                            (f.source / 'mods/One Mod/test.esp').stat().st_ino)
        self.assertEqual(game_files, {p.relative_to(f.game): p.read_bytes() for p in f.game.rglob('*') if p.is_file()})

    def test_root_destination_is_conditional_and_names_are_rejected_immediately(self):
        view, f = self.view, self.fixture
        self.assertEqual(view.root_fields.winfo_manager(), 'grid')
        self.assertEqual(str(view.preview_button['state']), 'normal')
        view.name.set('ORIGINAL')
        self.assertEqual(str(view.preview_button['state']), 'disabled')
        self.assertIn('already exists', view.name_error.get())
        view.name.set('Copy')
        target = f.staging / 'profiles/Copy'
        target.symlink_to(f.root / 'missing')
        view.validate_inputs()
        self.assertEqual(str(view.preview_button['state']), 'disabled')
        self.assertIn('already exists', view.name_error.get())
        self.assertTrue(target.is_symlink())

    def test_copy_has_no_technical_path_or_copy_mode_controls(self):
        def texts(parent):
            result = []
            for child in parent.winfo_children():
                if 'text' in child.keys():
                    result.append(str(child.cget('text')))
                result += texts(child)
            return result
        labels = '\n'.join(texts(self.view.window))
        for unwanted in ('Source game override', 'Amethyst config folder', 'Original Windows roots', 'Copy mode'):
            self.assertNotIn(unwanted, labels)
        self.assertEqual(self.view.clone_args().copy_mode, 'auto')
        self.view.name.set('-Personal')
        self.assertEqual(self.view.clone_args().name, '-Personal')

    def test_empty_profile_without_a_mod_store_can_be_copied_without_a_game_copy(self):
        f = self.fixture
        (f.staging / 'mods').rmdir()
        empty = f.source.parent / 'Empty'
        empty.mkdir()
        (empty / 'profile_state.json').write_text('{}')
        (empty / 'modlist.txt').write_text('')
        self.view.refresh_profiles()
        self.view.source.set('Empty')
        args = self.view.clone_args()
        plan = cloner.build_plan(args)
        self.assertIsNone(plan.game)
        result = cloner.apply_plan(plan, args, progress=lambda _: None)
        self.assertIsNone(result['game_root'])
        self.assertTrue((plan.destination / 'mods').is_dir())
        self.assertEqual((plan.destination / 'modlist.txt').read_text(), '')

    def test_late_case_collision_rolls_back_new_root_and_preserves_existing_profile(self):
        f = self.fixture
        args = self.view.clone_args()
        plan = cloner.build_plan(args)
        real_publish = cloner.rename_new
        collision = f.source.parent / 'cOpY'
        def publish(source, destination):
            if destination == plan.game:
                collision.mkdir()
                (collision / 'important.txt').write_text('keep')
            real_publish(source, destination)
        with patch.object(cloner, 'rename_new', side_effect=publish):
            with self.assertRaisesRegex(cloner.CloneError, 'already exists'):
                cloner.apply_plan(plan, args, progress=lambda _: None)
        self.assertEqual((collision / 'important.txt').read_text(), 'keep')
        self.assertFalse(plan.game.exists())
        self.assertFalse(plan.destination.exists())
        self.assertFalse(list(f.source.parent.glob('.profile-clone-*')))


if __name__ == '__main__':
    unittest.main()
