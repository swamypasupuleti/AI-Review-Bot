from __future__ import annotations

import logging
from typing import Any

from langchain_chroma import Chroma
from langchain_core.documents import Document

logger = logging.getLogger(__name__)


class CodeRetriever:
    """Thin wrapper that queries the per-repo Chroma store and filters out
    chunks that came from the same file we're asking about (otherwise the top
    hits are trivially the file itself)."""

    def __init__(self, vectorstore: Chroma, top_k: int = 8):
        self._vs = vectorstore
        self._k = top_k

    async def find_related(self, query: str, exclude_path: str | None = None) -> list[Document]:
        if not query.strip():
            return []

        # Over-fetch then filter so we still return ~k results after dropping
        # same-file hits.
        fetch_k = self._k * 3 if exclude_path else self._k
        try:
            hits: list[Any] = await self._vs.asimilarity_search(query, k=fetch_k)
        except Exception:  # noqa: BLE001 — logged below, never break the review
            logger.exception("Vector store query failed; returning no related code.")
            return []

        if exclude_path:
            hits = [d for d in hits if d.metadata.get("path") != exclude_path]
        return hits[: self._k]
