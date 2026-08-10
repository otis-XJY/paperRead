import asyncio
import unittest
from unittest.mock import patch

from bs4 import BeautifulSoup

from paper_sources import (
    FINAL_CANDIDATE_LIMIT,
    PUBLIC_SOURCE_CONFIG,
    candidate_history_keys,
    fetch_public_sources,
    merge_candidates,
    normalize_candidate,
    _page_entries,
    _get_text,
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

    def test_cvf_ecva_entry_uses_the_official_eccv_landing_page(self):
        soup = BeautifulSoup(
            '<dt class="ptitle"><a href="papers/eccv_2024/papers_ECCV/html/6_ECCV_2024_paper.php">'
            'Octopus: Embodied Vision-Language Programmer</a></dt>',
            "html.parser",
        )
        papers = _page_entries(soup, "https://www.ecva.net/papers.php", "cvf", "ECCV2024")
        self.assertEqual(len(papers), 1)
        self.assertIn("eccv_2024", papers[0]["url"])

    def test_http_403_source_is_circuit_broken_for_the_current_run(self):
        original = {key: dict(value) for key, value in PUBLIC_SOURCE_CONFIG.items()}
        try:
            for key in PUBLIC_SOURCE_CONFIG:
                PUBLIC_SOURCE_CONFIG[key]["enabled"] = key == "openreview"
            calls = []

            async def blocked(*args, **kwargs):
                calls.append(1)
                raise RuntimeError("HTTP 403 for fixture")

            class Session:
                pass

            session = Session()
            with patch("paper_sources.fetch_openreview", blocked):
                first = asyncio.run(fetch_public_sources(session, {}, {}, "one", False))
                second = asyncio.run(fetch_public_sources(session, {}, {}, "two", False))
            self.assertEqual(first[2], ["openreview"])
            self.assertEqual(second[2], [])
            self.assertEqual(len(calls), 1)
        finally:
            PUBLIC_SOURCE_CONFIG.clear()
            PUBLIC_SOURCE_CONFIG.update(original)

    def test_openreview_403_is_retried_before_circuit_breaking(self):
        class Response:
            status = 403
            url = "https://api2.openreview.net/notes"

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, traceback):
                return False

        class Session:
            def __init__(self):
                self.calls = 0

            def get(self, *args, **kwargs):
                self.calls += 1
                return Response()

        async def no_sleep(*args, **kwargs):
            return None

        session = Session()
        with patch("paper_sources.asyncio.sleep", no_sleep):
            with self.assertRaisesRegex(RuntimeError, "after 3 attempts"):
                asyncio.run(_get_text(session, "https://api2.openreview.net/notes"))
        self.assertEqual(session.calls, 3)


if __name__ == "__main__":
    unittest.main()
