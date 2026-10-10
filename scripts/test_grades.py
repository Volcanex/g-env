"""Grades read off a fixture ladder."""
from pathlib import Path
import sys
import unittest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'context'))
import grades

DATA = {
    'models': [{'id': 'opus-5.5', 'scores': {'DA': 1480}}, {'id': 'sonnet-5.5', 'scores': {'DA': 1400}},
               {'id': 'gpt-6.1-sol', 'scores': {'DA': 1236}}],
    'effort_ladder': [
        {'model': 'opus-5.5', 'effort': 'max', 'score': 58, 'cost_usd': 5.98, 'ttft_s': 716},
        {'model': 'opus-5.5', 'effort': 'high', 'score': 54, 'cost_usd': 1.82, 'ttft_s': 34},
        {'model': 'sonnet-5.5', 'effort': 'medium', 'score': 41, 'cost_usd': 0.59, 'ttft_s': 1}],
    'codex_ladder': [
        {'model': 'gpt-6.1-sol', 'effort': 'medium', 'score': 48, 'cost_usd': 0.21, 'ttft_s': 5},
        {'model': 'gpt-6-luna', 'effort': 'medium', 'score': 30, 'cost_usd': 0.02, 'ttft_s': None},
        {'model': 'gpt-6-luna', 'effort': 'high', 'score': 33, 'cost_usd': 0.03, 'ttft_s': None, 'ttft_estimate_s': 14}],
}


class GradeTest(unittest.TestCase):
    def test_table(self):
        table = {point: row['grade'] for point, row in grades.table(DATA).items()}
        self.assertEqual(table, {'opus-max': 'A9X∴', 'opus-high': 'A8S∴', 'sonnet-medium': 'D6F:',
                                 'sol-medium': 'C4F.', 'luna-medium': 'F1??', 'luna-high': 'F2M?'})

    def test_providers(self):
        table = grades.table(DATA)
        self.assertEqual(table['opus-high']['provider'], 'claude')
        self.assertEqual(table['sol-medium']['provider'], 'codex')

    def test_bounds(self):
        self.assertEqual(grades.cost(0.001), '0')
        self.assertEqual(grades.cost(100), '9')
        self.assertEqual(grades.quality(10, 58), 'F')
        self.assertEqual(grades.speed(121), 'X')


if __name__ == '__main__':
    unittest.main()
