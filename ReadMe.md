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
