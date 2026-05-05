from __future__ import annotations

import logging
import shutil
import tarfile
import tempfile
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com"


async def download_repo_tarball(token: str, owner: str, repo: str, ref: str) -> Path:
    """Download the repo at `ref` (branch / SHA / tag) as a tarball, extract it,
    and return the path to the extracted root directory.

    GitHub returns a tarball whose top-level directory is named like
    `{owner}-{repo}-{short-sha}/`. We return that path; the caller is responsible
    for cleaning up its parent directory when done (use shutil.rmtree on
    `result.parent`, which is the temp dir).
    """
    url = f"{GITHUB_API}/repos/{owner}/{repo}/tarball/{ref}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "review-bot/1.0",
    }

    tmp_dir = Path(tempfile.mkdtemp(prefix="reviewbot-src-"))
    tarball_path = tmp_dir / "src.tar.gz"

    logger.info("Downloading tarball %s/%s@%s", owner, repo, ref)
    try:
        async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
            async with client.stream("GET", url, headers=headers) as r:
                r.raise_for_status()
                with tarball_path.open("wb") as f:
                    async for chunk in r.aiter_bytes(chunk_size=64 * 1024):
                        f.write(chunk)

        with tarfile.open(tarball_path, "r:gz") as tar:
            tar.extractall(tmp_dir, filter="data")
        tarball_path.unlink(missing_ok=True)

        roots = [p for p in tmp_dir.iterdir() if p.is_dir()]
        if len(roots) != 1:
            raise RuntimeError(f"Unexpected tarball layout under {tmp_dir}: {roots}")
        return roots[0]
    except Exception:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise


def cleanup(repo_root: Path) -> None:
    """Remove the temp directory created by download_repo_tarball."""
    parent = repo_root.parent
    if parent.exists() and parent.name.startswith("reviewbot-src-"):
        shutil.rmtree(parent, ignore_errors=True)
