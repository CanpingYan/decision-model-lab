from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'app'))
from lab.config import load_settings


class ConfigTest(unittest.TestCase):
    def test_quotes_comments_and_project_relative_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / '.env'
            path.write_text('API_KEY="secret # keep"\nLAYA_MODEL_DIR=.runtime/model\nLAB_PORT=9000 # note\n')
            settings = load_settings(path)
            self.assertEqual(settings['API_KEY'], 'secret # keep')
            self.assertEqual(settings['LAYA_MODEL_DIR'], str(ROOT / '.runtime/model'))
            self.assertEqual(settings['LAB_PORT'], '9000')

    def test_no_shell_execution_or_implicit_environment_key_disclosure(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / '.env'
            path.write_text('API_KEY="$(echo unsafe)"\n')
            settings = load_settings(path)
            self.assertEqual(settings['API_KEY'], '$(echo unsafe)')
            self.assertNotIn('DEEPSEEK_API_KEY', settings)


if __name__ == '__main__':
    unittest.main()
