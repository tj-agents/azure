import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class DeclaredDependenciesTests(unittest.TestCase):
    def test_generated_selection_agrees_with_required_harness(self):
        marketplace = json.loads((ROOT / '.agents/plugins/marketplace.json').read_text(encoding='utf-8'))['name']
        paths = sorted((ROOT / 'plugins').glob('*/harness.json'))
        self.assertTrue(paths)
        for path in paths:
            harness = json.loads(path.read_text(encoding='utf-8'))
            selection = json.loads(path.with_name('selection.json').read_text(encoding='utf-8'))
            dependencies = {value if '/' in value else f'{marketplace}/{value}' for value in selection['dependencies']}
            self.assertEqual(set(harness['requires']['plugins']) - {harness['plugin']}, dependencies)


if __name__ == '__main__':
    unittest.main()
