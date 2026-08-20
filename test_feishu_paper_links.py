import unittest

from feishu_wiki import FeishuWikiClient


class FeishuPaperLinkTests(unittest.TestCase):
    def test_non_arxiv_paper_uses_its_source_link(self):
        client = FeishuWikiClient.__new__(FeishuWikiClient)
        blocks = client._paper_info_to_blocks({
            "title": "A PMLR Paper",
            "published": "2026-01-01",
            "source": "pmlr",
            "venue": "ICML",
            "primary_topic": "LLM_Agent.Workflow.SearchOptimization",
            "motivation_core_idea": "It addresses costly agent planning.",
            "method": "It introduces a reusable planning module.",
            "sharp_review": "The evaluation lacks long-horizon tasks.",
            "url": "https://proceedings.mlr.press/example.html",
        })
        serialized = str(blocks)
        self.assertIn("https://proceedings.mlr.press/example.html", serialized)
        self.assertIn("pmlr ICML", serialized)
        self.assertIn("LLM_Agent.Workflow.SearchOptimization", serialized)
        self.assertIn("Motivation & Core Idea", serialized)
        self.assertIn("It addresses costly agent planning.", serialized)
        self.assertIn("Method", serialized)
        self.assertIn("It introduces a reusable planning module.", serialized)
        self.assertIn("Critical Review", serialized)
        self.assertIn("The evaluation lacks long-horizon tasks.", serialized)
        self.assertNotIn("arxiv.org/abs", serialized)


if __name__ == "__main__":
    unittest.main()
