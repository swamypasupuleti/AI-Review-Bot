from __future__ import annotations

from typing import Any, Literal

from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

SEVERITY_BADGE = {"high": "🔴 High", "medium": "🟡 Medium", "low": "🟢 Low"}


class CommentItem(BaseModel):
    """A single inline review comment anchored to a line in a changed file."""

    path: str = Field(description="Exact filename from the patch.")
    line: int = Field(
        description=(
            "Line number in the NEW file as shown in the hunk header "
            "(e.g. '@@ -10,7 +20,9 @@' -> the new file starts at line 20)."
        )
    )
    severity: Literal["high", "medium", "low"] | None = Field(
        default=None, description="Optional severity tag."
    )
    body: str = Field(description="The review comment, may include a suggested fix.")


class AIReview(BaseModel):
    """Structured code review output."""

    summary: str = Field(description="1-3 sentence overall assessment of the PR.")
    comments: list[CommentItem] = Field(
        default_factory=list,
        description="Inline comments on added lines. Empty if the change looks good.",
    )


SYSTEM_PROMPT = """You are a senior frontend code reviewer bot.
Review the supplied unified-diff patches against these standards:

1. Correctness & bugs (off-by-one, null/undefined, wrong API usage, race conditions).
2. Security (XSS, hardcoded secrets, unsafe innerHTML/eval, missing input sanitization).
3. Error handling (unhandled promise rejections, swallowed errors, missing try/catch).
4. Readability & naming (clarity, dead code, magic numbers, overly long functions).
5. Performance (unnecessary re-renders, large bundles, blocking operations, layout thrash).
6. Tests (new logic without tests).
7. A11y / semantics (HTML semantics, ARIA, alt text, keyboard navigation).
8. Consistency with existing codebase patterns (use the 'Related code from the codebase'
   section to learn the project's conventions before suggesting alternatives).

Rules:
- Comment ONLY on lines that are added (lines starting with '+') in the patches.
- The 'line' field MUST be the line number in the NEW file as shown in the hunk header.
- Do NOT comment on the 'Related code' section — it is reference material only.
- Skip nitpicks. If the change is good, return an empty comments array and a positive summary.
"""

_PROMPT = ChatPromptTemplate.from_messages(
    [("system", SYSTEM_PROMPT), ("human", "{user_prompt}")]
)


class AIReviewer:
    def __init__(self, api_key: str, model: str):
        llm = ChatOpenAI(api_key=api_key, model=model, temperature=0.2)
        self._chain = _PROMPT | llm.with_structured_output(AIReview)

    async def review(
        self,
        pr: dict[str, Any],
        diff_section: str,
        rag_section: str,
    ) -> AIReview:
        related_block = (
            f"\nRelated code from the codebase (reference only — do NOT comment on these):\n{rag_section}"
            if rag_section.strip()
            else ""
        )
        user_prompt = (
            f"Repository: {pr['base']['repo']['full_name']}\n"
            f"PR #{pr['number']}: {pr.get('title', '')}\n"
            f"Author: {pr['user']['login']}\n"
            f"Description:\n{(pr.get('body') or '').strip() or '(no description)'}\n\n"
            f"Changed files:\n{diff_section}"
            f"{related_block}"
        )
        return await self._chain.ainvoke({"user_prompt": user_prompt})


def comments_to_github_payload(comments: list[CommentItem]) -> list[dict[str, Any]]:
    """Convert structured Pydantic comments to the dict shape GitHub expects,
    prepending a severity badge to the body when one is present."""
    out: list[dict[str, Any]] = []
    for c in comments:
        badge = SEVERITY_BADGE.get(c.severity or "")
        body = (f"**{badge}** — " if badge else "") + c.body.strip()
        out.append({"path": c.path, "line": c.line, "side": "RIGHT", "body": body})
    return out
