"""Optional dependencies, and what to do when one is missing.

The heavy models are extras rather than core requirements, deliberately: a
fresh clone boots and serves the API without downloading torch. The cost is
that four pipeline stages import a package that may not be installed.

Left alone, that surfaces as a bare `ModuleNotFoundError: No module named
'sentence_transformers'` raised on a worker thread, recorded as a failed stage,
and read by someone who has no reason to know which extra provides it. The
package name is not the install command, and guessing wrong is easy —
`rapidocr-onnxruntime` imports as `rapidocr_onnxruntime`, `google-genai` as
`google.genai`.

So each call site names its extra and the error says how to fix itself.
"""

from __future__ import annotations

# Extra name in pyproject.toml -> what stops working without it. Kept here
# rather than inline so the set is visible in one place; `tests/unit/
# test_dependencies.py` asserts every name below is a real extra.
EXTRAS: dict[str, str] = {
    "speech": "transcription",
    "embeddings": "embedding and reranking",
    "ocr": "reading on-screen text",
    "llm": "answering questions",
    "diarization": "speaker labelling",
}


class MissingExtra(RuntimeError):
    """An optional dependency is not installed. Carries the fix."""

    def __init__(self, distribution: str, extra: str) -> None:
        self.distribution = distribution
        self.extra = extra
        purpose = EXTRAS.get(extra, "this stage")
        super().__init__(
            f"{distribution!r} is not installed, so {purpose} cannot run. "
            f'Install it with:  pip install -e ".[{extra}]"'
        )


def missing(distribution: str, *, extra: str) -> MissingExtra:
    """Build the error to raise `from` a failed optional import.

    Used as::

        try:
            from sentence_transformers import SentenceTransformer
        except ModuleNotFoundError as exc:
            raise missing("sentence-transformers", extra="embeddings") from exc

    The import stays written the ordinary way — readable, and still visible to
    the AST scan in `tests/unit/test_dependencies.py` that checks every import
    is declared somewhere in pyproject.toml.
    """
    return MissingExtra(distribution, extra)
