import unittest

from paper_progress import advance_contiguous_cursor


class PaperCursorTests(unittest.TestCase):
    def test_later_success_does_not_jump_over_failed_paper(self):
        candidates = [
            {"id": "paper-a", "published": "2026-07-24T10:00:00+00:00"},
            {"id": "paper-b", "published": "2026-07-24T11:00:00+00:00"},
        ]
        cursor = advance_contiguous_cursor(
            "2026-07-24T09:00:00+00:00",
            candidates,
            ["paper-b"],
        )
        self.assertEqual(cursor, "2026-07-24T09:00:00Z")

    def test_cursor_advances_after_gap_is_completed(self):
        candidates = [
            {"id": "paper-a", "published": "2026-07-24T10:00:00+00:00"},
            {"id": "paper-b", "published": "2026-07-24T11:00:00+00:00"},
        ]
        cursor = advance_contiguous_cursor(
            "2026-07-24T09:00:00+00:00",
            candidates,
            ["paper-a", "paper-b"],
        )
        self.assertEqual(cursor, "2026-07-24T11:00:00Z")


if __name__ == "__main__":
    unittest.main()
