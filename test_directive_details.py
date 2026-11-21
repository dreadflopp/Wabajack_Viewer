"""Coverage for source interpretation and inspection of the unchanged authored instructions."""
import json
import tkinter as tk
import unittest
from unittest.mock import patch

from directive_details import EMPTY_FILE_HASH, archive_contributions, contribution_summary, describe_directive
import test_profile_compare as fixtures
from wabbajack_viewer import WabbajackGuideApp


def example_row():
    return {'raw_name': 'Alpha', 'folder_meta': {'installation_file': 'Alpha.zip'},
            'source_archives': [{'Hash': 'archive', 'Name': 'Alpha.zip'},
                                {'Hash': 'other', 'Name': 'Other.zip', 'State': {'Name': 'Other mod'}}],
            'folder_files': [
                {'target_path': 'mods/Alpha/content.txt', 'archive_hash': 'archive',
                 'archive_path': 'content.txt', 'size': 10, 'hash': 'content', 'directive_type': 'FromArchive'},
                {'target_path': 'mods/Alpha/.force-install', 'archive_hash': 'other',
                 'archive_path': 'different/folder/.force-install', 'size': 0, 'hash': EMPTY_FILE_HASH,
                 'directive_type': 'FromArchive', 'archive_hash_path': ['other', 'different/folder/.force-install']}]}


class ContributionTests(unittest.TestCase):
    def test_summary_uses_destination_roles_without_guessing_from_source_titles(self):
        def summary(paths):
            return contribution_summary({'empty_only': False, 'files': [{'target_path': path} for path in paths]})
        self.assertEqual(summary(['mods/Test/config.json', 'mods/Test/settings.ini']), '2 configuration files')
        self.assertEqual(summary(['mods/Test/animations/male/idle.hkx', 'mods/Test/animations/male/walk.hkx']), '2 male animations')
        self.assertEqual(summary(['mods/Test/animations/female/idle.hkx']), '1 female animation')
        self.assertEqual(summary(['mods/Test/animations/male/mt_sprintforward.hkx',
                                  'mods/Test/animations/female/mt_sprintforward.hkx']), '2 sprint animations')
        # A skeleton/physics HKX outside an animations tree is not an animation clip.
        self.assertEqual(summary(['mods/Female/skeleton.hkx']), '1 file')
        self.assertEqual(summary(['mods/Test/config.json', 'mods/Test/animations/male/walk.hkx', 'mods/Test/readme.txt']),
                         '3 files · 1 configuration file, 1 male animation, 1 other file')

    def test_empty_source_is_distinct_from_mod_identity_and_keeps_recorded_mapping(self):
        row = example_row()
        before = json.dumps(row)
        primary, other = archive_contributions(row)
        self.assertTrue(primary['primary'])
        self.assertTrue(other['auxiliary_empty'])
        self.assertEqual(other['role'], 'Empty files only')
        self.assertEqual(json.dumps(row), before)
        text = describe_directive(other['files'][0], None, other['archive'], other)
        self.assertIn('Source archive: Other.zip', text)
        self.assertIn('does not establish a mod dependency', text)
        self.assertIn('has not been checked', text)

    def test_missing_size_hash_generated_outputs_and_mixed_contributions_are_not_empty_only(self):
        cases = [{'size': None}, {'hash': None}, {'size': 4}, {'directive_type': 'CreateBSA'},
                 {'directive_type': 'PatchedFromArchive'}]
        for change in cases:
            row = example_row()
            row['folder_files'][1].update(change)
            with self.subTest(change=change):
                self.assertFalse(archive_contributions(row)[1]['auxiliary_empty'])
        row = example_row()
        row['folder_files'].append(dict(row['folder_files'][0], archive_hash='other'))
        self.assertFalse(archive_contributions(row)[1]['auxiliary_empty'])

    def test_primary_empty_files_stay_primary_and_unresolved_instructions_remain_visible(self):
        row = example_row()
        row['folder_meta']['installation_file'] = 'C:\\downloads\\OTHER.ZIP'
        other = archive_contributions(row)[1]
        self.assertTrue(other['primary'])
        self.assertFalse(other['auxiliary_empty'])
        row['folder_files'].append({'target_path': 'mods/Alpha/generated.bsa', 'directive_type': 'CreateBSA', 'size': 0})
        generated = archive_contributions(row)[-1]
        self.assertEqual(generated['files'][0]['directive_type'], 'CreateBSA')
        self.assertIn('Expected output size: Unknown', describe_directive(generated['files'][0], None))


class DetailInterfaceTests(unittest.TestCase):
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

    def test_combined_install_explains_contributions_and_recorded_titles_without_reassigning_files(self):
        app = self.app
        archives = [{'Hash': 'base', 'Name': 'Base.7z', 'State': {'Name': 'Base title'}},
                    {'Hash': 'male', 'Name': 'Follower-download.7z', 'State': {'Name': 'Old follower title'}},
                    {'Hash': 'sprint', 'Name': 'Sprint-addon.7z'},
                    {'Hash': 'female', 'Name': 'Female-addon.7z'}]
        def file(archive_hash, path):
            return {'target_path': 'mods\\Demo\\' + path.replace('/', '\\'), 'archive_hash': archive_hash,
                    'archive_path': path, 'size': 1, 'directive_type': 'FromArchive'}
        files = [file('base', f'conditions/{i}/config.json') for i in range(5)]
        files += [file('male', f'animations/male/walk{i}.hkx') for i in range(11)]
        files += [file('sprint', f'animations/{gender}/mt_sprintforward.hkx') for gender in ('male', 'female')]
        files += [file('female', f'animations/female/walk{i}.hkx') for i in range(11)]
        row = {'name': 'Demo', 'raw_name': 'Demo', 'number': 1, 'active': True, 'is_separator': False,
               'author': '', 'version': '', 'size_mb': '', 'archive_hash': 'base', 'archive': archives[0],
               'source_archives': archives, 'folder_meta': {'installation_file': 'Base.7z'}, 'folder_files': files}
        before = json.dumps(row)
        app.mod_rows = [row]
        app.active_profile = 'profiles\\Demo'
        app.apply_filters_and_sort()
        app.tree.selection_set(next(iter(app.row_by_item_id)))
        app.on_mod_select(None)
        text = app.overview_text.get('1.0', 'end')
        for summary in ('5 configuration files', '11 male animations', '2 sprint animations', '11 female animations'):
            self.assertIn('Contribution: ' + summary, text)
            self.assertTrue(any(summary in app.sources_tree.item(parent, 'text') for parent in app.sources_tree.get_children()))
        self.assertIn('Role: Recorded extraction source', text)
        self.assertIn('recorded title): Old follower title', text)
        self.assertIn('Archive filename: Follower-download.7z', text)
        self.assertIn('do not establish mod dependencies', text)
        self.assertEqual(app.archive_count_label(row), '4')
        self.assertEqual(len(app.row_by_file_item_id), 29)
        parent = next(parent for parent in app.sources_tree.get_children()
                      if app.sources_tree.set(parent, 'archive_filename') == 'Follower-download.7z')
        child = app.sources_tree.get_children(parent)[0]
        self.assertTrue(app.sources_tree.set(child, 'target_path').startswith('animations\\male\\'))
        self.assertEqual(json.dumps(row), before)

    def test_empty_groups_collapse_but_search_and_both_detail_views_retain_the_actual_source(self):
        app, row = self.app, example_row()
        app.current_selected_row = row
        raw = {'$type': 'FromArchive', 'To': 'mods/Alpha/.force-install',
               'ArchiveHashPath': ['other', 'different/folder/.force-install'], 'Size': 0,
               'Hash': EMPTY_FILE_HASH, 'FutureField': {'retained': True}}
        app.modlist_data = {'Directives': [raw]}
        app.update_sources_tab(row)
        parents = app.sources_tree.get_children()
        self.assertTrue(app.sources_tree.item(parents[0], 'open'))
        self.assertFalse(app.sources_tree.item(parents[1], 'open'))
        self.assertIn('1 empty file', app.sources_tree.item(parents[1], 'text'))
        app.sources_search_var.set('.force-install')
        parent = app.sources_tree.get_children()[0]
        self.assertTrue(app.sources_tree.item(parent, 'open'))
        child = app.sources_tree.get_children(parent)[0]
        app.sources_tree.selection_set(child)
        app.show_file_details('sources')
        self.assertEqual(json.loads(app.file_detail_raw.get('1.0', 'end')), raw)
        self.assertIn('Source archive: Other.zip', app.file_detail_text.get('1.0', 'end'))
        detail_window = app.file_detail_window
        app.update_files_tab(row)
        file_id = next(item_id for item_id, info in app.row_by_file_item_id.items() if info['target_path'].endswith('.force-install'))
        app.files_tree.selection_set(file_id)
        app.show_file_details('files')
        self.assertIs(app.file_detail_window, detail_window)
        self.assertEqual(json.loads(app.file_detail_raw.get('1.0', 'end')), raw)
        self.assertEqual(row['folder_files'][1]['archive_hash'], 'other')

    def test_raw_patch_fields_survive_save_and_reload_and_clear_with_mod_selection(self):
        app = self.app
        raw = {'$type': 'PatchedFromArchive', 'To': 'mods/Alpha/patched.txt',
               'ArchiveHashPath': ['archive', 'content.txt'], 'PatchID': 'patch.bin',
               'FromHash': 'before', 'Hash': 'after', 'Size': 4}
        path = self.fixture.package(extra=[raw])
        with patch('wabbajack_viewer.messagebox.showinfo'):
            self.assertTrue(app.extract_and_load_modlist(str(path)))
        self.root.update()
        row = next(row for row in app.mod_rows if row['raw_name'] == 'Alpha')
        app.current_selected_row = row
        app.update_files_tab(row)
        item = next(key for key, value in app.row_by_file_item_id.items() if value['target_path'].endswith('patched.txt'))
        app.files_tree.selection_set(item)
        app.show_file_details('files')
        self.assertIn('apply the bundled binary patch', app.file_detail_text.get('1.0', 'end'))
        self.assertEqual(json.loads(app.file_detail_raw.get('1.0', 'end'))['PatchID'], 'patch.bin')
        snapshot = self.fixture.root / 'saved.wvmodlist.json.gz'
        with patch('wabbajack_viewer.filedialog.asksaveasfilename', return_value=str(snapshot)), \
             patch('wabbajack_viewer.messagebox.showinfo'):
            app.save_modlist(wait=True)
        self.assertTrue(app.load_saved_modlist_from_path(snapshot))
        info = next(info for row in app.mod_rows for info in row['folder_files'] if info['target_path'].endswith('patched.txt'))
        self.assertEqual(app.raw_directive_for_file(info), raw)
        app.clear_mod_details()
        self.assertFalse(app.file_detail_window.winfo_exists())
        self.assertEqual(str(app.file_details_button['state']), 'disabled')

    def test_unknown_instruction_without_archive_is_inspectable(self):
        app = self.app
        raw = {'$type': 'FutureKind', 'To': 'mods/Alpha/future.bin', 'UnknownSettings': [1, 2]}
        with patch('wabbajack_viewer.messagebox.showinfo'):
            self.assertTrue(app.extract_and_load_modlist(str(self.fixture.package(extra=[raw]))))
        self.root.update()
        row = next(row for row in app.mod_rows if row['raw_name'] == 'Alpha')
        app.current_selected_row = row
        app.update_sources_tab(row)
        item = next(item for item, value in app.row_by_source_item_id.items()
                    if value.get('file_info', {}).get('target_path', '').endswith('future.bin'))
        app.sources_tree.selection_set(item)
        app.show_file_details('sources')
        self.assertEqual(json.loads(app.file_detail_raw.get('1.0', 'end')), raw)

    def test_overview_counts_embedded_files_separately_from_downloads(self):
        app = self.app
        with patch('wabbajack_viewer.messagebox.showinfo'):
            self.assertTrue(app.extract_and_load_modlist(str(self.fixture.package(extra=[
                {'$type': 'InlineFile', 'To': 'mods/Alpha/note.ini', 'SourceDataID': '0'}]))))
        item = next(item for item, row in app.row_by_item_id.items() if row['raw_name'] == 'Alpha')
        app.tree.selection_set(item)
        app.on_mod_select(None)
        text = app.overview_text.get('1.0', 'end')
        self.assertIn('Download archives used: 1', text)
        self.assertIn('Embedded package file groups: 1', text)
        self.assertEqual(app.archive_count_label(app.current_selected_row), '1')


if __name__ == '__main__':
    unittest.main()
