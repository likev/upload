import json
from pathlib import Path

from twitter_article import _extract_article_from_graphql


FIXTURE_DIR = Path(__file__).parent / "fixtures"
FIXTURE_JSON = FIXTURE_DIR / "twitter_article_2033565474273828864.json"
BLOCK1_PATH = FIXTURE_DIR / "block1.txt"


def _normalize_text(text: str) -> str:
    if text is None:
        return ""
    replacements = {
        "\u2018": "'",
        "\u2019": "'",
        "\u201C": "\"",
        "\u201D": "\"",
        "\u00A0": " ",
    }
    for src, dst in replacements.items():
        text = text.replace(src, dst)
    return " ".join(text.split())


def _find_article_result(data: object) -> dict:
    if isinstance(data, dict):
        if "article_results" in data and isinstance(data.get("article_results"), dict):
            result = data["article_results"].get("result")
            if isinstance(result, dict):
                return result
        for value in data.values():
            found = _find_article_result(value)
            if found:
                return found
    elif isinstance(data, list):
        for item in data:
            found = _find_article_result(item)
            if found:
                return found
    return {}


def _get_first_block_text(article: dict) -> str:
    content_state = article.get("content_state")
    if not isinstance(content_state, dict):
        return ""
    blocks = content_state.get("blocks")
    if not isinstance(blocks, list) or not blocks:
        return ""
    first = blocks[0]
    if not isinstance(first, dict):
        return ""
    return first.get("text") or ""


def test_article_extraction_fixture():
    data = json.loads(FIXTURE_JSON.read_text(encoding="utf-8"))
    expected_title = 'It All Comes Down to Who Controls the Straight of Hormuz: The "Final Battle"'
    expected_block1 = BLOCK1_PATH.read_text(encoding="utf-8")

    title, _ = _extract_article_from_graphql(data["data"])
    assert _normalize_text(title) == _normalize_text(expected_title)

    article = _find_article_result(data["data"])
    assert article.get("rest_id") == "2033565474273828864"

    block1 = _get_first_block_text(article)
    assert _normalize_text(block1) == _normalize_text(expected_block1)
