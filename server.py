import glob
import hashlib
import mimetypes
import os
import shutil
import threading
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import List, Optional

import arxiv
import github
import twitter_article
from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, StreamingResponse
from pydantic import BaseModel

UPLOAD_DIR = os.path.expanduser("~/upload/files")
README_PATH = os.path.join(os.path.dirname(__file__), "README.md")
INDEX_PATH = os.path.join(os.path.dirname(__file__), "index.html")
CHUNK_SIZE = 1_000_000
PART_SUFFIX = ".part"
URL_PART_SUFFIX = ".download"
MIN_FREE_BYTES = 5 * 1024 * 1024 * 1024
MAX_SPEED_TEST_BYTES = 200 * 1024 * 1024

app = FastAPI()
DOWNLOAD_INFO = {}
DOWNLOAD_LOCK = threading.Lock()


@app.get("/help")
def help_readme():
    try:
        with open(README_PATH, "r", encoding="utf-8") as f:
            return PlainTextResponse(f.read())
    except OSError:
        return PlainTextResponse("README not found\n", status_code=404)


@app.get("/rm/{pattern}")
def delete_file(pattern: str):
    safe_pattern = os.path.basename(pattern)
    if safe_pattern != pattern or not safe_pattern:
        return PlainTextResponse("Not found\n", status_code=404)
    matches = []
    for path in sorted(glob.glob(os.path.join(UPLOAD_DIR, safe_pattern))):
        if not os.path.isfile(path):
            continue
        matches.append(path)
    if not matches:
        return PlainTextResponse("Not found\n", status_code=404)
    deleted = []
    for path in matches:
        try:
            os.remove(path)
        except OSError:
            return PlainTextResponse("Failed to delete\n", status_code=500)
        deleted.append(os.path.basename(path))
    return PlainTextResponse("Deleted:\n" + "\n".join(deleted) + "\n")


@app.get("/", response_class=HTMLResponse)
def index():
    try:
        with open(INDEX_PATH, "r", encoding="utf-8") as f:
            return HTMLResponse(f.read())
    except OSError:
        return PlainTextResponse("Index not found\n", status_code=404)


def _safe_basename(name: str) -> Optional[str]:
    if not name:
        return None
    safe = os.path.basename(name)
    if safe != name:
        return None
    return safe


def _chunk_path(filename: str) -> str:
    return os.path.join(UPLOAD_DIR, filename + PART_SUFFIX)


def _existing_path(filename: str) -> Optional[str]:
    part_path = _chunk_path(filename)
    if os.path.exists(part_path):
        return part_path
    final_path = os.path.join(UPLOAD_DIR, filename)
    if os.path.exists(final_path):
        return final_path
    return None


def _chunk_md5(path: str, offset: int, size: int) -> Optional[str]:
    try:
        with open(path, "rb") as f:
            f.seek(offset)
            data = f.read(size)
    except OSError:
        return None
    if len(data) != size:
        return None
    return hashlib.md5(data).hexdigest()


def _ensure_free_space(exclude_names: Optional[set] = None) -> bool:
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    exclude_names = exclude_names or set()
    while True:
        usage = shutil.disk_usage(UPLOAD_DIR)
        if usage.free >= MIN_FREE_BYTES:
            return True
        largest = None
        largest_size = -1
        try:
            for entry in os.scandir(UPLOAD_DIR):
                if not entry.is_file():
                    continue
                if entry.name in exclude_names:
                    continue
                try:
                    size = entry.stat().st_size
                except OSError:
                    continue
                if size > largest_size:
                    largest_size = size
                    largest = entry.path
        except OSError:
            return False
        if not largest:
            return False
        try:
            os.remove(largest)
        except OSError:
            return False


def _update_download_info(name: str, **kwargs) -> None:
    with DOWNLOAD_LOCK:
        entry = DOWNLOAD_INFO.setdefault(name, {})
        entry.update(kwargs)


def _get_download_info(name: str) -> dict:
    with DOWNLOAD_LOCK:
        return dict(DOWNLOAD_INFO.get(name, {}))


class ChunkCheck(BaseModel):
    filename: str
    block_id: int
    block_md5: str
    block_size: int
    total_size: Optional[int] = None


class UrlUpload(BaseModel):
    url: str
    filename: Optional[str] = None


@app.post("/check/chunk")
def check_chunk(info: ChunkCheck):
    safe_name = _safe_basename(info.filename)
    if safe_name is None:
        return PlainTextResponse("Not found\n", status_code=404)
    if info.block_id < 0 or info.block_size <= 0 or info.block_size > CHUNK_SIZE:
        return PlainTextResponse("Bad request\n", status_code=400)
    if len(info.block_md5) != 32:
        return PlainTextResponse("Bad request\n", status_code=400)

    os.makedirs(UPLOAD_DIR, exist_ok=True)
    path = _existing_path(safe_name)
    if not path:
        return {"exists": False}

    offset = info.block_id * CHUNK_SIZE
    digest = _chunk_md5(path, offset, info.block_size)
    if digest is None:
        return {"exists": False}
    return {"exists": digest == info.block_md5}


@app.post("/upload/chunk")
def upload_chunk(
    filename: str = Form(...),
    block_id: int = Form(...),
    block_md5: str = Form(...),
    block_size: int = Form(...),
    total_size: Optional[int] = Form(None),
    file: UploadFile = File(...),
):
    safe_name = _safe_basename(filename)
    if safe_name is None:
        return PlainTextResponse("Not found\n", status_code=404)
    if block_id < 0 or block_size <= 0 or block_size > CHUNK_SIZE:
        return PlainTextResponse("Bad request\n", status_code=400)
    if len(block_md5) != 32:
        return PlainTextResponse("Bad request\n", status_code=400)

    os.makedirs(UPLOAD_DIR, exist_ok=True)
    part_path = _chunk_path(safe_name)
    offset = block_id * CHUNK_SIZE
    if not _ensure_free_space(exclude_names={os.path.basename(part_path)}):
        return PlainTextResponse("Insufficient storage\n", status_code=507)

    data = file.file.read()
    if len(data) != block_size:
        return PlainTextResponse("Bad request\n", status_code=400)
    if hashlib.md5(data).hexdigest() != block_md5:
        return PlainTextResponse("Bad request\n", status_code=400)

    try:
        if not os.path.exists(part_path):
            with open(part_path, "wb"):
                pass
        with open(part_path, "r+b") as f:
            f.seek(offset)
            f.write(data)
    except OSError:
        return PlainTextResponse("Failed to write\n", status_code=500)

    if total_size is not None and offset + block_size == total_size:
        final_path = os.path.join(UPLOAD_DIR, safe_name)
        try:
            os.replace(part_path, final_path)
        except OSError:
            return PlainTextResponse("Failed to finalize\n", status_code=500)

    return PlainTextResponse("OK\n")


@app.post("/upload/url")
def upload_url(info: UrlUpload):
    raw_url = (info.url or "").strip()
    if not raw_url:
        return PlainTextResponse("Bad request\n", status_code=400)
    if not raw_url.startswith(("http://", "https://")):
        if raw_url.startswith("//"):
            raw_url = "https:" + raw_url
        else:
            raw_url = "https://" + raw_url

    parsed = urllib.parse.urlparse(raw_url)
    if parsed.scheme not in {"http", "https"}:
        return PlainTextResponse("Bad request\n", status_code=400)

    is_twitter_article = twitter_article.is_twitter_article_url(raw_url)
    twitter_name = twitter_article.suggested_filename(raw_url) if is_twitter_article else None

    raw_url, suggested_name = github.rewrite_github_url(raw_url)
    raw_url, arxiv_name = arxiv.rewrite_arxiv_url(raw_url)
    if not suggested_name:
        suggested_name = arxiv_name
    if not suggested_name:
        suggested_name = twitter_name
    parsed = urllib.parse.urlparse(raw_url)

    if info.filename:
        safe_name = _safe_basename(info.filename)
    else:
        candidate = suggested_name or os.path.basename(parsed.path)
        safe_name = _safe_basename(candidate)
    if not safe_name:
        safe_name = uuid.uuid4().hex[:6]

    os.makedirs(UPLOAD_DIR, exist_ok=True)
    dest_path = os.path.join(UPLOAD_DIR, safe_name)
    tmp_path = os.path.join(UPLOAD_DIR, safe_name + URL_PART_SUFFIX)

    if os.path.exists(dest_path):
        size = os.path.getsize(dest_path)
        _update_download_info(safe_name, status="ready", downloaded=size, total=size)
        return {"filename": safe_name, "url": f"./{safe_name}", "status": "ready"}
    if _get_download_info(safe_name).get("status") == "downloading":
        return {"filename": safe_name, "url": f"./{safe_name}", "status": "downloading"}

    if not _ensure_free_space(exclude_names={os.path.basename(tmp_path)}):
        return PlainTextResponse("Insufficient storage\n", status_code=507)

    _update_download_info(safe_name, status="downloading", downloaded=0, total=None)

    def _worker():
        try:
            opener = urllib.request.build_opener(urllib.request.HTTPRedirectHandler())
            if is_twitter_article:
                markdown = twitter_article.fetch_article_markdown(raw_url)
                if markdown is None:
                    _update_download_info(safe_name, status="failed")
                    return
                data = markdown.encode("utf-8")
                total = len(data)
                _update_download_info(safe_name, total=total)
                with open(tmp_path, "wb") as f:
                    f.write(data)
                    _update_download_info(safe_name, downloaded=total)
                os.replace(tmp_path, dest_path)
                _update_download_info(safe_name, status="ready", downloaded=total, total=total)
                return

            with opener.open(raw_url) as resp:
                if resp.status and resp.status >= 400:
                    _update_download_info(safe_name, status="failed")
                    return
                total = None
                if resp.headers.get("Content-Length"):
                    try:
                        total = int(resp.headers.get("Content-Length"))
                    except ValueError:
                        total = None
                if total is not None:
                    _update_download_info(safe_name, total=total)
                with open(tmp_path, "wb") as f:
                    downloaded = 0
                    while True:
                        chunk = resp.read(64 * 1024)
                        if not chunk:
                            break
                        f.write(chunk)
                        downloaded += len(chunk)
                        _update_download_info(safe_name, downloaded=downloaded)
            os.replace(tmp_path, dest_path)
            size = os.path.getsize(dest_path)
            _update_download_info(safe_name, status="ready", downloaded=size, total=total or size)
        except (urllib.error.URLError, urllib.error.HTTPError, OSError):
            _update_download_info(safe_name, status="failed")
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except OSError:
                pass

    threading.Thread(target=_worker, daemon=True).start()
    return {"filename": safe_name, "url": f"./{safe_name}", "status": "downloading"}


@app.get("/url_status/{filename}")
def url_status(filename: str):
    safe_name = _safe_basename(filename)
    if safe_name is None:
        return PlainTextResponse("Not found\n", status_code=404)

    info = _get_download_info(safe_name)
    dest_path = os.path.join(UPLOAD_DIR, safe_name)
    tmp_path = os.path.join(UPLOAD_DIR, safe_name + URL_PART_SUFFIX)

    if not info:
        if os.path.exists(dest_path):
            size = os.path.getsize(dest_path)
            return {"status": "ready", "downloaded": size, "total": size, "url": f"./{safe_name}"}
        if os.path.exists(tmp_path):
            size = os.path.getsize(tmp_path)
            return {"status": "downloading", "downloaded": size, "total": None, "url": f"./{safe_name}"}
        return PlainTextResponse("Not found\n", status_code=404)

    status = info.get("status", "downloading")
    downloaded = info.get("downloaded", 0)
    total = info.get("total")
    if status == "ready" and os.path.exists(dest_path):
        size = os.path.getsize(dest_path)
        downloaded = size
        total = total or size
    return {"status": status, "downloaded": downloaded, "total": total, "url": f"./{safe_name}"}


@app.get("/speed_test/download")
def speed_test_download(size: int = 10 * 1024 * 1024):
    if size <= 0 or size > MAX_SPEED_TEST_BYTES:
        return PlainTextResponse("Bad request\n", status_code=400)

    def _gen(total: int, chunk_size: int = 64 * 1024):
        remaining = total
        block = b"\0" * chunk_size
        while remaining > 0:
            n = remaining if remaining < chunk_size else chunk_size
            yield block[:n]
            remaining -= n

    headers = {"Content-Length": str(size)}
    return StreamingResponse(_gen(size), media_type="application/octet-stream", headers=headers)


@app.post("/speed_test/upload")
async def speed_test_upload(request: Request):
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > MAX_SPEED_TEST_BYTES:
            return PlainTextResponse("Bad request\n", status_code=400)
    return {"received": total}


@app.get("/{filename}")
def download_file(filename: str):
    safe_name = os.path.basename(filename)
    if safe_name != filename or not safe_name:
        return PlainTextResponse("Not found\n", status_code=404)
    file_path = os.path.join(UPLOAD_DIR, safe_name)
    if not os.path.exists(file_path):
        status = _get_download_info(safe_name).get("status")
        tmp_path = os.path.join(UPLOAD_DIR, safe_name + URL_PART_SUFFIX)
        if status == "downloading" or os.path.exists(tmp_path):
            return PlainTextResponse("File is downloading\n", status_code=202)
        if status == "failed":
            return PlainTextResponse("Failed to download\n", status_code=502)
        return PlainTextResponse("Not found\n", status_code=404)
    media_type, _ = mimetypes.guess_type(file_path)
    if media_type and (
        media_type.startswith("text/")
        or media_type.startswith("image/")
        or media_type in {"application/json", "application/xml", "application/javascript"}
    ):
        return FileResponse(
            file_path,
            media_type=media_type,
            headers={"Content-Disposition": "inline"},
        )
    return FileResponse(file_path, filename=safe_name)
