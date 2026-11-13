from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from chappie.config import Settings
from chappie.web_search import WebSearchEngine


def test_sanitize_query_strips_paths_and_tokens() -> None:
    engine = WebSearchEngine(Settings(enable_web_search=True))

    raw_query = (
        "Fix bug in /workspace/src/chappie/main.py and C:\\Users\\admin\\secret.txt "
        "using API_KEY=sk-abcdef1234567890123456 and token ghp_12345678901234567890 "
        "with env $DATABASE_URL and ${SECRET_PASS} on python 3.12"
    )
    cleaned = engine.sanitize_query(raw_query)

    assert "/workspace" not in cleaned
    assert "C:\\Users" not in cleaned
    assert "API_KEY" not in cleaned
    assert "sk-abcdef" not in cleaned
    assert "ghp_" not in cleaned
    assert "$DATABASE_URL" not in cleaned
    assert "${SECRET_PASS}" not in cleaned
    assert "Fix bug in" in cleaned
    assert "on python 3.12" in cleaned


def test_sanitize_query_preserves_legitimate_queries() -> None:
    engine = WebSearchEngine(Settings(enable_web_search=True))
    query = "How to configure FastAPI with Pydantic v2 and SQLAlchemy 2.0"
    assert engine.sanitize_query(query) == query


@pytest.mark.asyncio
async def test_web_search_disabled_by_default() -> None:
    engine = WebSearchEngine(Settings(enable_web_search=False))
    results, audit = await engine.search("FastAPI documentation")

    assert results == []
    assert audit.status == "disabled"
    assert audit.results_count == 0
    assert "disabled" in (audit.error_message or "").lower()


@pytest.mark.asyncio
async def test_web_search_empty_after_sanitization() -> None:
    engine = WebSearchEngine(Settings(enable_web_search=True))
    results, audit = await engine.search("/workspace/secret.env $API_KEY")

    assert results == []
    assert audit.status == "empty"
    assert audit.results_count == 0


@pytest.mark.asyncio
async def test_web_search_success_with_snippets_and_excerpts() -> None:
    engine = WebSearchEngine(Settings(enable_web_search=True, web_search_max_results=2))

    fake_item1 = MagicMock()
    fake_item1.url = "https://docs.pydantic.dev/latest/"
    fake_item1.title = "Pydantic Documentation"
    fake_item1.description = "Data validation using Python type hints."

    fake_item2 = MagicMock()
    fake_item2.url = "https://fastapi.tiangolo.com/"
    fake_item2.title = "FastAPI framework"
    fake_item2.description = "FastAPI framework, high performance."

    fake_html = """
    <html>
        <head><title>Pydantic</title><style>body { color: red; }</style></head>
        <body>
            <script>alert(1);</script>
            <header><nav>Menu</nav></header>
            <p>Pydantic is the most widely used data validation library for Python.</p>
            <footer>Footer notes</footer>
        </body>
    </html>
    """

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = fake_html

    with (
        patch.object(engine, "_sync_google_search", return_value=[fake_item1, fake_item2]),
        patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=mock_resp),
    ):
        results, audit = await engine.search("Pydantic v2 documentation")

    assert len(results) == 2
    assert audit.status == "success"
    assert audit.results_count == 2
    assert "https://docs.pydantic.dev/latest/" in audit.urls_contacted
    assert results[0].title == "Pydantic Documentation"
    assert "Pydantic is the most widely used" in results[0].content_excerpt
    # Script and nav should be stripped by BeautifulSoup
    assert "alert" not in results[0].content_excerpt
    assert "Menu" not in results[0].content_excerpt


@pytest.mark.asyncio
async def test_web_search_network_unreachable_fallback() -> None:
    engine = WebSearchEngine(Settings(enable_web_search=True))

    with patch.object(
        engine,
        "_sync_google_search",
        side_effect=OSError("Network is unreachable"),
    ):
        results, audit = await engine.search("langgraph tutorial")

    assert results == []
    assert audit.status == "network_unreachable"
    assert "Network is unreachable" in (audit.error_message or "")


@pytest.mark.asyncio
async def test_web_search_excerpt_http_failure_retains_snippet() -> None:
    engine = WebSearchEngine(Settings(enable_web_search=True, web_search_max_results=1))

    fake_item = MagicMock()
    fake_item.url = "https://example.com/unavailable"
    fake_item.title = "Example Error"
    fake_item.description = "Useful snippet from google search."

    with (
        patch.object(engine, "_sync_google_search", return_value=[fake_item]),
        patch("httpx.AsyncClient.get", new_callable=AsyncMock, side_effect=Exception("HTTP 500")),
    ):
        results, audit = await engine.search(".env query")

    assert len(results) == 1
    assert audit.status == "success"
    assert results[0].snippet == "Useful snippet from google search."
    assert results[0].content_excerpt == "Useful snippet from google search."
