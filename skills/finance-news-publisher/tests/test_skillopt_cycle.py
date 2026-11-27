import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('skillopt_cycle', Path(__file__).parents[1] / 'scripts/skillopt_cycle.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class AdoptionTests(unittest.TestCase):
    def test_identity_and_shared_contract_cannot_disappear(self):
        before = 'name: example\ndescription: Example\nreferences/contract.md\n'
        m.candidate_ok(before, before + 'Use shared evolution rules.\n')
        with self.assertRaises(ValueError):
            m.candidate_ok(before, before.replace('name: example', 'name: other'))
        with self.assertRaises(ValueError):
            m.candidate_ok(before, before.replace('references/contract.md', ''))

    def test_repeated_rules_and_unbounded_growth_are_rejected(self):
        before = 'name: example\ndescription: Example\nreferences/contract.md\n'
        with self.assertRaises(ValueError):
            m.candidate_ok(before, before + ('Do not duplicate the same semantic rule.\n' * 2))
        with self.assertRaises(ValueError):
            m.candidate_ok(before, before + 'x' * 2000)

    def test_final_test_must_be_present_and_complete(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'evidence.jsonl'
            p.write_text('{}\n')
            with self.assertRaises(ValueError):
                m.test_measure(d, 1)
            row = dict(stage='test', event='held_out_score', n_test=1, hard=1.0, soft=1.0)
            p.write_text(json.dumps(row) + '\n')
            self.assertEqual(1.0, m.test_measure(d, 1)['hard'])
            with self.assertRaises(ValueError):
                m.test_measure(d, 2)


if __name__ == '__main__':
    unittest.main()
