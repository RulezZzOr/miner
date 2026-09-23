"""Switch's selected worker reads pages without an unselected extraction model."""
import importlib
from unittest.mock import AsyncMock, patch


async def test_aligned_fetch_returns_bounded_page_without_summary_candidate(monkeypatch):
    from plugins.tools.web_fetch_aligned import _extract_info_with_llm
    monkeypatch.setenv("SWITCH_RAW_WEB_FETCH", "1")
    with patch("plugins.tools.web_fetch_aligned.summary_llm_candidates", side_effect=AssertionError("Unselected model")):
        result = await _extract_info_with_llm("PUBLIC_PAGE " * 5000, "Find the important detail")
    assert result["success"]
    assert "PUBLIC_PAGE" in result["extracted_info"]
    assert len(result["extracted_info"]) < 21000


async def test_plain_fetch_returns_page_without_auxiliary_llm(monkeypatch):
    web_fetch = importlib.import_module("plugins.tools.web_fetch")
    monkeypatch.setenv("SWITCH_RAW_WEB_FETCH", "1")
    content = "Public documentation describing a function. " * 500
    with patch.object(web_fetch, "non_public_url_error", new=AsyncMock(return_value=None)), \
         patch("plugins.tools.web_search.is_domain_blocked", return_value=False), \
         patch.object(web_fetch._scrape_cache, "check", return_value=None), \
         patch.object(web_fetch.scrape_result_cache, "get_or_scrape", new=AsyncMock(return_value=content)), \
         patch.object(web_fetch, "_summary_llm_summarize", new=AsyncMock(side_effect=AssertionError("Unselected model"))), \
         patch("plugins.tools._overflow.maybe_overflow", side_effect=lambda name, text: text[:20000]):
        result = await web_fetch._fetch_one("https://example.com/documentation", "Find the function")
    assert result.startswith("Public documentation")
