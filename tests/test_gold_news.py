import unittest
import requests
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import gold_news


class GoldNewsContextTests(unittest.TestCase):
    def setUp(self):
        gold_news._CACHE.update(at=0.0, events=[], verified=False, error="not_loaded")

    def test_calendar_unavailable_does_not_block_technical_signals(self):
        with patch.object(gold_news.requests, "get", side_effect=requests.Timeout):
            context = gold_news.gold_news_context(datetime.now(timezone.utc))
        self.assertFalse(context["verified"])
        self.assertFalse(context["block_new_entries"])
        self.assertIsNone(context["block_reason"])

    def test_verified_high_impact_event_temporarily_blocks_entries(self):
        now = datetime.now(timezone.utc)
        event = now + timedelta(minutes=10)
        ics = (
            "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\n"
            f"DTSTART:{event.strftime('%Y%m%dT%H%M%SZ')}\r\n"
            "SUMMARY:US CPI\r\nEND:VEVENT\r\nEND:VCALENDAR"
        )

        class Response:
            text = ics
            def raise_for_status(self):
                return None

        with patch.object(gold_news.requests, "get", return_value=Response()):
            context = gold_news.gold_news_context(now)
        self.assertTrue(context["verified"])
        self.assertTrue(context["block_new_entries"])
        self.assertEqual(context["block_reason"], "HIGH_IMPACT_NEWS_WINDOW")

    def test_verified_calendar_without_nearby_events_does_not_block(self):
        now = datetime.now(timezone.utc)
        event = now + timedelta(days=2)
        ics = (
            "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\n"
            f"DTSTART:{event.strftime('%Y%m%dT%H%M%SZ')}\r\n"
            "SUMMARY:US CPI\r\nEND:VEVENT\r\nEND:VCALENDAR"
        )

        class Response:
            text = ics
            def raise_for_status(self):
                return None

        with patch.object(gold_news.requests, "get", return_value=Response()):
            context = gold_news.gold_news_context(now)
        self.assertTrue(context["verified"])
        self.assertFalse(context["block_new_entries"])


if __name__ == "__main__":
    unittest.main()
