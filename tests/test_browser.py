import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from ozon_monitor.browser import browser_options


class BrowserSelection(unittest.TestCase):
    def test_bundled_profile_unchanged(self):
        options = browser_options('playwright', Path('/tmp/monitor'))
        self.assertEqual(options, {'channel': 'chromium', 'user_data_dir': '/tmp/monitor/browser'})

    def test_system_binary_uses_separate_profile(self):
        with tempfile.NamedTemporaryFile() as binary:
            options = browser_options('system-chromium', Path('/tmp/monitor'), binary.name)
        self.assertEqual(options['executable_path'], binary.name)
        self.assertEqual(options['user_data_dir'], '/tmp/monitor/browser-system-chromium')
        self.assertNotIn('channel', options)

    @patch('ozon_monitor.browser.shutil.which', return_value=None)
    def test_missing_binary_has_actionable_error(self, _):
        with self.assertRaisesRegex(ValueError, 'CHROMIUM_EXECUTABLE_PATH'):
            browser_options('system-chromium', Path('/tmp/monitor'))

    def test_invalid_browser_rejected(self):
        with self.assertRaises(ValueError):
            browser_options('invalid', Path('/tmp/monitor'))
