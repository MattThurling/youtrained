"""Evidence PDF via WeasyPrint. Optional: install with `uv sync --extra pdf` (needs pango)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

# On macOS, Homebrew's pango/gobject live in /opt/homebrew/lib (Apple silicon) or
# /usr/local/lib (Intel). Python's library finder consults this env var at call time,
# so setting it before WeasyPrint's import is enough; a uv-managed Python does not look there.
if sys.platform == "darwin":
    _brew = [d for d in ("/opt/homebrew/lib", "/usr/local/lib") if Path(d).is_dir()]
    if _brew:
        _existing = os.environ.get("DYLD_FALLBACK_LIBRARY_PATH", "")
        os.environ["DYLD_FALLBACK_LIBRARY_PATH"] = ":".join([*_brew, _existing]).rstrip(":")


class PdfUnavailable(RuntimeError):
    pass


def html_to_pdf(html: str, base_url: str | None = None) -> bytes:
    try:
        from weasyprint import HTML
    except Exception as exc:  # ImportError or missing native libs (pango/cairo)
        raise PdfUnavailable(
            "PDF export needs WeasyPrint and its native libraries: run "
            "`brew install pango` then `uv sync --extra pdf`."
        ) from exc
    return HTML(string=html, base_url=base_url).write_pdf()
