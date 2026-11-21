import copy
import json
import shutil
import subprocess
from pathlib import Path
import tempfile
import threading
import time
import unittest
import zipfile
from unittest.mock import patch

from directive_details import archive_contributions, describe_directive
from preferred_sources import presentation_row, verify_preferred_sources, SourceVerificationCancelled
from profile_compare import FileHasher
import test_directive_details as details
import preferred_sources


class PreferredSourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)

    def archive(self, empty_path='Data/.force-install', payload=b''):
        path = self.folder / 'Alpha.zip'
        with zipfile.ZipFile(path, 'w') as z:
            z.writestr('Data/content.txt', b'content')
            z.writestr(empty_path, payload)
        row = details.example_row()
        hasher = FileHasher()
        try:
            digest = hasher.digest(path)
        finally:
            hasher.cache.close()
        row['source_archives'][0]['Hash'] = digest
        row['folder_files'][0].update(archive_hash=digest, archive_hash_path=[digest, 'Data/content.txt'])
        return row

    def test_verified_presentation_uses_primary_without_mutating_export_inputs(self):
        row = self.archive()
        original = copy.deepcopy(row)
        result = verify_preferred_sources(row, self.folder)
        display = presentation_row(row, result)
        self.assertEqual(len(display['source_archives']), 1)
        self.assertEqual(len(archive_contributions(display)[0]['files']), 2)
        item = display['folder_files'][1]
        self.assertEqual(item['archive_path'], 'Data/.force-install')
        self.assertEqual(row, original)
        self.assertEqual(item['recorded_file_info']['archive_hash'], 'other')
        text = describe_directive(item, {'ArchiveHashPath': ['other', 'different/folder/.force-install']},
                                  display['source_archives'][0], archive_contributions(display)[0])
        self.assertIn('Recorded Wabbajack source archive: Other.zip', text)
        self.assertIn('Recorded source path: different/folder/.force-install', text)
        self.assertIn('Source path: Data/.force-install', text)

    def test_wrong_path_nonempty_ambiguous_or_wrong_archive_never_replace_a_source(self):
        for path, payload in [('elsewhere/.force-install', b''), ('Data/.force-install', b'x')]:
            row = self.archive(path, payload)
            with self.subTest(path=path, payload=payload):
                self.assertFalse(verify_preferred_sources(row, self.folder)['alternatives'])
        row = self.archive()
        row['source_archives'][0]['Hash'] = 'wrong-archive-hash'
        self.assertFalse(verify_preferred_sources(row, self.folder)['alternatives'])
        row = self.archive()
        with zipfile.ZipFile(self.folder / 'Alpha.zip', 'a') as z:
            z.writestr('Data/.FORCE-INSTALL', b'')
        hasher = FileHasher()
        try:
            row['source_archives'][0]['Hash'] = hasher.digest(self.folder / 'Alpha.zip')
        finally:
            hasher.cache.close()
        row['folder_files'][0]['archive_hash'] = row['source_archives'][0]['Hash']
        self.assertFalse(verify_preferred_sources(row, self.folder)['alternatives'])

    def test_missing_or_changed_download_reverts_to_recorded_sources(self):
        row = self.archive()
        result = verify_preferred_sources(row, self.folder)
        (self.folder / 'Alpha.zip').unlink()
        self.assertIs(presentation_row(row, result), row)
        self.assertFalse(verify_preferred_sources(row, self.folder)['alternatives'])

    def nonempty_row(self, payload=b'animation bytes'):
        row = self.archive('Data/clip.hkx', payload)
        sample = self.folder / 'sample'
        sample.write_bytes(payload)
        hasher = FileHasher()
        try:
            digest = hasher.digest(sample)
        finally:
            hasher.cache.close()
        row['folder_files'][1].update(target_path='mods/Alpha/clip.hkx', size=len(payload), hash=digest)
        return row

    def test_nonempty_files_require_matching_contents_and_keep_patch_directives(self):
        row = self.nonempty_row()
        result = verify_preferred_sources(row, self.folder)
        self.assertEqual(len(presentation_row(row, result)['source_archives']), 1)
        row['folder_files'][1]['hash'] = 'different-content-same-size'
        self.assertFalse(verify_preferred_sources(row, self.folder)['alternatives'])
        row = self.nonempty_row()
        row['folder_files'][1]['directive_type'] = 'PatchedFromArchive'
        self.assertFalse(verify_preferred_sources(row, self.folder)['alternatives'])

    def test_cancellation_is_not_downgraded_to_a_missing_alternative(self):
        row = self.nonempty_row()
        def cancelled(message):
            raise SourceVerificationCancelled('cancelled')
        with self.assertRaises(SourceVerificationCancelled):
            verify_preferred_sources(row, self.folder, progress=cancelled)

    def test_unseen_option_folders_are_disambiguated_by_full_path_and_hash(self):
        row = self.nonempty_row()
        archive = self.folder / 'Alpha.zip'
        suffix = 'meshes/actors/animations/clip.hkx'
        with zipfile.ZipFile(archive, 'a') as z:
            z.writestr('Options/Vanilla/' + suffix, b'animation bytes')
            z.writestr('Options/Other/' + suffix, b'different bytes')
            z.writestr('Unrelated/clip.hkx', b'animation bytes')
        hasher = FileHasher()
        try:
            digest = hasher.digest(archive)
        finally:
            hasher.cache.close()
        row['source_archives'][0]['Hash'] = digest
        row['folder_files'][0].update(archive_hash=digest, archive_hash_path=[digest, 'Data/content.txt'])
        row['folder_files'][1]['target_path'] = 'mods/Alpha/' + suffix
        result = verify_preferred_sources(row, self.folder)
        self.assertEqual(result['alternatives']['mods/Alpha/' + suffix]['member'], 'Options/Vanilla/' + suffix)
        row['folder_files'][1]['target_path'] = 'mods/Alpha/meshes/other/clip.hkx'
        self.assertFalse(verify_preferred_sources(row, self.folder)['alternatives'])

    def test_unar_fallback_reads_members_when_sevenzip_cannot_open_archive(self):
        row = self.nonempty_row()
        archive = self.folder / 'Alpha.rar'
        archive.write_bytes(b'archive requiring a different decoder')
        hasher = FileHasher()
        try:
            digest = hasher.digest(archive)
        finally:
            hasher.cache.close()
        row['source_archives'][0].update(Name=archive.name, Hash=digest)
        row['folder_meta']['installation_file'] = archive.name
        row['folder_files'][0].update(archive_hash=digest, archive_hash_path=[digest, 'Data/content.txt'])
        entry = {'XADFileName': 'Data/clip.hkx', 'XADFileSize': 15, 'XADIndex': 7}
        def command(arguments, output, progress, timeout=60):
            if arguments[0].endswith('/7z'):
                raise subprocess.CalledProcessError(2, arguments)
            if arguments[0].endswith('/lsar'):
                output.write(json.dumps({'lsarContents': [entry]}).encode())
            elif arguments[0].endswith('/unar'):
                self.assertEqual(arguments[-1], '7')
                target = Path(arguments[arguments.index('-o') + 1]) / entry['XADFileName']
                target.parent.mkdir(parents=True)
                target.write_bytes(b'animation bytes')
        with patch('preferred_sources.shutil.which', side_effect=lambda name: '/tools/' + name), \
             patch('preferred_sources.run_archive_tool', side_effect=command):
            result = verify_preferred_sources(row, self.folder)
        self.assertEqual(len(result['alternatives']), 1)
        self.assertEqual(result['message'], '')

    @unittest.skipUnless(shutil.which('7z'), '7-Zip required')
    def test_solid_archive_batches_members_and_hashes_each_output(self):
        row = self.nonempty_row()
        data = self.folder / 'Data'
        data.mkdir()
        (data / 'content.txt').write_bytes(b'content')
        (data / 'clip.hkx').write_bytes(b'animation bytes')
        (data / '.force-install').write_bytes(b'')
        archive = self.folder / 'Alpha.7z'
        subprocess.run(['7z', 'a', '-t7z', str(archive), 'Data'], cwd=self.folder,
                       capture_output=True, check=True)
        hasher = FileHasher()
        try:
            digest = hasher.digest(archive)
        finally:
            hasher.cache.close()
        row['source_archives'][0].update(Name='Alpha.7z', Hash=digest)
        row['folder_meta']['installation_file'] = 'Alpha.7z'
        row['folder_files'][0].update(archive_hash=digest, archive_hash_path=[digest, 'Data/content.txt'])
        empty = copy.deepcopy(details.example_row()['folder_files'][1])
        row['folder_files'].append(empty)
        with patch('preferred_sources.run_archive_tool', wraps=preferred_sources.run_archive_tool) as tool:
            checked = verify_preferred_sources(row, self.folder)
        self.assertEqual(len(checked['alternatives']), 2)
        self.assertEqual(sum(call.args[0][1] == 'x' for call in tool.call_args_list), 1)


class PreferredInterfaceTests(unittest.TestCase):
    setUp = details.DetailInterfaceTests.setUp

    def nonempty_package(self):
        helper = PreferredSourceTests()
        helper.setUp()
        self.addCleanup(helper.doCleanups)
        row = helper.nonempty_row()
        item = row['folder_files'][1]
        path = self.fixture.package(extra=[{'$type': 'FromArchive', 'To': item['target_path'],
                                           'Size': item['size'], 'Hash': item['hash'],
                                           'ArchiveHashPath': ['other', 'original/clip.hkx']}])
        with zipfile.ZipFile(path) as z:
            blobs = {name: z.read(name) for name in z.namelist()}
        manifest = json.loads(blobs['modlist'])
        manifest['Archives'] = row['source_archives']
        for directive in manifest['Directives']:
            if (directive.get('ArchiveHashPath') or [''])[0] == 'archive':
                directive['ArchiveHashPath'] = [row['source_archives'][0]['Hash'], 'Data/content.txt']
        blobs['modlist'] = json.dumps(manifest).encode()
        with zipfile.ZipFile(path, 'w') as z:
            for name, payload in blobs.items():
                z.writestr(name, payload)
        self.app.downloads_folder = str(helper.folder)
        return path

    def pump_until(self, finished):
        deadline = time.monotonic() + 10
        while not finished() and time.monotonic() < deadline:
            self.root.update()
            time.sleep(.01)
        self.assertTrue(finished())

    def test_open_finishes_nonempty_source_checks_before_publishing_rows(self):
        path = self.nonempty_package()
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def blocked(*args, **kwargs):
            entered.set()
            while not release.wait(.01):
                kwargs['progress']('Verifying source')
            return verify_preferred_sources(*args, **kwargs)
        with patch('wabbajack_viewer.verify_preferred_sources', side_effect=blocked):
            self.app.start_package_load(str(path))
            self.pump_until(entered.is_set)
            self.assertEqual(self.app.mod_rows, [])
            self.assertEqual(self.app.tree.get_children(), ())
            release.set()
            self.pump_until(lambda: not self.app.loading_package)
        row_id = next(item for item, row in self.app.row_by_item_id.items() if row['raw_name'] == 'Alpha')
        self.assertEqual(self.app.tree.set(row_id, 'sources'), '1')
        self.assertFalse(self.app._preferred_pending)

    def test_cancel_during_source_verification_preserves_the_previous_package(self):
        with patch('wabbajack_viewer.messagebox.showinfo'):
            self.assertTrue(self.app.extract_and_load_modlist(str(self.fixture.package())))
        previous_model = self.app.package_model
        previous_rows = self.app.mod_rows
        path = self.nonempty_package()
        entered = threading.Event()
        def blocked(*args, **kwargs):
            entered.set()
            for _ in range(500):
                kwargs['progress']('Verifying source')
                time.sleep(.01)
            return verify_preferred_sources(*args, **kwargs)
        with patch('wabbajack_viewer.verify_preferred_sources', side_effect=blocked):
            self.app.start_package_load(str(path))
            self.pump_until(entered.is_set)
            self.app.load_cancel.set()
            self.pump_until(lambda: not self.app.loading_package)
        self.assertIs(self.app.package_model, previous_model)
        self.assertIs(self.app.mod_rows, previous_rows)
        self.assertIn('cancelled', self.app.status_var.get().lower())

    def bulk_rows(self):
        helper = PreferredSourceTests()
        helper.setUp()
        self.addCleanup(helper.doCleanups)
        alpha = helper.archive()
        alpha.update(name='Alpha', author='', version='', size_mb='', number=1, active=True,
                     is_separator=False, archive=alpha['source_archives'][0], archive_hash=alpha['source_archives'][0]['Hash'],
                     archive_name='Alpha.zip')
        beta = copy.deepcopy(alpha)
        beta.update(name='Beta', raw_name='Beta', number=2)
        for item in beta['folder_files']:
            item['target_path'] = item['target_path'].replace('/Alpha/', '/Beta/')
        self.app.mod_rows = [alpha, beta]
        self.app.archives = alpha['source_archives']
        self.app.active_profile = 'profiles/Authored'
        self.app.downloads_folder = str(helper.folder)
        return alpha, beta

    def finish_checks(self):
        deadline = time.monotonic() + 10
        while self.app._preferred_pending and time.monotonic() < deadline:
            self.root.update()
            time.sleep(.01)
        self.assertFalse(self.app._preferred_pending)

    def test_load_checks_unselected_and_filtered_mods_and_updates_counts_without_clicking(self):
        alpha, beta = self.bulk_rows()
        self.app.search_var.set('Alpha')
        self.app.update_ui_after_load(prepare_rows=False)
        self.finish_checks()
        self.assertIs(self.app.current_selected_row, alpha)
        self.assertIn(self.app.preferred_key(beta), self.app._preferred_cache)
        self.assertEqual(self.app.status_var.get(), 'Archive source checks complete: 2 mods checked')
        self.app.search_var.set('')
        counts = {row['raw_name']: self.app.tree.set(item, 'sources')
                  for item, row in self.app.row_by_item_id.items()}
        self.assertEqual(counts, {'Alpha': '1', 'Beta': '1'})
        self.assertEqual(beta['folder_files'][1]['archive_hash'], 'other')

    def test_visible_unselected_count_updates_in_place(self):
        alpha, beta = self.bulk_rows()
        self.app.update_ui_after_load(prepare_rows=False)
        before = {row['raw_name']: item for item, row in self.app.row_by_item_id.items()}
        self.finish_checks()
        self.assertEqual(self.app.tree.set(before['Beta'], 'sources'), '1')
        self.assertIs(self.app.current_selected_row, alpha)
        self.assertEqual(self.app.tree.selection(), (before['Alpha'],))

    def test_folder_change_discards_old_results_and_uses_one_worker(self):
        self.bulk_rows()
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        threads = set()
        old_folder = self.app.downloads_folder
        def verify(row, folder, archives, **kwargs):
            threads.add(threading.get_ident())
            if folder == old_folder:
                entered.set()
                release.wait(5)
            return {'alternatives': {}, 'path': '/nonexistent-preferred-source-test', 'signature': None, 'message': folder}
        with patch('wabbajack_viewer.verify_preferred_sources', side_effect=verify):
            self.app.start_preferred_source_checks()
            self.assertTrue(entered.wait(1))
            self.app.downloads_folder = str(self.fixture.root / 'different-downloads')
            self.app.start_preferred_source_checks()
            release.set()
            self.finish_checks()
        self.assertEqual(len(threads), 1)
        self.assertEqual(len(self.app._preferred_cache), 2)
        self.assertTrue(all(key[1] == self.app.downloads_folder for key in self.app._preferred_cache))

    def test_explicit_recheck_replaces_cached_results(self):
        self.bulk_rows()
        self.app.update_ui_after_load(prepare_rows=False)
        with patch('wabbajack_viewer.verify_preferred_sources', wraps=verify_preferred_sources) as check:
            self.app.start_preferred_source_checks()
            self.finish_checks()
        self.assertEqual(check.call_count, 2)

    def test_cancelled_recheck_retains_verified_sources_and_discards_late_results(self):
        self.bulk_rows()
        self.app.update_ui_after_load(prepare_rows=False)
        previous = dict(self.app._preferred_cache)
        entered, release, finished = threading.Event(), threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def delayed(*args, **kwargs):
            entered.set()
            release.wait(5)
            try:
                kwargs['progress']('Checking files')
            finally:
                finished.set()
            raise AssertionError('Cancellation checkpoint was ignored')
        with patch('wabbajack_viewer.verify_preferred_sources', side_effect=delayed):
            self.app.start_preferred_source_checks()
            self.assertTrue(entered.wait(2))
            self.assertEqual(self.app.activity.owner, 'sources')
            self.app.activity.cancel_button.invoke()
            self.assertFalse(self.app._preferred_pending)
            self.assertEqual(self.app._preferred_cache, previous)
            release.set()
            self.assertTrue(finished.wait(2))
            self.root.update()
        self.assertEqual(self.app._preferred_cache, previous)
        self.assertIsNone(self.app.activity.owner)
        self.assertTrue(all(self.app.tree.set(item, 'sources') == '1' for item in self.app.row_by_item_id))

    def test_background_check_refreshes_groups_and_preserves_recorded_raw_details(self):
        app = self.app
        helper = PreferredSourceTests()
        helper.setUp()
        self.addCleanup(helper.doCleanups)
        row = helper.archive()
        row.update(name='Alpha', author='', version='', size_mb='', number=1, active=True,
                   is_separator=False, archive=row['source_archives'][0], archive_hash=row['source_archives'][0]['Hash'])
        app.mod_rows = [row]
        app.archives = row['source_archives']
        app.active_profile = 'profiles/Authored'
        app.downloads_folder = str(helper.folder)
        app.apply_filters_and_sort()
        item = next(iter(app.row_by_item_id))
        app.tree.selection_set(item)
        app.on_mod_select(None)
        deadline = time.monotonic() + 10
        while app._preferred_pending and time.monotonic() < deadline:
            self.root.update()
            time.sleep(.01)
        self.assertFalse(app._preferred_pending)
        self.assertEqual(len(app.sources_tree.get_children()), 1)
        self.assertIn('2 files', app.sources_tree.item(app.sources_tree.get_children()[0], 'text'))
        self.assertEqual(app.tree.set(item, 'sources'), '1')
        file_id = next(key for key, value in app.row_by_file_item_id.items() if value['target_path'].endswith('.force-install'))
        app.files_tree.selection_set(file_id)
        app.show_file_details('files')
        self.assertIn('Recorded Wabbajack source archive: Other.zip', app.file_detail_text.get('1.0', 'end'))
        self.assertEqual(row['folder_files'][1]['archive_hash'], 'other')


if __name__ == '__main__':
    unittest.main()
