import unittest

from notifier import NotificationManager, format_paper_source


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


if __name__ == "__main__":
    unittest.main()
