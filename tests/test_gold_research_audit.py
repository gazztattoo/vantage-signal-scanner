import csv
import tempfile
import unittest
from pathlib import Path

from research.audit_gold_csv import audit_csv


class GoldCsvAuditTests(unittest.TestCase):
    def _write(self, rows):
        temp = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, newline="", encoding="utf-8")
        writer = csv.writer(temp)
        writer.writerow(["timestamp", "open", "high", "low", "close"])
        writer.writerows(rows)
        temp.close()
        self.addCleanup(lambda: Path(temp.name).unlink(missing_ok=True))
        return temp.name

    def test_audit_counts_valid_rows_and_reports_gap(self):
        rows = []
        for i in range(1000):
            minute = i * 5
            # Unix timestamps, with one deliberate missing 5-minute interval.
            stamp = 1_700_000_000 + minute * 60
            if i >= 500:
                stamp += 300
            rows.append([stamp, 2000, 2002, 1999, 2001])
        result = audit_csv(self._write(rows), 5)
        self.assertEqual(result["rows_unique"], 1000)
        self.assertEqual(result["invalid_rows"], 0)
        self.assertGreaterEqual(result["gap_count"], 1)

    def test_duplicate_and_invalid_rows_are_not_silently_accepted(self):
        rows = [
            [1_700_000_000, 2000, 2002, 1999, 2001],
            [1_700_000_000, 2000, 2002, 1999, 2001],
            [1_700_000_300, 2000, 1990, 1999, 2001],
        ]
        result = audit_csv(self._write(rows), 5)
        self.assertEqual(result["duplicate_timestamps"], 1)
        self.assertEqual(result["invalid_rows"], 1)
        self.assertEqual(result["status"], "REVIEW")

    def test_rejects_timezone_naive_timestamps(self):
        path = self._write([["2026-01-01 00:00:00", 2000, 2002, 1999, 2001]])
        result = audit_csv(path, 5)
        self.assertEqual(result["invalid_rows"], 1)
        self.assertEqual(result["rows_unique"], 0)


if __name__ == "__main__":
    unittest.main()
