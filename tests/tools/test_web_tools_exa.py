"""Tests for Exa web backend helpers and dispatch."""

import json
import os
from unittest.mock import MagicMock, patch

import pytest


class TestNormalizeExaSearchResults:
    def test_maps_results_and_highlights(self):
        from tools.web_tools import _normalize_exa_search_results

        raw = {
            "results": [
                {
                    "title": "A",
                    "url": "https://a.example",
                    "highlights": ["one", "two"],
                }
            ]
        }
        out = _normalize_exa_search_results(raw)
        assert out["success"] is True
        assert out["data"]["web"][0]["title"] == "A"
        assert out["data"]["web"][0]["url"] == "https://a.example"
        assert "one" in out["data"]["web"][0]["description"]

    def test_summary_over_highlights(self):
        from tools.web_tools import _normalize_exa_search_results

        raw = {
            "results": [
                {"title": "T", "url": "https://u", "summary": "S", "highlights": ["h"]},
            ]
        }
        out = _normalize_exa_search_results(raw)
        assert out["data"]["web"][0]["description"] == "S"


class TestNormalizeExaDocuments:
    def test_orders_by_requested_urls(self):
        from tools.web_tools import _normalize_exa_documents

        raw = {
            "results": [
                {"url": "https://second", "text": "b", "title": "B"},
                {"url": "https://first", "text": "a", "title": "A"},
            ]
        }
        docs = _normalize_exa_documents(raw, ["https://first", "https://second"])
        assert [d["url"] for d in docs] == ["https://first", "https://second"]
        assert docs[0]["content"] == "a"


class TestWebSearchExa:
    def test_search_dispatches_to_exa(self):
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "results": [
                {
                    "title": "Hit",
                    "url": "https://hit.example",
                    "highlights": ["snippet"],
                }
            ]
        }
        mock_response.raise_for_status = MagicMock()

        with (
            patch("tools.web_tools._get_backend", return_value="exa"),
            patch.dict(os.environ, {"EXA_API_KEY": "exa-test"}),
            patch("tools.web_tools.httpx.post", return_value=mock_response),
            patch("tools.interrupt.is_interrupted", return_value=False),
        ):
            from tools.web_tools import web_search_tool

            result = json.loads(web_search_tool("q", limit=3))
            assert result["success"] is True
            assert result["data"]["web"][0]["title"] == "Hit"


class TestWebExtractExa:
    @pytest.mark.asyncio
    async def test_extract_dispatches_to_exa(self):
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "results": [
                {"url": "https://example.com", "text": "Body", "title": "Page"},
            ]
        }
        mock_response.raise_for_status = MagicMock()

        with (
            patch("tools.web_tools._get_backend", return_value="exa"),
            patch.dict(os.environ, {"EXA_API_KEY": "exa-test"}),
            patch("tools.web_tools.httpx.post", return_value=mock_response),
            patch("tools.web_tools.process_content_with_llm", return_value=None),
        ):
            from tools.web_tools import web_extract_tool

            result = json.loads(
                await web_extract_tool(
                    ["https://example.com"], use_llm_processing=False
                )
            )
            assert len(result["results"]) == 1
            assert result["results"][0]["url"] == "https://example.com"


class TestWebCrawlExa:
    @pytest.mark.asyncio
    async def test_crawl_rejects_exa_backend(self):
        with (
            patch("tools.web_tools._get_backend", return_value="exa"),
            patch.dict(os.environ, {"EXA_API_KEY": "exa-test"}),
        ):
            from tools.web_tools import web_crawl_tool

            result = json.loads(
                await web_crawl_tool("https://example.com", use_llm_processing=False)
            )
            assert result.get("success") is False
            assert "Exa" in result.get("error", "")
