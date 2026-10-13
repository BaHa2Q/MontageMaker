"""Version comparison used by the startup update check."""
import unittest

from ui.updater import newer


class UpdaterTests(unittest.TestCase):
    def test_numeric_version_order(self):
        self.assertTrue(newer('4.10', '4.9'))
        self.assertTrue(newer('5.0', '4.2'))
        self.assertFalse(newer('4.2', '4.2'))
        self.assertFalse(newer('4.1.9', '4.2'))


if __name__ == '__main__':
    unittest.main()
