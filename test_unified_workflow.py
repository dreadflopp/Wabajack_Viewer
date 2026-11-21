"""Behavior checks for the main comparison and simple Amethyst copy workflow."""
import copy
import json
from pathlib import Path
import time
import tkinter as tk
import unittest
from unittest.mock import patch

import clone_amethyst_profile as cloner
from comparison_presenter import category, display_rows, matches_filter
from profile_compare import compare_paths, match_records, read_local_profile
from profile_copy_ui import ProfileCopyView
import test_clone_amethyst_profile as clone_fixtures
import test_profile_compare as fixtures
import test_workflow as workflow


class UnifiedComparisonTests(unittest.TestCase):
    setUp = workflow.WorkflowTests.setUp
    open_package = workflow.WorkflowTests.open_package
    comparison = workflow.WorkflowTests.comparison
    wait = workflow.WorkflowTests.wait

    def test_unified_table_colors_both_sides_filters_and_folder_access(self):
        self.open_package()
        view = self.comparison()
        original = copy.deepcopy(self.app.mod_rows)
        view.start()
        self.wait(lambda: view.running)
        self.assertEqual(len(self.app.workspace.tabs()), 2)
        projected = list(self.app.row_by_item_id.values())
        self.assertEqual(len(projected), 3)
        by_name = {r['raw_name']: r for r in projected}
        self.assertEqual(by_name['Alpha']['compare_status'], 'green')
        self.assertEqual(by_name['Beta']['compare_status'], 'red')
        self.assertEqual(by_name['Personal']['compare_status'], 'external_only')
        self.assertEqual(self.app.mod_rows, original)
        self.assertIn('local_version', self.app.tree['displaycolumns'])
        self.assertIn('local_order', self.app.tree['displaycolumns'])
        self.app.comparison_filter.set('Installed only')
        items = self.app.tree.get_children()
        self.assertEqual(len(items), 1)
        self.app.tree.selection_set(items[0])
        self.app.on_mod_select(None)
        self.assertIn('Personal', self.app.comparison_detail_text.get('1.0', tk.END))
        with patch('wabbajack_viewer.webbrowser.open') as browser:
            self.app.open_installed_mod_folder()
            browser.assert_called_once_with((self.fixture.mods / 'Personal').as_uri())
        self.app.copy_tree_selection()
        self.assertIn('Personal', self.root.clipboard_get())
        self.assertIn('Installed only', self.root.clipboard_get())
        authored_text = self.app.build_modlist_txt()
        self.assertNotIn('Personal', authored_text)
        view.clear_button.invoke()
        self.assertIsNone(self.app.installed_report)
        self.assertEqual(len(self.app.row_by_item_id), 2)
        self.assertNotIn('local_version', self.app.tree['displaycolumns'])

    def test_changed_version_is_amber_and_reports_evidence_on_both_sides(self):
        self.open_package()
        (self.fixture.mods / 'Alpha/meta.ini').write_text(self.fixture.meta('2.0'))
        view = self.comparison()
        view.start()
        self.wait(lambda: view.running)
        row = next(r for r in self.app.row_by_item_id.values() if r['raw_name'] == 'Alpha')
        self.assertEqual(row['compare_status'], 'yellow')
        self.app.comparison_filter.set('Version differs')
        self.assertEqual(len(self.app.tree.get_children()), 1)
        item = self.app.tree.get_children()[0]
        values = dict(zip(self.app.tree['columns'], self.app.tree.item(item, 'values')))
        self.assertEqual(values['version'], '1.0')
        self.assertEqual(values['local_version'], '2.0')
        self.assertIn('Version differs', values['result'])

    def test_profile_selector_changes_clear_report_and_diffs_are_exported(self):
        self.open_package()
        view = self.comparison()
        view.start()
        self.wait(lambda: view.running)
        self.assertTrue(view.report['profile_files'])
        row = next(r for r in view.report['profile_files'] if r['path'] == 'modlist.txt')
        self.assertEqual(row['status'], 'Text differs')
        self.assertIn('Personal', row['diff'])
        output = self.fixture.root / 'comparison.html'
        with patch('comparison_ui.filedialog.asksaveasfilename', return_value=str(output)):
            view.export()
        self.assertIn('Profile files', output.read_text())
        other = self.fixture.profile.parent / 'Second'
        other.mkdir()
        (other / 'modlist.txt').write_text('+Alpha\n')
        view.refresh_profiles()
        view.selected_profile.set('Second')
        self.assertEqual(view.local.get(), str(other))
        self.assertIsNone(view.report)
        self.assertIsNone(self.app.installed_report)


class MatchingAndProfileFilesTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ComparisonTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.temp.cleanup)

    def test_reciprocal_nexus_version_match_keeps_variants_separate(self):
        def record(version):
            return {'meta': {'mod_id': '123', 'game': 'skyrimspecialedition', 'version': version, 'repository': 'nexus'}}
        a = {'mods': {'old-a': record('1'), 'old-b': record('2')}}
        b = {'mods': {'new-a': record('1'), 'new-b': record('2')}}
        matches, used = match_records(a, b)
        self.assertEqual(matches['old-a'], ('new-a', 'Nexus mod and version'))
        self.assertEqual(matches['old-b'], ('new-b', 'Nexus mod and version'))
        b['mods']['new-b'] = record('1')
        self.assertFalse(match_records(a, b)[0])

    def test_profile_ini_fallback_and_generated_text_do_not_claim_equality(self):
        from package_model import PackageModel
        from profile_compare import compare_profile_files
        f = self.fixture
        model = PackageModel(f.package(extra=[{'$type': 'RemappedInlineFile', 'To': 'profiles/Authored/tool.ini', 'SourceDataID': '0'},
            {'$type': 'InlineFile', 'To': 'profiles/Authored/modlist.txt.2026_11_20', 'SourceDataID': '0'},
            {'$type': 'InlineFile', 'To': 'profiles/Authored/backups/old.ini', 'SourceDataID': '0'}]))
        folder = f.profile / 'ini files'
        folder.mkdir()
        (folder / 'tool.ini').write_text('configured by installer')
        rows = compare_profile_files(model, 'Authored', f.profile)
        row = next(r for r in rows if r['path'] == 'tool.ini')
        self.assertEqual(row['local_path'], str(folder / 'tool.ini'))
        self.assertEqual(row['status'], 'Unverified')
        self.assertFalse(any('backup' in r['path'] or '.2026' in r['path'] for r in rows))
        (folder / 'extra.ini').write_text('personal setting')
        rows = compare_profile_files(model, 'Authored', f.profile, limit=5)
        self.assertTrue(any(r['status'] == 'Not previewed' for r in rows))

    def test_state_can_resolve_wabbajack_mods_without_profile_symlink(self):
        f = self.fixture
        wj = f.root / '.wj/install'
        (wj / 'root').mkdir(parents=True)
        f.mods.rename(wj / 'root/mods')
        (f.profile / 'profile_state.json').write_text(json.dumps({'profile_settings': {
            'profile_specific_mods': True, 'wabbajack_directory': str(wj)}}))
        local = read_local_profile(f.profile)
        self.assertEqual(Path(local['mods_dir']), wj / 'root/mods')
        self.assertTrue(local['mods']['alpha']['present'])

    def test_content_uncertainty_and_ambiguity_never_show_green(self):
        report = compare_paths(self.fixture.package(), self.fixture.profile)
        row = next(r for r in report['rows'] if r['package_name'] == 'Alpha')
        row['content']['status'] = 'Files unverified'
        self.assertEqual(category(row), 'Needs review')
        row['content']['status'] = 'Files differ'
        self.assertEqual(category(row), 'Different')
        self.assertTrue(matches_filter(row, 'Files differ'))
        row['local_name'], row['candidates'] = '', ['Variant A', 'Variant B']
        self.assertEqual(category(row), 'Needs review')


class SimpleCopyTests(unittest.TestCase):
    def setUp(self):
        self.fixture = clone_fixtures.CloneTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(str(error))
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        f = self.fixture
        self.view = ProfileCopyView(self.root, folder=str(f.source.parent), selected=f.source.name)
        self.view.preferences_path = f.root / 'copy-preferences.json'
        self.view.config_dir.set(str(f.config))
        self.view.name.set('Copy')

    def wait(self):
        end = time.monotonic() + 10
        while self.view.busy and time.monotonic() < end:
            self.root.update()
            time.sleep(.01)
        self.assertFalse(self.view.busy)

    def test_root_and_profile_selection_preview_and_copy_need_no_review_records(self):
        view, f = self.view, self.fixture
        self.assertEqual(view.source_box['values'], ('Original',))
        self.assertEqual(Path(view.game_root.get()), f.staging.parent / 'Copy Game Root')
        with patch.object(cloner, 'running_amethyst', return_value=[]), patch.object(view, 'fail') as errors:
            view.preview_copy()
            self.wait()
            errors.assert_not_called()
            self.assertTrue(view.preview)
            self.assertFalse((f.staging / 'profiles/Copy').exists())
            view.name.set('Changed')
            self.assertIsNone(view.preview)
            self.assertEqual(Path(view.game_root.get()).name, 'Changed Game Root')
            view.name.set('Copy')
            view.preview_copy()
            self.wait()
            view.create_copy()
            self.wait()
            errors.assert_not_called()
        destination = f.staging / 'profiles/Copy'
        self.assertTrue(destination.is_dir())
        self.assertIn('Profile created:', view.copy_text.get('1.0', tk.END))
        old, new = f.source / 'mods/One Mod/test.esp', destination / 'mods/One Mod/test.esp'
        self.assertNotEqual(old.stat().st_ino, new.stat().st_ino)
        self.assertEqual((destination / 'modlist.txt').read_bytes(), (f.source / 'modlist.txt').read_bytes())
        new.write_bytes(b'changed')
        self.assertEqual(old.read_bytes(), b'fixture-plugin')
        self.assertEqual(str(view.create_button['state']), 'disabled')

    def test_wabbajack_state_finds_source_and_existing_names_are_protected(self):
        view, f = self.view, self.fixture
        wj = f.staging / '.wj/example/root'
        wj.mkdir(parents=True)
        (f.source / 'mods').rename(wj / 'mods')
        f.game.rename(wj / 'Game Root')
        (f.source / 'profile_state.json').write_text(json.dumps({'profile_settings': {
            'profile_specific_mods': True, 'wabbajack_directory': str(wj.parent), 'wabbajack_profile': 'Original'}}))
        view.source_changed()
        self.assertIn(str(wj.parent), view.source_info.get())
        plan = cloner.build_plan(view.clone_args())
        self.assertEqual(plan.source_game, wj / 'Game Root')
        self.assertTrue(any(job.source == wj / 'mods' for job in plan.jobs))
        view.name.set('ORIGINAL')
        with patch.object(view, 'fail') as errors:
            view.preview_copy()
            self.wait()
            self.assertIn('already exists', str(errors.call_args.args[0]))
        self.assertIsNone(view.preview)


if __name__ == '__main__':
    unittest.main()
