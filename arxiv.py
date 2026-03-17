import urllib.parse
from typing import Optional, Tuple


def rewrite_arxiv_url(raw_url: str) -> Tuple[str, Optional[str]]:
    try:
        parsed = urllib.parse.urlparse(raw_url)
    except ValueError:
        return raw_url, None

    host = (parsed.netloc or "").lower()
    if not host or not host.endswith("arxiv.org"):
        return raw_url, None

    path = parsed.path or ""
    for prefix in ("/abs/", "/html/"):
        if path.startswith(prefix):
            ident = _normalize_ident(path[len(prefix) :])
            if not ident:
                return raw_url, None
            pdf_path = "/pdf/" + ident
            updated = parsed._replace(path=pdf_path, params="", query="", fragment="")
            return urllib.parse.urlunparse(updated), _suggest_pdf_filename(ident)

    if path.startswith("/pdf/"):
        ident = _normalize_ident(path[len("/pdf/") :])
        if not ident:
            return raw_url, None
        return raw_url, _suggest_pdf_filename(ident)

    return raw_url, None


def _normalize_ident(ident: str) -> str:
    ident = (ident or "").strip("/")
    if ident.endswith(".html"):
        ident = ident[:-5]
    if ident.endswith(".pdf"):
        ident = ident[:-4]
    return ident


def _suggest_pdf_filename(ident: str) -> Optional[str]:
    if not ident:
        return None
    name = ident.rsplit("/", 1)[-1]
    if not name:
        return None
    return name + ".pdf"
