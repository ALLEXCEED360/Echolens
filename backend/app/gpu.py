"""CUDA runtime discovery.

**Import this before faster_whisper / ctranslate2.** It mutates PATH, and the
libraries resolve their CUDA dependencies at first use.

The Windows problem this solves: ctranslate2 loads cuBLAS and cuDNN lazily from
its own C++ code via a plain `LoadLibrary` call, which follows the default
Windows search order — PATH included, but *not* directories registered with
`os.add_dll_directory()` (that only affects Python's own extension loading).

So when CUDA is installed via the `nvidia-*-cu12` pip wheels rather than a
system CUDA toolkit, the DLLs sit inside site-packages where nothing looks, and
`get_cuda_device_count()` cheerfully reports 1 while the first inference dies
with "Library cublas64_12.dll is not found or cannot be loaded".
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

_registered = False


def register_cuda_libraries() -> list[Path]:
    """Prepend pip-installed NVIDIA DLL directories to PATH. Idempotent."""
    global _registered
    if _registered:
        return []

    try:
        import nvidia
    except ImportError:
        logger.debug("nvidia-* wheels not installed; relying on a system CUDA toolkit")
        _registered = True
        return []

    dirs: list[Path] = []
    # `nvidia` is a namespace package: __file__ is None, so iterate __path__.
    for root in map(Path, nvidia.__path__):
        for dll in root.rglob("*.dll"):
            if dll.parent not in dirs:
                dirs.append(dll.parent)

    if dirs:
        os.environ["PATH"] = (
            os.pathsep.join(str(d) for d in dirs) + os.pathsep + os.environ.get("PATH", "")
        )
        logger.debug("Registered %d CUDA library directories", len(dirs))

    _registered = True
    return dirs


def resolve_device(preference: str) -> tuple[str, str]:
    """Map a device preference to a concrete `(device, compute_type)` pair.

    `preference` is `auto`, `cuda` or `cpu`. Falls back to CPU with int8 when
    CUDA is unavailable, so a missing GPU degrades rather than crashes.
    """
    register_cuda_libraries()

    if preference == "cpu":
        return "cpu", "int8"

    try:
        import ctranslate2

        if ctranslate2.get_cuda_device_count() > 0:
            supported = ctranslate2.get_supported_compute_types("cuda")
            # float16 halves VRAM against float32 with no meaningful accuracy
            # cost for Whisper, which matters on an 8 GB card.
            compute = "float16" if "float16" in supported else "float32"
            return "cuda", compute
    except Exception:
        logger.warning("CUDA probe failed; falling back to CPU", exc_info=True)

    if preference == "cuda":
        logger.warning("device=cuda requested but no CUDA device is usable; using CPU")

    return "cpu", "int8"
