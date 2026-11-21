import ctypes.util
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from comparison_presenter import result_label
from profile_compare import FileHasher, ComparisonError, compare_paths, compare_inventories
import test_profile_compare as fixtures
import test_workflow as workflow


@unittest.skipUnless(ctypes.util.find_library('xxhash'), 'libxxhash unavailable')
class HashReuseTests(unittest.TestCase):
    def test_repeat_uses_cache_and_same_size_edit_with_restored_mtime_invalidates_it(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); file = root / 'data'; cache = root / 'cache.sqlite'
            file.write_bytes(b'a' * 1024**2)
            first = FileHasher(cache_path=cache)
            digest = first.digest(file); first.close()
            second = FileHasher(cache_path=cache)
            self.assertEqual(second.digest(file), digest)
            self.assertEqual(second.bytes_read, 0)
            self.assertEqual(second.cache_hits, 1)
            second.close()
            before = file.stat()
            file.write_bytes(b'b' * 1024**2)
            os.utime(file, ns=(before.st_atime_ns, before.st_mtime_ns))
            third = FileHasher(cache_path=cache)
            self.assertNotEqual(third.digest(file), digest)
            self.assertGreater(third.bytes_read, 0)
            third.close()

    def test_corrupt_cache_falls_back_to_reading(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); file=root/'data'; file.write_bytes(b'data')
            cache=root/'cache.sqlite'; cache.write_bytes(b'bad sqlite')
            hasher=FileHasher(cache_path=cache)
            hasher.digest(file)
            self.assertEqual(hasher.bytes_read, 4)
            hasher.close()

    def test_cancellation_during_large_file_read_does_not_cache_partial_hash(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); file=root/'data'; file.write_bytes(b'x' * 3 * 1024**2)
            def progress(message):
                if 'MiB read' in message:
                    raise ComparisonError('cancelled')
            hasher=FileHasher(progress, root/'cache.sqlite')
            with self.assertRaisesRegex(ComparisonError, 'cancelled'):
                hasher.digest(file)
            self.assertIsNone(hasher.cache.get(file,file.stat()))
            hasher.close()


class ComparisonImprovementTests(unittest.TestCase):
    setUp=workflow.WorkflowTests.setUp
    open_package=workflow.WorkflowTests.open_package
    comparison=workflow.WorkflowTests.comparison
    wait=workflow.WorkflowTests.wait

    def test_order_columns_are_adjacent_and_result_explains_matching(self):
        self.open_package(); view=self.comparison(); view.start(); self.wait(lambda:view.running)
        self.assertEqual(self.app.tree['displaycolumns'][0], 'local_order')
        row=next(r for r in view.report['rows'] if r['package_name']=='Alpha')
        self.assertIn('Folder name', result_label(row))
        self.assertIn('Matches', result_label(row))

    def test_visible_progress_and_cancel_during_initial_indexing(self):
        self.open_package(); view=self.comparison()
        started, release=threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def blocked(*args, progress):
            progress('Indexing authored files…'); started.set(); release.wait(4); progress('Still indexing')
        with patch('comparison_ui.compare_paths', side_effect=blocked):
            view.start(); self.assertTrue(started.wait(2))
            self.assertEqual(view.progress_bar.winfo_manager(),'grid')
            view.cancel_button.invoke()
            self.assertIn('Cancelling',view.status.get())
            release.set(); self.wait(lambda:view.running)
        self.assertIsNone(view.report)
        self.assertFalse(view.progress_bar.winfo_manager())
        self.assertIn('cancelled',view.status.get())

    def test_size_mismatch_avoids_hash_read(self):
        f=self.fixture
        package=f.package(entries=[('Alpha',True)])
        (f.mods/'Alpha/content.txt').write_bytes(b'wrong size')
        with patch.object(FileHasher,'digest',side_effect=AssertionError('Unnecessary read')):
            report=compare_paths(package,f.profile,verify_files=True)
        row=report['rows'][0]
        self.assertEqual(row['content']['status'],'Files differ')
        self.assertEqual(row['content']['differences'][0]['reason'],'File size differs')

    def test_empty_profile_can_compare_without_mod_store(self):
        f=self.fixture
        empty=f.root/'profiles/Empty';empty.mkdir();(empty/'modlist.txt').write_text('')
        report=compare_paths(f.package(),empty)
        self.assertTrue(all(row['status']=='Package only' for row in report['rows']))
