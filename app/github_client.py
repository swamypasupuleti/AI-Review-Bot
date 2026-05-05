from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

API_BASE = "https://api.github.com"


class GitHubClient:
    """Minimal async GitHub REST client for the PR review workflow."""

    def __init__(self, token: str, timeout: float = 30.0):
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "review-bot/1.0",
        }
        self._timeout = timeout

    async def list_pr_files(self, owner: str, repo: str, pr_number: int) -> list[dict[str, Any]]:
        url = f"{API_BASE}/repos/{owner}/{repo}/pulls/{pr_number}/files"
        files: list[dict[str, Any]] = []
        page = 1
        async with httpx.AsyncClient(timeout=self._timeout, headers=self._headers) as client:
            while True:
                r = await client.get(url, params={"per_page": 100, "page": page})
                r.raise_for_status()
                batch = r.json()
                files.extend(batch)
                if len(batch) < 100:
                    return files
                page += 1

    async def create_review(
        self,
        owner: str,
        repo: str,
        pr_number: int,
        commit_id: str,
        body: str,
        event: str = "COMMENT",
        comments: list[dict[str, Any]] | None = None,
    ) -> None:
        url = f"{API_BASE}/repos/{owner}/{repo}/pulls/{pr_number}/reviews"
        payload = {"commit_id": commit_id, "body": body, "event": event, "comments": comments or []}
        async with httpx.AsyncClient(timeout=self._timeout, headers=self._headers) as client:
            r = await client.post(url, json=payload)
            if r.status_code >= 400:
                logger.error("review post failed %s: %s", r.status_code, r.text)
            r.raise_for_status()

    async def create_issue_comment(
        self, owner: str, repo: str, pr_number: int, body: str
    ) -> None:
        """Post a plain comment on the PR conversation tab. Used for the
        cold-start indexing notice."""
        url = f"{API_BASE}/repos/{owner}/{repo}/issues/{pr_number}/comments"
        async with httpx.AsyncClient(timeout=self._timeout, headers=self._headers) as client:
            r = await client.post(url, json={"body": body})
            r.raise_for_status()
