"""Every import is declared in pyproject.toml.

This exists because three of them were not.

`sentence-transformers`, `rapidocr-onnxruntime` and `google-genai` were
imported lazily inside functions — invisible to a top-of-file scan, absent from
pyproject, and present on the one machine that had pip-installed them by hand.
A fresh clone installed cleanly and then failed at the first embed, rerank, OCR
or question. CI did not catch it because the suite stubs those models out, so
the tree that shipped was one no new contributor could run.

The same class of bug had already bitten twice, with `pgvector` and `pillow`.
The pattern is always a package that is present locally for some incidental
reason, so nobody notices it was never declared.

Reading imports out of the source is the only check that does not depend on
what happens to be installed in the environment running it — which is exactly
the assumption that let this through.
"""

from __future__ import annotations

import ast
import pathlib
import re
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
APP = REPO / "app"
PYPROJECT = REPO / "pyproject.toml"

# Import name -> distribution name, where they differ. Anything not listed is
# assumed to be the module name with underscores swapped for hyphens, which
# covers `sentence_transformers`, `pydantic_settings`, `faster_whisper` and most
# others. Only genuine surprises need an entry.
DISTRIBUTION_OF = {
    # `from google import genai` — the namespace package is `google`, and
    # several unrelated distributions publish into it.
    "google": "google-genai",
    "PIL": "pillow",
    "yaml": "pyyaml",
    "multipart": "python-multipart",
    "dotenv": "python-dotenv",
    # The nvidia-*-cu12 wheels install into a shared `nvidia` namespace.
    "nvidia": "nvidia-cublas-cu12",
}

# Imported directly, but supplied by something else that *is* declared, and
# guarded by try/except at the call site so absence degrades rather than
# crashes. Each entry needs a reason; an unexplained one is just this bug
# wearing a disguise.
ALLOWED_TRANSITIVE = {
    # Pulled in by sentence-transformers. app/pipeline/{embedding,rerank}.py
    # import it only to ask whether CUDA exists, inside a try/except ImportError
    # that falls back to CPU.
    "torch": "sentence-transformers",
    # Pulled in by faster-whisper. app/gpu.py uses it to count CUDA devices,
    # inside a try/except ImportError.
    "ctranslate2": "faster-whisper",
}


def _normalise(name: str) -> str:
    """PEP 503 name normalisation: `Foo_Bar.baz` and `foo-bar-baz` are one name."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _declared_distributions() -> set[str]:
    """Every distribution named in pyproject, across core and all extras.

    Read with a regex rather than tomllib so a malformed edit fails loudly here
    rather than being silently skipped.
    """
    text = PYPROJECT.read_text(encoding="utf-8")
    names = set()
    for raw in re.findall(r'"([^"]+)"', text):
        # Strip extras, version specifiers and environment markers:
        #   sqlalchemy[asyncio]>=2.0.36
        #   nvidia-cublas-cu12; platform_system == 'Windows'
        name = re.split(r"[<>=!~;\[]", raw, maxsplit=1)[0].strip()
        if name and re.fullmatch(r"[A-Za-z0-9._-]+", name):
            names.add(_normalise(name))
    return names


def _imported_roots() -> dict[str, set[str]]:
    """Root module of every absolute import under `app/`, at any nesting depth.

    `ast.walk` rather than reading only module-level statements: the imports
    this test was written for all live inside functions.
    """
    roots: dict[str, set[str]] = {}
    for path in sorted(APP.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    roots.setdefault(alias.name.split(".")[0], set()).add(path.name)
            elif isinstance(node, ast.ImportFrom):
                if node.level or not node.module:
                    continue  # relative import: first-party by definition
                roots.setdefault(node.module.split(".")[0], set()).add(path.name)
    return roots


def _third_party() -> dict[str, set[str]]:
    return {
        module: files
        for module, files in _imported_roots().items()
        if module not in sys.stdlib_module_names and module != "app"
    }


def test_the_scan_finds_something() -> None:
    """A scan that silently matched nothing would pass every other test here."""
    found = _third_party()
    assert len(found) >= 10, f"only found {sorted(found)} — the scan is broken"
    assert "sqlalchemy" in found


@pytest.mark.parametrize("module", sorted(_third_party()))
def test_import_is_declared(module: str) -> None:
    """Each third-party import maps to a distribution named in pyproject."""
    if module in ALLOWED_TRANSITIVE:
        provider = _normalise(ALLOWED_TRANSITIVE[module])
        assert provider in _declared_distributions(), (
            f"{module!r} is allowed as a transitive import of {provider!r}, "
            f"but {provider!r} is not declared either."
        )
        return

    distribution = _normalise(DISTRIBUTION_OF.get(module, module))
    declared = _declared_distributions()
    files = ", ".join(sorted(_third_party()[module]))

    assert distribution in declared, (
        f"{module!r} is imported by {files} but {distribution!r} is not declared "
        f"in pyproject.toml.\n"
        f"Add it to [project] dependencies or one of the optional-dependencies "
        f"groups. If it arrives transitively and its import is guarded, add it "
        f"to ALLOWED_TRANSITIVE in this file with the reason."
    )


def test_every_extra_named_in_code_exists() -> None:
    """`missing(..., extra=...)` must name a real extra.

    The error tells the reader to run `pip install -e ".[<extra>]"`. An extra
    that does not exist turns a clear message into a confusing one.
    """
    from app.extras import EXTRAS

    text = PYPROJECT.read_text(encoding="utf-8")
    groups = set(re.findall(r"^([a-z][a-z0-9-]*) = \[", text, re.M))

    unknown = set(EXTRAS) - groups
    assert not unknown, f"app/extras.py names extras that pyproject does not define: {unknown}"


def test_optional_imports_explain_themselves() -> None:
    """Optional dependencies are wrapped, not left to raise ModuleNotFoundError.

    Each of these packages is absent from a default install. Unwrapped, the
    failure reaches the user as a traceback from a worker thread naming an
    import they have no reason to connect to an extra.
    """
    optional = {
        "sentence_transformers": ("pipeline/embedding.py", "pipeline/rerank.py"),
        "rapidocr_onnxruntime": ("pipeline/ocr.py",),
        "faster_whisper": ("pipeline/transcribe.py",),
    }

    for module, files in optional.items():
        for relative in files:
            source = (APP / relative).read_text(encoding="utf-8")
            index = source.index(f"from {module} import")
            window = source[max(0, index - 200) : index + 300]
            assert "except ModuleNotFoundError" in window, (
                f"{relative}: `from {module} import ...` is not wrapped, so a "
                f"missing extra raises a bare ModuleNotFoundError."
            )
