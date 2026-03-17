import html
import json
import os
import subprocess
import urllib.parse
from html.parser import HTMLParser
from typing import Optional, Tuple


ARTICLE_TESTID = "twitterArticleRichTextView"
TITLE_TESTID = "twitter-article-title"


def is_twitter_article_url(raw_url: str) -> bool:
    try:
        parsed = urllib.parse.urlparse(raw_url)
    except ValueError:
        return False
    host = (parsed.netloc or "").lower()
    if not (host.endswith("twitter.com") or host.endswith("x.com")):
        return False
    parts = [p for p in (parsed.path or "").split("/") if p]
    return any(part in {"articles", "article", "status"} for part in parts)


def suggested_filename(raw_url: str) -> Optional[str]:
    try:
        parsed = urllib.parse.urlparse(raw_url)
    except ValueError:
        return None
    parts = [p for p in (parsed.path or "").split("/") if p]
    for key, prefix in (("articles", "twitter-article"), ("article", "twitter-article"), ("status", "twitter-article")):
        if key in parts:
            idx = parts.index(key)
            if idx + 1 < len(parts):
                slug = parts[idx + 1]
                if slug:
                    return f"{prefix}-{slug}.md"
    return None


def fetch_article_markdown(raw_url: str) -> Optional[str]:
    payload = _fetch_via_node(raw_url)
    if not payload:
        return None

    data = _parse_json_payload(payload)
    if data:
        if data.get("html"):
            title, body = _extract_article(data["html"])
            if title or body:
                return _build_markdown(title, body)
        if data.get("data"):
            title, body = _extract_article_from_graphql(data["data"])
            if title or body:
                return _build_markdown(title, body)
        markdown = data.get("markdown")
        if markdown:
            return _ensure_trailing_newline(markdown)
        title = (data.get("title") or "").strip()
        body = (data.get("body") or data.get("text") or "").strip()
        if title or body:
            return _build_markdown(title, body)

    title, body = _extract_article(payload)
    if not title and not body:
        return None
    return _build_markdown(title, body)


def _fetch_via_node(raw_url: str) -> Optional[str]:
    script_path = os.path.join(os.path.dirname(__file__), "scripts", "twitter_article_fetch.mjs")
    if not os.path.exists(script_path):
        return None
    try:
        result = subprocess.run(
            ["node", script_path, raw_url],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
            env=os.environ,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout


def _parse_json_payload(payload: str) -> Optional[dict]:
    if not payload:
        return None
    lines = [line for line in payload.splitlines() if line.strip()]
    for line in reversed(lines):
        trimmed = line.lstrip()
        if not trimmed.startswith("{"):
            continue
        try:
            data = json.loads(trimmed)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data
    return None


def _build_markdown(title: str, body: str) -> Optional[str]:
    title = (title or "").strip()
    body = (body or "").strip()
    if not title and not body:
        return None
    parts = []
    if title:
        parts.append("# " + title)
    if body:
        parts.append(body)
    return "\n\n".join(parts) + "\n"


def _ensure_trailing_newline(text: str) -> str:
    if text.endswith("\n"):
        return text
    return text + "\n"


class _TwitterArticleExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._title_depth = 0
        self._article_depth = 0
        self._title_parts = []
        self._article_lines = []
        self._article_buf = []
        self._in_link = False
        self._link_href = None
        self._link_buf = []

    @property
    def title(self) -> str:
        return html.unescape("".join(self._title_parts)).strip()

    @property
    def body(self) -> str:
        self._flush_line()
        return "\n\n".join(self._article_lines).strip()

    def handle_starttag(self, tag: str, attrs) -> None:
        attrs_dict = dict(attrs)
        testid = attrs_dict.get("data-testid")

        if testid == TITLE_TESTID and self._title_depth == 0:
            self._title_depth = 1
        elif self._title_depth > 0:
            self._title_depth += 1

        if testid == ARTICLE_TESTID and self._article_depth == 0:
            self._article_depth = 1
        elif self._article_depth > 0:
            self._article_depth += 1

        if self._article_depth > 0:
            if tag in {"p", "div", "section", "article", "blockquote", "ul", "ol", "li"}:
                self._flush_line()
            if tag == "br":
                self._flush_line()
            if tag == "li":
                self._append_article("- ")
            if tag == "a":
                self._in_link = True
                self._link_href = attrs_dict.get("href")
                self._link_buf = []

    def handle_endtag(self, tag: str) -> None:
        if self._in_link and tag == "a":
            text = _clean_text("".join(self._link_buf))
            if text:
                if self._link_href:
                    self._append_article(f"[{text}]({self._link_href})")
                else:
                    self._append_article(text)
            self._in_link = False
            self._link_href = None
            self._link_buf = []

        if self._title_depth > 0:
            self._title_depth -= 1

        if self._article_depth > 0:
            self._article_depth -= 1
            if tag in {"p", "div", "section", "article", "blockquote", "li"}:
                self._flush_line()

    def handle_data(self, data: str) -> None:
        if self._title_depth > 0:
            self._title_parts.append(data)
        if self._article_depth > 0:
            if self._in_link:
                self._link_buf.append(data)
            else:
                self._append_article(data)

    def _append_article(self, text: str) -> None:
        cleaned = _clean_text(text)
        if not cleaned:
            return
        if self._article_buf and not self._article_buf[-1].endswith((" ", "\n")):
            self._article_buf.append(" ")
        self._article_buf.append(cleaned)

    def _flush_line(self) -> None:
        if not self._article_buf:
            return
        line = "".join(self._article_buf).strip()
        if line:
            self._article_lines.append(line)
        self._article_buf = []


def _clean_text(text: str) -> str:
    return " ".join(html.unescape(text).split())


def _extract_article(html_text: str) -> Tuple[str, str]:
    parser = _TwitterArticleExtractor()
    parser.feed(html_text)
    return parser.title, parser.body


def _extract_article_from_graphql(data: object) -> Tuple[str, str]:
    best_title = ""
    best_body = ""

    def consider(title: Optional[str], body: Optional[str]) -> None:
        nonlocal best_title, best_body
        title = (title or "").strip()
        body = (body or "").strip()
        if not body and not title:
            return
        if len(body) > len(best_body):
            best_title = title or best_title
            best_body = body

    def consider_article(article: dict) -> None:
        title = article.get("title") if isinstance(article, dict) else None
        body = ""
        if isinstance(article, dict):
            content_state = article.get("content_state")
            if isinstance(content_state, dict):
                blocks = content_state.get("blocks")
                if isinstance(blocks, list):
                    parts = []
                    for block in blocks:
                        if not isinstance(block, dict):
                            continue
                        text = (block.get("text") or "").strip()
                        if text:
                            parts.append(text)
                    body = "\n\n".join(parts)
            if not body:
                body = (article.get("text") or article.get("body") or "").strip()
        consider(title, body)

    def visit(obj: object) -> None:
        if isinstance(obj, dict):
            if "article_results" in obj and isinstance(obj.get("article_results"), dict):
                result = obj.get("article_results", {}).get("result")
                if isinstance(result, dict):
                    consider_article(result)
            if "article" in obj and isinstance(obj["article"], dict):
                consider_article(obj["article"])
            if "content_state" in obj and "title" in obj:
                consider_article(obj)
            if "title" in obj and ("text" in obj or "body" in obj):
                consider(obj.get("title"), obj.get("text") or obj.get("body"))
            for value in obj.values():
                visit(value)
        elif isinstance(obj, list):
            for item in obj:
                visit(item)

    visit(data)
    return best_title, best_body
