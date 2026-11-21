import json
import time
import tkinter as tk
import unittest
from unittest.mock import patch
import zipfile

import test_profile_compare as fixtures
from package_model import PackageModel
from profile_compare import ComparisonError, read_package
from wabbajack_viewer import WabbajackGuideApp


class PackageTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ComparisonTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.temp.cleanup)

    def generated(self):
        return self.fixture.package(extra=[
            {'$type': 'FromArchive', 'To': 'TEMP_BSA_FILES/container/inside.dds', 'ArchiveHashPath': ['archive', 'inner.bsa', 'inside.dds'], 'Hash': 'input', 'Size': 20},
            {'$type': 'CreateBSA', 'To': 'mods/Alpha/generated.bsa', 'TempID': 'container', 'FileStates': [{'Path': 'inside.dds'}], 'Hash': 'output', 'Size': 0},
            {'$type': 'MergedPatch', 'To': 'mods/Alpha/merged.esp', 'Sources': [{'RelativePath': 'TEMP_BSA_FILES/container/inside.dds'}], 'Hash': 'merged'},
            {'$type': 'InlineFile', 'To': 'tools/missing.ini', 'SourceDataID': 'missing'},
            {'$type': 'FutureKind', 'To': 'Game Root/unknown.bin', 'FutureField': {'retain': True}},
        ])

    def test_shared_graph_provenance_diagnostics_and_accounting(self):
        model = PackageModel(self.generated())
        bsa = model.destinations['mods/alpha/generated.bsa']
        self.assertEqual(model.source_hashes(bsa), ['archive'])
        self.assertEqual(model.source_hashes(model.destinations['mods/alpha/merged.esp']), ['archive'])
        self.assertEqual(len(model.ingredients(bsa)), 1)
        self.assertEqual({d['code'] for d in model.diagnostics}, {'missing_inline_payload', 'unsupported_instruction'})
        self.assertTrue(model.destinations['game root/unknown.bin']['FutureField']['retain'])
        overview = model.overview()
        self.assertEqual(sum(overview['categories'].values()), len(model.destinations))
        self.assertEqual(overview['unknown_generated_sizes'], 1)
        inventory = read_package(model.path)
        self.assertEqual(inventory['coverage']['instructions'], len(model.destinations))
        self.assertEqual(inventory['diagnostics'], model.diagnostics)

    def test_missing_source_and_incomplete_generated_container_are_visible(self):
        model = PackageModel(self.fixture.package(extra=[
            {'$type': 'FromArchive', 'To': 'tools/test.bin', 'ArchiveHashPath': ['missing']},
            {'$type': 'CreateBSA', 'To': 'mods/Alpha/missing.bsa', 'TempID': 'absent', 'FileStates': [{'Path': 'missing.dds'}]},
        ]))
        self.assertEqual({d['code'] for d in model.diagnostics}, {'missing_archive', 'incomplete_generated_archive'})

    def test_inline_cached_preview_limits_and_file_changes(self):
        model = PackageModel(self.fixture.package())
        item = model.destinations['profiles/authored/modlist.txt']
        with self.assertRaisesRegex(ComparisonError, 'preview limit'):
            model.read_text(item, 1)
        self.assertIn('+Alpha', model.read_text(item))
        with patch.object(type(model.path), 'stat') as stat:
            stat.return_value.st_ino = -1
            with self.assertRaisesRegex(ComparisonError, 'changed'):
                model.read_text(item)

    def test_duplicate_unsafe_destinations_rejected_by_shared_reader(self):
        for path in ('../escape', 'mods/Alpha/content.txt'):
            with self.subTest(path=path), self.assertRaises(ComparisonError):
                PackageModel(self.fixture.package(extra=[{'To': path}]))

    def test_background_loader_and_cancellation(self):
        try:
            root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(str(error))
        root.withdraw()
        self.addCleanup(root.destroy)
        model = PackageModel(self.generated())
        with patch.object(WabbajackGuideApp, 'load_persisted_preferences'), patch.object(WabbajackGuideApp, 'auto_load_last_saved_modlist'):
            app = WabbajackGuideApp(root)
        app.config_path = self.fixture.root / 'viewer-preferences.json'
        app.start_package_load(str(model.path))
        deadline = time.monotonic() + 10
        while app.loading_package and time.monotonic() < deadline:
            root.update()
            time.sleep(.01)
        self.assertFalse(app.loading_package)
        self.assertEqual(len(app.mod_rows), 2)
        self.assertEqual(app.package_model.path, model.path)
        app.start_package_load(str(model.path))
        app.load_cancel.set()
        while app.loading_package and time.monotonic() < deadline:
            root.update()
            time.sleep(.01)
        self.assertFalse(app.loading_package)
        self.assertIn('cancelled', app.status_var.get().lower())


if __name__ == '__main__':
    unittest.main()
