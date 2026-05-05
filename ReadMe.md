# Review Bot

FastAPI service that listens for GitHub Pull Request webhooks, retrieves repo-wide context using RAG, asks an OpenAI model (via LangChain) to review the diff against frontend coding standards, and posts inline comments back to the PR.

## How it works (Phase 1: RAG-augmented review)

```
Webhook
   ↓
Extract diff           (GET /repos/.../pulls/{n}/files)
   ↓
Index repo if missing  (download tarball, chunk, embed → ChromaDB)
   ↓
Semantic search        (query ChromaDB for top-k similar chunks per changed file)
   ↓
Merge context          (token-budgeted: diff + related code)
   ↓
LLM review             (LangChain ChatOpenAI + with_structured_output)
   ↓
Inline comments        (POST /repos/.../pulls/{n}/reviews, event=COMMENT)
```

1. GitHub sends a `pull_request` webhook to `POST /webhook/github`.
2. Endpoint validates `X-Hub-Signature-256` HMAC.
3. If the action is `opened`, `reopened`, or `assigned` and the PR is assigned to `BOT_USERNAME`, the work is queued in a background task.
4. Background task:
   - Checks if this repo has a ChromaDB collection. If not, posts a "🤖 Bot is indexing this repo for the first time" comment, downloads the repo tarball at the PR's base ref, chunks supported source files (JS/TS/JSX/TSX/HTML/CSS/SCSS/SASS/LESS/Vue/Svelte), and persists embeddings (`text-embedding-3-small`) to a per-repo Chroma collection on disk.
   - Pulls the PR's per-file diffs.
   - Queries ChromaDB for the top-k semantically similar chunks per changed file, excluding chunks from the same file.
   - Merges diff + related-code into a token-budgeted prompt.
   - Calls OpenAI through LangChain's `ChatOpenAI.with_structured_output(AIReview)` and gets back validated `{summary, comments[]}`.
   - Posts a single non-blocking review (`event: COMMENT`) with inline comments anchored to specific lines.
   - On 422 (invalid line anchor), retries with summary-only.

> **Vector store:** ChromaDB 1.x. The store is referenced only in `app/indexer.py` and `app/retriever.py`, so swapping to FAISS / Pinecone / pgvector later is a small, contained change.

## Setup

```powershell
# 1. Install deps inside the venv
.\venv\Scripts\python.exe -m pip install -r requirements.txt

# 2. Configure secrets
copy .env.example .env
# then edit .env with real GITHUB_WEBHOOK_SECRET, GITHUB_TOKEN, OPENAI_API_KEY, BOT_USERNAME

# 3. Run the server
.\venv\Scripts\python.exe -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

## Configure the GitHub webhook

In your repo: **Settings → Webhooks → Add webhook**

- **Payload URL:** `https://<public-host>/webhook/github`
- **Content type:** `application/json`
- **Secret:** the same value as `GITHUB_WEBHOOK_SECRET`
- **Events:** select **Pull requests**.

For local dev, expose port 8000 with a tunnel (e.g., ngrok).

## Trigger flow

Open or reopen a PR and assign `BOT_USERNAME`. On the **first** PR for a given repo you'll see a "Bot is indexing…" comment first; the review follows once indexing finishes (~30s–5min depending on repo size).

## Endpoints

| Method | Path                | Purpose                                    |
|--------|---------------------|--------------------------------------------|
| GET    | `/`                 | Hello world (sample)                       |
| GET    | `/hello/{name}`     | Hello sample                               |
| GET    | `/health`           | Health check                               |
| POST   | `/webhook/github`   | GitHub webhook receiver                    |
| GET    | `/docs`             | Swagger UI                                 |

## Module map

| File | Responsibility |
|---|---|
| `main.py` | FastAPI app + `/webhook/github` |
| `app/config.py` | Settings (env-driven) |
| `app/security.py` | HMAC signature verification |
| `app/github_client.py` | GitHub REST: list PR files, post review, post issue comment |
| `app/repo_fetcher.py` | Tarball download + extract |
| `app/indexer.py` | Walk repo, chunk JS/HTML/CSS/SCSS, embed, persist to ChromaDB |
| `app/retriever.py` | Query ChromaDB, exclude same-file hits |
| `app/context_builder.py` | Token-budgeted merge of diff + RAG sections |
| `app/ai_reviewer.py` | LangChain chain (`ChatOpenAI` + `with_structured_output`) |
| `app/review_service.py` | Orchestration: trigger, index, retrieve, review, post |

## Index storage

Per-repo Chroma collections live under `INDEX_DIR` (default `./indexes`):

```
indexes/
  └── owner__repo/
        ├── chroma.sqlite3        # Chroma metadata + docstore
        └── <uuid>/               # HNSW index segment files
```

The collection name inside each directory is `code_v1` (bumpable when chunking strategy or embedding model changes, so old indexes can be invalidated cleanly).

Phase 1 builds the index lazily on the first PR and never refreshes. Phase 2 will add a `push` event handler to re-index on default-branch updates.

## Notes & limits

- **Token budgets** (configurable in `.env`):
  - Diff: `DIFF_BUDGET=30000` chars
  - Related code: `RAG_BUDGET=18000` chars
  - Top-k similar chunks per file: `RAG_TOP_K=8`
- **Indexed extensions:** `.js .jsx .ts .tsx .mjs .cjs .html .htm .css .scss .sass .less .vue .svelte`
- **Skipped dirs:** `node_modules dist build out .next .nuxt .cache .turbo coverage vendor bower_components .git .venv venv __pycache__`
- **Per-file size cap:** 1 MB (skips minified bundles, sourcemaps).
- **All reviews are `COMMENT` events** — the bot never blocks merging.
- **Single-process locking only:** concurrent webhooks for the same repo are serialized via `asyncio.Lock`. For multi-worker deploys, move to a file lock or DB row.
