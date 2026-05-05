from __future__ import annotations

from typing import Any

from langchain_core.documents import Document


def build_diff_section(files: list[dict[str, Any]], budget: int) -> str:
    """Render the per-file unified diff patches into a single text block,
    truncating to stay under `budget` characters."""
    parts: list[str] = []
    used = 0
    for f in files:
        patch = f.get("patch")
        if not patch:
            continue
        chunk = (
            f"### {f['filename']} (+{f.get('additions', 0)}/-{f.get('deletions', 0)})\n"
            f"{patch}\n"
        )
        if used + len(chunk) > budget:
            parts.append(chunk[: budget - used] + "\n... [truncated]\n")
            break
        parts.append(chunk)
        used += len(chunk)
    return "".join(parts)


def build_rag_section(related: dict[str, list[Document]], budget: int) -> str:
    """Render related-code snippets into a single text block, deduped across
    files and truncated to fit `budget`. `related` maps changed-file path ->
    list of similar Documents pulled from the vector store."""
    if not related:
        return ""

    seen: set[tuple[str, int]] = set()
    parts: list[str] = []
    used = 0

    for changed_file, docs in related.items():
        for doc in docs:
            path = doc.metadata.get("path", "?")
            chunk_idx = doc.metadata.get("chunk", 0)
            key = (path, chunk_idx)
            if key in seen:
                continue
            seen.add(key)

            header = f"### From {path} (related to changes in {changed_file})\n"
            block = header + doc.page_content.rstrip() + "\n\n"
            if used + len(block) > budget:
                if used == 0:
                    # At least include a truncated first snippet.
                    parts.append(block[:budget] + "\n... [truncated]\n")
                else:
                    parts.append("... [more related snippets truncated] ...\n")
                return "".join(parts)
            parts.append(block)
            used += len(block)

    return "".join(parts)
