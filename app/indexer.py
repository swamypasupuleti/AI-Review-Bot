from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterable

from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_openai import OpenAIEmbeddings
from langchain_text_splitters import Language, RecursiveCharacterTextSplitter

logger = logging.getLogger(__name__)

COLLECTION_NAME = "code_v1"

# Frontend extensions we care about (Phase 1 scope: JS + HTML/CSS family).
EXT_TO_LANGUAGE: dict[str, Language | None] = {
    ".js": Language.JS,
    ".jsx": Language.JS,
    ".mjs": Language.JS,
    ".cjs": Language.JS,
    ".ts": Language.TS,
    ".tsx": Language.TS,
    ".html": Language.HTML,
    ".htm": Language.HTML,
    # No language-specific splitter for these — fall back to generic recursive splitter.
    ".css": None,
    ".scss": None,
    ".sass": None,
    ".less": None,
    ".vue": None,
    ".svelte": None,
}

SKIP_DIRS = {
    ".git", "node_modules", "dist", "build", "out",
    ".next", ".nuxt", ".cache", ".turbo", ".parcel-cache",
    "coverage", "vendor", "bower_components", ".venv", "venv",
    "__pycache__", ".vscode", ".idea",
}

MAX_FILE_BYTES = 1_000_000  # 1 MB — skip minified bundles, sourcemaps, etc.


def repo_index_dir(index_root: Path, owner: str, repo: str) -> Path:
    return index_root / f"{owner}__{repo}"


def is_indexed(index_root: Path, owner: str, repo: str) -> bool:
    """A repo is considered indexed if its Chroma sqlite file exists."""
    return (repo_index_dir(index_root, owner, repo) / "chroma.sqlite3").exists()


def open_vectorstore(
    index_root: Path, owner: str, repo: str, api_key: str, model: str
) -> Chroma:
    """Open (or lazily create) the persisted Chroma collection for this repo."""
    persist = repo_index_dir(index_root, owner, repo)
    persist.mkdir(parents=True, exist_ok=True)
    embeddings = OpenAIEmbeddings(api_key=api_key, model=model)
    return Chroma(
        collection_name=COLLECTION_NAME,
        embedding_function=embeddings,
        persist_directory=str(persist),
    )


def build_index(
    repo_root: Path,
    index_root: Path,
    owner: str,
    repo: str,
    api_key: str,
    model: str,
    chunk_size: int = 1000,
    chunk_overlap: int = 100,
) -> int:
    """Walk repo_root, chunk supported files, embed, and persist to Chroma.

    Returns the number of chunks indexed.
    """
    vs = open_vectorstore(index_root, owner, repo, api_key, model)

    docs: list[Document] = []
    for path in _iter_source_files(repo_root):
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if not text.strip():
            continue

        rel = path.relative_to(repo_root).as_posix()
        ext = path.suffix.lower()
        splitter = _splitter_for(ext, chunk_size, chunk_overlap)
        for i, chunk in enumerate(splitter.split_text(text)):
            docs.append(
                Document(
                    page_content=chunk,
                    metadata={"path": rel, "ext": ext, "chunk": i},
                )
            )

    if not docs:
        logger.warning("No indexable source files found under %s", repo_root)
        return 0

    logger.info("Embedding %d chunks for %s/%s", len(docs), owner, repo)
    # Insert in batches so a single huge request doesn't blow up.
    BATCH = 200
    for batch in _batched(docs, BATCH):
        vs.add_documents(list(batch))
    return len(docs)


def _iter_source_files(root: Path) -> Iterable[Path]:
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.suffix.lower() not in EXT_TO_LANGUAGE:
            continue
        try:
            if path.stat().st_size > MAX_FILE_BYTES:
                continue
        except OSError:
            continue
        yield path


def _splitter_for(ext: str, size: int, overlap: int) -> RecursiveCharacterTextSplitter:
    lang = EXT_TO_LANGUAGE.get(ext)
    if lang is None:
        return RecursiveCharacterTextSplitter(chunk_size=size, chunk_overlap=overlap)
    return RecursiveCharacterTextSplitter.from_language(
        language=lang, chunk_size=size, chunk_overlap=overlap
    )


def _batched(items: list, n: int) -> Iterable[list]:
    for i in range(0, len(items), n):
        yield items[i : i + n]
