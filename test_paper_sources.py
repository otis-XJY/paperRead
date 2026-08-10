import asyncio
import unittest
from unittest.mock import patch

from paper_sources import (
    FINAL_CANDIDATE_LIMIT,
    PUBLIC_SOURCE_CONFIG,
    candidate_history_keys,
    fetch_public_sources,
    merge_candidates,
    normalize_candidate,
    rank_candidates,
)


def candidate(source, source_id, title="A Language Agent", **extra):
    data = {
        "source": source,
        "source_id": source_id,
        "title": title,
        "summary": "A large language model agent uses memory and tools.",
        "published": "2026-07-01T00:00:00Z",
        "url": "https://example.org/%s/%s" % (source, source_id),
    }
    data.update(extra)
    return normalize_candidate(data)


class PaperSourceTests(unittest.TestCase):
    def test_normalized_schema_and_legacy_arxiv_history_alias(self):
        paper = normalize_candidate({"id": "2601.12345v2", "title": "Paper"}, "arxiv")
        self.assertEqual(paper["id"], "arxiv:2601.12345")
        self.assertEqual(paper["external_ids"]["arxiv"], "2601.12345")
        self.assertIn("2601.12345", candidate_history_keys(paper))

    def test_dedup_prefers_doi_before_title_similarity(self):
        left = candidate("pmlr", "p1", doi="10.1000/example")
        right = candidate("acl_anthology", "a1", title="Unrelated title", doi="10.1000/example")
        merged = merge_candidates([left, right])
        self.assertEqual(len(merged), 1)
        self.assertEqual(set(merged[0]["source_meta"]["matched_sources"]), {"pmlr", "acl_anthology"})

    def test_title_similarity_deduplicates_cross_source_records(self):
        merged = merge_candidates([
            candidate("cvf", "one", "Learning Agent Memory for Long Tasks"),
            candidate("openreview", "two", "Learning Agent Memory for Long-Term Tasks"),
        ])
        self.assertEqual(len(merged), 1)

    def test_quality_interest_ranking_is_capped_at_twenty(self):
        category = {"discovery_queries": ["language agent memory", "tool use"]}
        papers = [
            candidate("pmlr", "p%s" % index, "Language Agent Memory " + ("topic%s " % index) * (index + 1))
            for index in range(30)
        ]
        ranked = rank_candidates(papers, category)
        self.assertEqual(len(ranked), FINAL_CANDIDATE_LIMIT)
        self.assertTrue(all(paper["source_meta"]["discovery_score"] >= 30 for paper in ranked))

    def test_public_openreview_submission_without_decision_is_retained(self):
        category = {"discovery_queries": ["language agent memory"]}
        paper = candidate("openreview", "forum-id", source_meta={"decision": ""})
        self.assertEqual(len(rank_candidates([paper], category)), 1)

    def test_failed_source_has_no_cursor_and_does_not_block_other_source(self):
        original = {key: dict(value) for key, value in PUBLIC_SOURCE_CONFIG.items()}
        try:
            for key in PUBLIC_SOURCE_CONFIG:
                PUBLIC_SOURCE_CONFIG[key]["enabled"] = key in {"openreview", "europe_pmc"}

            async def failed(*args, **kwargs):
                raise RuntimeError("fixture failure")

            async def successful(*args, **kwargs):
                return [candidate("europe_pmc", "pmc-1")], "2026-07-01T00:00:00Z"

            with patch("paper_sources.fetch_openreview", failed), patch("paper_sources.fetch_europe_pmc", successful):
                papers, cursors, failures = asyncio.run(fetch_public_sources(
                    object(), {"discovery_queries": ["language agent"]},
                    {"last_date": "2026-01-01T00:00:00Z"}, "fixture", False,
                ))
            self.assertEqual(len(papers), 1)
            self.assertEqual(cursors, {"europe_pmc": "2026-07-01T00:00:00Z"})
            self.assertEqual(failures, ["openreview"])
        finally:
            PUBLIC_SOURCE_CONFIG.clear()
            PUBLIC_SOURCE_CONFIG.update(original)


if __name__ == "__main__":
    unittest.main()
