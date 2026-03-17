import urllib.parse
from typing import Optional, Tuple


def rewrite_github_url(raw_url: str) -> Tuple[str, Optional[str]]:
    try:
        parsed = urllib.parse.urlparse(raw_url)
    except ValueError:
        return raw_url, None

    host = (parsed.netloc or "").lower()
    if host != "github.com":
        return raw_url, None

    parts = [p for p in (parsed.path or "").split("/") if p]
    if len(parts) < 2:
        return raw_url, None

    owner, repo = parts[0], parts[1]
    branch = None
    if len(parts) >= 4 and parts[2] == "tree":
        branch = parts[3]
        if len(parts) >= 5:
            return raw_url, None

    if len(parts) >= 5 and parts[2] == "blob":
        branch = parts[3]
        file_path = "/".join(parts[4:])
        if not branch or not file_path:
            return raw_url, None
        raw_path = f"/{owner}/{repo}/raw/refs/heads/{branch}/{file_path}"
        updated = parsed._replace(path=raw_path, params="", query="", fragment="")
        return urllib.parse.urlunparse(updated), _suggest_raw_filename(file_path)

    if branch is None and len(parts) >= 3:
        return raw_url, None

    if branch is None:
        branch = "master"

    archive_path = f"/{owner}/{repo}/archive/refs/heads/{branch}.zip"
    updated = parsed._replace(path=archive_path, params="", query="", fragment="")
    return urllib.parse.urlunparse(updated), _suggest_zip_filename(repo, branch)


def _suggest_zip_filename(repo: str, branch: str) -> Optional[str]:
    repo = (repo or "").strip()
    branch = (branch or "").strip()
    if not repo or not branch:
        return None
    return f"{repo}-{branch}.zip"


def _suggest_raw_filename(file_path: str) -> Optional[str]:
    name = (file_path or "").rsplit("/", 1)[-1].strip()
    if not name:
        return None
    return name
