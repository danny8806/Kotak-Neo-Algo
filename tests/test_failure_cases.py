"""Fail-closed tests for the live signal contract."""
import copy
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from validate_signals import validate

IST = ZoneInfo("Asia/Kolkata")


def ready_fixture(day="2026-11-03"):
    return {
        "schema_version": "1.0", "status": "READY", "market": "NSE",
        "timezone": "Asia/Kolkata", "session_date": day,
        "news_cutoff_at": day + "T09:00:00+05:30",
        "generated_at": day + "T09:01:00+05:30", "execution_enabled": False,
        "signals": [{
            "symbol": "TESTA", "side": "LONG", "rank": 1,
            "reason": "Synthetic positive financial report for unit testing.",
            "news_summary": "Fictional unit test event; no trade signal.",
            "first_available_at": "2026-11-02T16:15:00+05:30",
            "sources": [{"url": "https://www.nseindia.com/companies-listing/corporate-filings-announcements",
                         "title": "Test-only source reference",
                         "published_at": "2026-11-02T16:15:00+05:30"}],
            "confidence": None,
        }],
    }


class ValidatorFailureCases(unittest.TestCase):
    def setUp(self):
        self.good = ready_fixture()

    def assert_rejected(self, mutator, message=None):
        payload = copy.deepcopy(self.good)
        mutator(payload)
        with self.assertRaises((ValueError, TypeError, KeyError, AttributeError)) as caught:
            validate(payload)
        if message:
            self.assertIn(message, str(caught.exception))

    def test_baseline_ready_passes_format_only(self):
        validate(copy.deepcopy(self.good))

    def test_reject_unsupported_schema(self):
        self.assert_rejected(lambda p: p.update(schema_version="2.0"), "schema version")

    def test_reject_missing_fields(self):
        for field in ("schema_version", "status", "market", "timezone", "session_date",
                      "news_cutoff_at", "generated_at", "execution_enabled", "signals"):
            with self.subTest(field=field):
                self.assert_rejected(lambda p: p.pop(field), "Missing")

    def test_reject_extra_field(self):
        self.assert_rejected(lambda p: p.update(broker_api_key="abc"), "Unexpected")

    def test_reject_wrong_market_timezone(self):
        for field, value in (("market", "BSE"), ("timezone", "UTC")):
            with self.subTest(field=field):
                self.assert_rejected(lambda p: p.update({field: value}))

    def test_reject_unknown_status_and_live_execution(self):
        self.assert_rejected(lambda p: p.update(status="ORDER_NOW"), "Invalid status")
        self.assert_rejected(lambda p: p.update(execution_enabled=True), "Live execution")

    def test_reject_sample(self):
        self.assert_rejected(lambda p: p.update(sample=True), "sample")

    def test_reject_missing_ready_candidates(self):
        self.assert_rejected(lambda p: p.update(signals=[]), "READY requires")

    def test_nonready_requires_reason(self):
        self.assert_rejected(lambda p: p.update(status="NO_TRADE", signals=[]), "reason")

    def test_candidates_are_rejected_for_nonready_status(self):
        for status in ("NO_TRADE", "UNAVAILABLE"):
            self.assert_rejected(lambda p: p.update(status=status, reason="No candidates"),
                                 "may not contain candidates")

    def test_valid_no_trade_passes(self):
        payload = copy.deepcopy(self.good)
        payload.update(status="NO_TRADE", signals=[], reason="Nothing qualifies today")
        validate(payload)

    def test_reject_wrong_session(self):
        with self.assertRaisesRegex(ValueError, "differs"):
            validate(self.good, expected_date="2026-11-04")

    def test_reject_bad_date_cutoff_and_naive_timestamps(self):
        self.assert_rejected(lambda p: p.update(session_date="2026-02-30"), "Invalid session")
        self.assert_rejected(lambda p: p.update(news_cutoff_at="2026-11-02T15:00:00+05:30"), "session date")
        for field in ("news_cutoff_at", "generated_at"):
            self.assert_rejected(lambda p: p.update({field: "2026-11-03T09:00:00"}), "timezone")

    def test_intraday_cutoff_accepts_new_session_evidence(self):
        payload = copy.deepcopy(self.good)
        payload["news_cutoff_at"] = "2026-11-03T10:00:00+05:30"
        payload["generated_at"] = "2026-11-03T10:01:00+05:30"
        payload["signals"][0]["first_available_at"] = "2026-11-03T09:30:00+05:30"
        payload["signals"][0]["sources"][0]["published_at"] = "2026-11-03T09:30:00+05:30"
        validate(payload)

    def test_off_session_cutoffs_are_allowed_for_mapping(self):
        for stamp in ("2026-11-03T00:30:00+05:30", "2026-11-03T23:59:00+05:30"):
            with self.subTest(stamp=stamp):
                payload = copy.deepcopy(self.good)
                payload["news_cutoff_at"] = stamp
                payload["generated_at"] = stamp
                validate(payload)

    def test_reject_generated_before_cutoff_or_in_future(self):
        self.assert_rejected(lambda p: p.update(generated_at="2026-11-03T08:59:00+05:30"), "at or after")
        future = (datetime.now(IST) + timedelta(days=2)).isoformat()
        self.assert_rejected(lambda p: p.update(generated_at=future), "future")

    def test_reject_late_availability_publication_and_missing_sources(self):
        self.assert_rejected(lambda p: p["signals"][0].update(
            first_available_at="2026-11-03T09:01:00+05:30"), "not available")
        self.assert_rejected(lambda p: p["signals"][0]["sources"][0].update(
            published_at="2026-11-03T09:01:00+05:30"), "after research cutoff")
        self.assert_rejected(lambda p: p["signals"][0].update(sources=[]), "requires a cited source")

    def test_reject_unsafe_source_url(self):
        for url in ("http://example.org", "javascript:alert(1)", "https://example.com/fake", "https:///bad"):
            with self.subTest(url=url):
                self.assert_rejected(lambda p: p["signals"][0]["sources"][0].update(url=url))

    def test_reject_duplicate_stock_or_invalid_side(self):
        def duplicate(p):
            item = copy.deepcopy(p["signals"][0])
            item.update(side="SHORT", rank=1)
            p["signals"].append(item)
        self.assert_rejected(duplicate, "Duplicate symbol")
        self.assert_rejected(lambda p: p["signals"][0].update(side="BUY_AND_SELL"), "Invalid side")

    def test_reject_invalid_or_noncontiguous_ranks(self):
        for rank in (0, 2, 11, True, 1.0):
            with self.subTest(rank=rank):
                self.assert_rejected(lambda p: p["signals"][0].update(rank=rank))

    def test_reject_more_than_five_per_side_or_ten_total(self):
        def six_longs(p):
            base = p["signals"][0]
            for rank in range(2, 7):
                item = copy.deepcopy(base)
                item.update(symbol=f"TEST{rank}", rank=rank)
                p["signals"].append(item)
        self.assert_rejected(six_longs)
        def twenty(p):
            base = p["signals"][0]
            for index in range(2, 22):
                item = copy.deepcopy(base)
                item.update(symbol=f"TEST{index}", rank=1)
                p["signals"].append(item)
        self.assert_rejected(twenty, "Maximum ten")

    def test_reject_invalid_confidence_symbol_or_reason(self):
        for score in (-0.01, 1.01, "99%", True):
            self.assert_rejected(lambda p: p["signals"][0].update(confidence=score))
        self.assert_rejected(lambda p: p["signals"][0].update(symbol="INVALID SYMBOL"))
        self.assert_rejected(lambda p: p["signals"][0].update(reason="Good news"))

    def test_nonready_bootstrap_only_if_empty(self):
        payload = copy.deepcopy(self.good)
        payload.update(status="UNAVAILABLE", session_date=None, news_cutoff_at=None,
                       generated_at=None, signals=[], reason="Awaiting first run")
        validate(payload)
        payload["signals"] = copy.deepcopy(self.good["signals"])
        with self.assertRaises(ValueError):
            validate(payload)


if __name__ == "__main__":
    unittest.main()
