# Simple Upload Server

Small FastAPI app for uploading, downloading, and deleting files in a local directory.

**Storage location**

Files are stored in `~/upload/files` (resolved from the running user's home directory).

**Run**

Install:

```bash
pip install "fastapi[standard]"
```

Optional (Twitter/X article extraction):

```bash
npm install
```

Production-style:

```bash
fastapi run server.py --host 0.0.0.0 --port 8000
```

Dev mode with reload:

```bash
fastapi dev server.py --host 0.0.0.0 --port 8000
```

**Endpoints**

- `GET /`
  - Chunked HTML upload form (loads `index.html`).
- `GET /help`
  - Returns this README as plain text.
- `POST /check/chunk`
  - JSON body: `filename`, `block_id`, `block_md5`, `block_size`, optional `total_size`.
  - Response: `{ "exists": true|false }` based on MD5 match at the expected offset.
- `POST /upload/chunk`
  - Multipart form fields: `filename`, `block_id`, `block_md5`, `block_size`, optional `total_size`, plus `file` (chunk bytes).
  - Writes chunk into `filename.part` and renames to the final filename when the last chunk is received.
- `POST /upload/url`
  - JSON body: `url`, optional `filename`.
  - Server downloads the URL (defaults to `https://` if no scheme is provided) and saves it into `~/upload/files`.
  - Follows HTTP redirects.
  - Download starts in the background and returns immediately with a relative `url`.
  - Accessing the file while downloading returns `202 File is downloading`.
  - Auto-rewrite helpers:
    - arXiv `https://arxiv.org/abs/...` or `https://arxiv.org/html/...` are downloaded as `https://arxiv.org/pdf/...` with a `.pdf` filename.
    - GitHub repo `https://github.com/{owner}/{repo}` is downloaded as `.../archive/refs/heads/master.zip` (or `.../tree/{branch}` to the branch zip).
    - GitHub file `https://github.com/{owner}/{repo}/blob/{branch}/path` is downloaded as `.../raw/refs/heads/{branch}/path`.
    - Twitter/X articles `https://twitter.com/i/article...`, `https://twitter.com/i/articles/...`, or `https://x.com/{user}/article/...` are downloaded as a `.md` file extracted from the article title and body.
    - Twitter/X status URLs are supported; if the tweet links to an article, the article content is used.
- `GET /url_status/{filename}`
  - Returns JSON status for a URL download: `status`, `downloaded`, `total`, `url`.
- `GET /speed_test/download`
  - Query: `size` (bytes, default 10 MB, max 200 MB). Returns a stream for download speed tests.
- `POST /speed_test/upload`
  - Raw body upload for speed tests (max 200 MB). Returns bytes received.
- `GET /{filename}`
  - Download a file from `~/upload/files`.
  - Text-like types (`text/*`, JSON, XML, JS) are served inline; others download as attachments.
- `GET /rm/{pattern}`
  - Deletes files matching a glob pattern in `~/upload/files`.
  - Example: `GET /rm/*.txt` deletes all `.txt` files.
  - Only basename patterns are allowed (no path separators).

**Notes**

- Upload and delete operations only affect files in `~/upload/files`.
- Directory traversal is blocked by requiring the path to be a basename.
- Chunked uploads use 1,000,000-byte blocks and can resume by checking existing blocks.
- Before each upload (multipart, chunk, or URL), the server ensures at least 5 GB free space.
  - If free space is below 5 GB, the largest file in `~/upload/files` is deleted.
- Twitter/X article extraction uses a Node script powered by `@the-convocation/twitter-scraper`.
  - The script extracts the tweet/article id from the URL and uses `Scraper.getTweet`.
  - If articles require auth, set `TWITTER_COOKIES` (or `TWITTER_COOKIE`) for the Node fetcher.
  - If `node` is not on PATH, set `TWITTER_NODE_PATH` to the Node binary.

**Examples**

The URL upload endpoint includes built-in URL normalization for common sources (arXiv and GitHub) so you can paste a share link and still download the actual file.

**Twitter Article Upload**

Twitter/X article and status URLs are fetched via the Node scraper and saved as Markdown. Re-uploading the same URL will refresh the file (it replaces the previous download).

- arXiv
  - `https://arxiv.org/abs/2512.01457v4` → `https://arxiv.org/pdf/2512.01457v4`
  - `https://arxiv.org/html/2512.01457v4` → `https://arxiv.org/pdf/2512.01457v4`
- GitHub repo
  - `https://github.com/moonshotai/attention-residuals` → `https://github.com/moonshotai/attention-residuals/archive/refs/heads/master.zip`
- GitHub file
  - `https://github.com/MoonshotAI/Attention-Residuals/blob/master/Attention_Residuals.pdf` → `https://github.com/MoonshotAI/Attention-Residuals/raw/refs/heads/master/Attention_Residuals.pdf`
- Twitter/X article
  - `https://twitter.com/i/article/1234567890` → `twitter-article-1234567890.md`
  - `https://twitter.com/i/articles/1234567890` → `twitter-article-1234567890.md`
  - `https://x.com/RayDalio/article/1234567890` → `twitter-article-1234567890.md`
- Twitter/X status
  - `https://x.com/user/status/1234567890` → `twitter-article-1234567890.md`
