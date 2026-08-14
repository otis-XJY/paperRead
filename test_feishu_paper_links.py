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
            "url": "https://proceedings.mlr.press/example.html",
        })
        serialized = str(blocks)
        self.assertIn("https://proceedings.mlr.press/example.html", serialized)
        self.assertIn("pmlr ICML", serialized)
        self.assertIn("LLM_Agent.Workflow.SearchOptimization", serialized)
        self.assertNotIn("arxiv.org/abs", serialized)


if __name__ == "__main__":
    unittest.main()
