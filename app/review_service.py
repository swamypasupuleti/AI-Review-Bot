from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from app.ai_reviewer import AIReview, AIReviewer, comments_to_github_payload
from app.config import Settings
from app.context_builder import build_diff_section, build_rag_section
from app.github_client import GitHubClient
from app.indexer import build_index, is_indexed, open_vectorstore
from app.repo_fetcher import cleanup, download_repo_tarball
from app.retriever import CodeRetriever

logger = logging.getLogger(__name__)

TRIGGER_ACTIONS = {"opened", "reopened", "assigned"}

# Per-repo locks to prevent two concurrent webhook deliveries from indexing the
# same repo at the same time. Single-process scope only — for multi-worker
# deployments this needs to move to a file lock or a DB row lock.
_INDEXING_LOCKS: dict[str, asyncio.Lock] = {}


def should_trigger_review(payload: dict[str, Any], bot_username: str) -> bool:
    if payload.get("action") not in TRIGGER_ACTIONS:
        return False
    target = bot_username.lower()
    if not target:
        return False
    assignees = (payload.get("pull_request") or {}).get("assignees") or []
    return any((a or {}).get("login", "").lower() == target for a in assignees)


async def run_review(payload: dict[str, Any], settings: Settings) -> None:
    pr = payload["pull_request"]
    repo = payload["repository"]
    owner, repo_name = repo["owner"]["login"], repo["name"]
    pr_number, head_sha = pr["number"], pr["head"]["sha"]
    base_ref = pr["base"]["ref"]

    gh = GitHubClient(token=settings.github_token)
    ai = AIReviewer(api_key=settings.openai_api_key, model=settings.openai_model)

    logger.info("Reviewing %s/%s#%s", owner, repo_name, pr_number)

    # 1. Make sure the repo is indexed (one-time per repo in Phase 1).
    await _ensure_indexed(gh, settings, owner, repo_name, pr_number, base_ref)

    # 2. Pull the diff.
    files = await gh.list_pr_files(owner, repo_name, pr_number)
    if not any(f.get("patch") for f in files):
        return

    # 3. Retrieve related code from the vector store, per changed file.
    vs = open_vectorstore(
        settings.index_dir, owner, repo_name,
        api_key=settings.openai_api_key, model=settings.openai_embedding_model,
    )
    retriever = CodeRetriever(vs, top_k=settings.rag_top_k)

    related: dict[str, list] = {}
    for f in files:
        patch = f.get("patch")
        if not patch:
            continue
        related[f["filename"]] = await retriever.find_related(
            query=patch, exclude_path=f["filename"],
        )

    # 4. Merge into a single context blob for the LLM.
    diff_section = build_diff_section(files, settings.diff_budget)
    rag_section = build_rag_section(related, settings.rag_budget)

    # 5. Run the review.
    review = await ai.review(pr, diff_section, rag_section)

    # 6. Post inline comments.
    valid_paths = {f["filename"] for f in files if f.get("patch")}
    inline = [
        c for c in comments_to_github_payload(review.comments) if c["path"] in valid_paths
    ]
    body = _format_summary(review, len(inline))

    try:
        await gh.create_review(owner, repo_name, pr_number, head_sha, body, "COMMENT", inline)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code != 422:
            raise
        logger.warning("Inline anchors invalid; posting summary only.")
        await gh.create_review(owner, repo_name, pr_number, head_sha, body, "COMMENT", [])


async def _ensure_indexed(
    gh: GitHubClient,
    settings: Settings,
    owner: str,
    repo: str,
    pr_number: int,
    base_ref: str,
) -> None:
    """If the repo has no index yet, post a cold-start notice on the PR and build it."""
    key = f"{owner}/{repo}"
    if is_indexed(settings.index_dir, owner, repo):
        return

    lock = _INDEXING_LOCKS.setdefault(key, asyncio.Lock())
    async with lock:
        # Re-check inside the lock — another waiting task may have done the work.
        if is_indexed(settings.index_dir, owner, repo):
            return

        await gh.create_issue_comment(
            owner, repo, pr_number,
            "🤖 **Review Bot is indexing this repo for the first time.** "
            "The first review will take a few minutes; subsequent reviews will be fast.",
        )

        repo_root = await download_repo_tarball(settings.github_token, owner, repo, base_ref)
        try:
            n = build_index(
                repo_root=repo_root,
                index_root=settings.index_dir,
                owner=owner,
                repo=repo,
                api_key=settings.openai_api_key,
                model=settings.openai_embedding_model,
                chunk_size=settings.chunk_size,
                chunk_overlap=settings.chunk_overlap,
            )
            logger.info("Indexed %d chunks for %s/%s", n, owner, repo)
        finally:
            cleanup(repo_root)


def _format_summary(review: AIReview, n_findings: int) -> str:
    header = (
        "✅ Looks good"
        if n_findings == 0
        else f"🔍 {n_findings} finding{'s' if n_findings != 1 else ''}"
    )
    return f"### Review Bot — {header}\n\n{review.summary}\n\n_Generated automatically with repo-wide RAG context._"
