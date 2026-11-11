"""Regression tests: only source-backed, time-valid, research-only signals pass."""
import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from validate_signals import validate

SAMPLE = {
    'schema_version': '1.0', 'status': 'READY', 'market': 'NSE',
    'timezone': 'Asia/Kolkata', 'session_date': '2026-11-03',
    'news_cutoff_at': '2026-11-03T09:00:00+05:30',
    'generated_at': '2026-11-03T09:01:00+05:30',
    'execution_enabled': False, 'sample': True,
    'signals': [{
        'symbol': 'EXAMPLE1', 'side': 'LONG', 'rank': 1,
        'reason': 'A hypothetical research catalyst only.',
        'news_summary': 'A fictional earnings report for testing.',
        'first_available_at': '2026-11-02T18:00:00+05:30',
        'sources': [{
            'url': 'https://example.com/fictional',
            'title': 'Fictional event',
            'published_at': '2026-11-02T18:00:00+05:30'
        }],
        'confidence': None
    }]
}


class SignalsTest(unittest.TestCase):
    def test_ten_research_candidates_are_supported(self):
        payload=copy.deepcopy(SAMPLE)
        payload["sample"]=True
        payload["signals"]=[]
        for side,prefix in (("LONG","LONG"),("SHORT","SHORT")):
            for rank in range(1,6):
                item=copy.deepcopy(SAMPLE["signals"][0])
                item.update(symbol=f"{prefix}{rank}",side=side,rank=rank)
                payload["signals"].append(item)
        validate(payload,allow_sample=True)

    def test_digit_leading_nse_symbol_still_requires_universe_membership(self):
        payload = copy.deepcopy(SAMPLE)
        payload["signals"][0]["symbol"] = "360ONE"
        validate(payload, allow_sample=True, allowed_symbols={"360ONE"})
        with self.assertRaisesRegex(ValueError, "outside the current official Nifty 500"):
            validate(payload, allow_sample=True, allowed_symbols={"TCS"})
        payload["signals"][0]["symbol"] = "360123"
        with self.assertRaisesRegex(ValueError, "Invalid NSE symbol"):
            validate(payload, allow_sample=True)

    def test_sample_valid_only_in_explicit_example_mode(self):
        validate(copy.deepcopy(SAMPLE), allow_sample=True)
        with self.assertRaisesRegex(ValueError, 'Example/sample'):
            validate(copy.deepcopy(SAMPLE))

    def test_execution_never_enabled(self):
        payload = copy.deepcopy(SAMPLE)
        payload['execution_enabled'] = True
        with self.assertRaisesRegex(ValueError, 'Live execution'):
            validate(payload, allow_sample=True)

    def test_reject_future_publication_at_cutoff(self):
        payload = copy.deepcopy(SAMPLE)
        payload['signals'][0]['sources'][0]['published_at'] = '2026-11-03T09:02:00+05:30'
        with self.assertRaisesRegex(ValueError, 'published after'):
            validate(payload, allow_sample=True)

    def test_reject_duplicate_symbol(self):
        payload = copy.deepcopy(SAMPLE)
        second = copy.deepcopy(payload['signals'][0])
        second['side'] = 'SHORT'
        payload['signals'].append(second)
        with self.assertRaisesRegex(ValueError, 'Duplicate symbol'):
            validate(payload, allow_sample=True)

    def test_configured_nifty500_universe_blocks_outside_symbols(self):
        payload = copy.deepcopy(SAMPLE)
        with self.assertRaisesRegex(ValueError, 'outside the current official Nifty 500'):
            validate(payload, allow_sample=True, allowed_symbols={'RELIANCE', 'TCS'})
        payload['signals'][0]['symbol'] = 'TCS'
        validate(payload, allow_sample=True, allowed_symbols={'RELIANCE', 'TCS'})

    def test_bootstrap_is_unavailable(self):
        payload = {
            'schema_version': '1.0', 'status': 'UNAVAILABLE', 'market': 'NSE',
            'timezone': 'Asia/Kolkata', 'session_date': None,
            'news_cutoff_at': None, 'generated_at': None,
            'execution_enabled': False, 'signals': [],
            'reason': 'No report'
        }
        validate(payload)


if __name__ == '__main__':
    unittest.main()
