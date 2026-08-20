import unittest

from notifier import (
    FEISHU_CARD_TEXT_LIMIT,
    NotificationManager,
    format_paper_source,
    split_feishu_card_text,
)


class NotificationTopicTests(unittest.TestCase):
    def test_paper_detail_card_includes_primary_topic(self):
        manager = NotificationManager.__new__(NotificationManager)
        sections = manager._build_paper_section({
            "title": "Skill-Based Agent Memory",
            "recommendation": "必读",
            "primary_topic": "LLM_Agent.MemoryToolSkill.SkillLearning",
        }, "LLM_Agent", 1, 1)

        self.assertIn(
            "LLM_Agent.MemoryToolSkill.SkillLearning",
            str(sections),
        )

    def test_paper_detail_card_includes_source_and_venue(self):
        manager = NotificationManager.__new__(NotificationManager)
        sections = manager._build_paper_section({
            "title": "A Conference Paper",
            "source": "acl_anthology",
            "venue": "EMNLP 2025",
            "url": "https://aclanthology.org/2025.emnlp-main.1/",
        }, "LLM_Agent", 1, 1)

        serialized = str(sections)
        self.assertIn("ACL Anthology | EMNLP 2025", serialized)
        self.assertEqual(
            format_paper_source({"source": "europe_pmc"}),
            "Europe PMC",
        )

    def test_paper_detail_card_preserves_full_analysis_text(self):
        manager = NotificationManager.__new__(NotificationManager)
        method = "method-" + ("a" * 500)
        motivation_core_idea = "motivation-" + ("m" * 500)
        comparison = "comparison-" + ("b" * 500)
        sharp_review = "review-" + ("c" * 500)
        sections = manager._build_paper_section({
            "title": "Long Analysis",
            "method": method,
            "motivation_core_idea": motivation_core_idea,
            "comparison": comparison,
            "sharp_review": sharp_review,
        }, "LLM_Agent", 1, 1)

        serialized = str(sections)
        self.assertIn(method, serialized)
        self.assertIn(motivation_core_idea, serialized)
        self.assertIn(comparison, serialized)
        self.assertIn(sharp_review, serialized)
        self.assertIn("Motivation & Core Idea", serialized)
        self.assertIn("Method", serialized)
        self.assertIn("Critical Review", serialized)

    def test_long_card_text_is_split_without_content_loss(self):
        content = ("A" * (FEISHU_CARD_TEXT_LIMIT - 1)) + "\n" + ("B" * 100)
        chunks = split_feishu_card_text(content)

        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(chunk) <= FEISHU_CARD_TEXT_LIMIT for chunk in chunks))
        self.assertEqual("".join(chunks), content)


if __name__ == "__main__":
    unittest.main()
