"""Clean launches reset only disposable state, before constructing the UI."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import wabbajack_viewer as viewer


class CleanLaunchTests(unittest.TestCase):
    def test_reset_removes_state_and_keeps_user_files(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            state = [root / name for name in (
                'wabbajack_viewer.config.json', 'wabbajack_comparison.config.json',
                'wabbajack_copy.config.json', 'wabbajack_session.wvmodlist.json.gz',
            )]
            cache = root / 'cache' / 'xxhash64-v1.sqlite'
            cache.parent.mkdir()
            state.extend(Path(str(cache) + suffix) for suffix in ('', '-wal', '-shm', '-journal'))
            kept = [root / name for name in ('manual.wvmodlist.json.gz', 'list.wabbajack', 'report.html')]
            for path in state + kept:
                path.write_bytes(b'original')
            with patch.object(viewer, '__file__', str(root / 'wabbajack_viewer.py')), \
                    patch('hash_cache.default_cache_path', return_value=cache):
                viewer.reset_stored_state()
                viewer.reset_stored_state()  # Already-clean state is valid too.
            self.assertTrue(all(not path.exists() for path in state))
            self.assertTrue(all(path.read_bytes() == b'original' for path in kept))

    def test_clean_resets_before_ui_creation(self):
        events = []
        root = Mock()
        with patch.object(viewer, 'reset_stored_state', side_effect=lambda: events.append('reset')), \
                patch.object(viewer.tk, 'Tk', side_effect=lambda: (events.append('ui'), root)[1]), \
                patch.object(viewer, 'WabbajackGuideApp'):
            viewer.main(['--clean'])
        self.assertEqual(events, ['reset', 'ui'])

    def test_normal_launch_preserves_state(self):
        with patch.object(viewer, 'reset_stored_state') as reset, \
                patch.object(viewer.tk, 'Tk'), patch.object(viewer, 'WabbajackGuideApp'):
            viewer.main([])
        reset.assert_not_called()


if __name__ == '__main__':
    unittest.main()
