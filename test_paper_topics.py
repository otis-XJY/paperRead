import unittest

from paper_topics import normalize_primary_topic, topic_options_for_category


class PaperTopicTests(unittest.TestCase):
    def test_each_configured_category_has_taxonomy_options(self):
        categories = [
            "UAV_VLN", "multi_VLN", "MultiAgent_Game_Theory", "MARL",
            "LLM_Agent_Memory_Tool_Skill", "LLM_Agent_Self_Evolution",
            "LLM_Agent_Workflow_Long_Horizon",
            "Multi_LLM_Agent_Memory_Tool_Skill",
            "Multi_LLM_Agent_Collaboration_Communication",
            "Multi_LLM_Agent_Evolution",
        ]
        for category in categories:
            self.assertTrue(topic_options_for_category(category), category)

    def test_primary_topic_is_limited_to_its_category_taxonomy(self):
        valid = "Multi_LLM_Agent.Topology.TopologyOptimization"
        self.assertEqual(
            normalize_primary_topic(valid, "Multi_LLM_Agent_Collaboration_Communication"),
            valid,
        )
        self.assertEqual(
            normalize_primary_topic(valid, "LLM_Agent_Self_Evolution"),
            "Unclassified",
        )
        self.assertEqual(
            normalize_primary_topic(
                "multi_llm_agent.topology.topology_optimization",
                "Multi_LLM_Agent_Collaboration_Communication",
            ),
            valid,
        )


if __name__ == "__main__":
    unittest.main()
