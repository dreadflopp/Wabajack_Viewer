"""Downloads discovery and the Wabbajack file picker work without a display."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from profile_locations import default_downloads_folder
from wabbajack_viewer import WabbajackGuideApp


class DownloadsLocationTests(unittest.TestCase):
    def test_linux_custom_downloads_and_fallback(self):
        with tempfile.TemporaryDirectory() as folder, \
                patch('profile_locations.sys.platform', 'linux'), \
                patch.dict(os.environ, {'XDG_CONFIG_HOME': folder}):
            self.assertEqual(default_downloads_folder(), str(Path.home() / 'Downloads'))
            config = Path(folder) / 'user-dirs.dirs'
            config.write_text('# custom location\nXDG_DOWNLOAD_DIR="$HOME/My Downloads"\n')
            self.assertEqual(default_downloads_folder(), str(Path.home() / 'My Downloads'))
            config.write_text('XDG_DOWNLOAD_DIR="/mnt/storage/Downloads"\n')
            self.assertEqual(default_downloads_folder(), '/mnt/storage/Downloads')

    def test_windows_redirected_downloads(self):
        registry = Mock()
        registry.OpenKey.return_value.__enter__ = Mock(return_value='key')
        registry.OpenKey.return_value.__exit__ = Mock(return_value=False)
        registry.QueryValueEx.return_value = (r'D:\User Downloads', 1)
        with patch('profile_locations.sys.platform', 'win32'), \
                patch.dict('sys.modules', {'winreg': registry}):
            self.assertEqual(default_downloads_folder(), r'D:\User Downloads')
        registry.QueryValueEx.assert_called_once_with('key', '{374DE290-123F-4565-9164-39C4925E467B}')

    def test_wabbajack_picker_starts_in_downloads(self):
        app = WabbajackGuideApp.__new__(WabbajackGuideApp)
        app._closing = False
        with patch('wabbajack_viewer.default_downloads_folder', return_value='/custom/Downloads'), \
                patch('wabbajack_viewer.filedialog.askopenfilename', return_value='') as picker:
            app.load_wabbajack_file()
        self.assertEqual(picker.call_args.kwargs['initialdir'], '/custom/Downloads')


if __name__ == '__main__':
    unittest.main()
