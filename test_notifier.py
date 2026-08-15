import unittest

from notifier import NotificationManager


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


if __name__ == "__main__":
    unittest.main()
