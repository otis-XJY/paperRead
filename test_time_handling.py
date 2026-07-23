import os
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from time_utils import format_utc_timestamp, newer_timestamp
from notifier import format_report_time


class TimeHandlingTests(unittest.TestCase):
    def test_notification_time_uses_report_timezone(self):
        utc_time = datetime(2026, 7, 23, 3, 21, 52, tzinfo=timezone.utc)
        with patch.dict(os.environ, {"REPORT_TIMEZONE": "Asia/Shanghai"}):
            self.assertEqual(format_report_time(utc_time), "2026-07-23 11:21:52")

    def test_incremental_timestamps_compare_instants(self):
        self.assertFalse(
            newer_timestamp(
                "2026-07-23T03:21:52Z",
                "2026-07-23T11:30:00+08:00",
            )
        )
        self.assertTrue(
            newer_timestamp(
                "2026-07-23T11:21:52+08:00",
                "2026-07-23T03:21:51Z",
            )
        )

    def test_state_timestamp_is_canonical_utc(self):
        self.assertEqual(
            format_utc_timestamp("2026-07-23T11:21:52+08:00"),
            "2026-07-23T03:21:52Z",
        )
        self.assertEqual(
            format_utc_timestamp("2026-07-23"),
            "2026-07-23T00:00:00Z",
        )


if __name__ == "__main__":
    unittest.main()
