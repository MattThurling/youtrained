"""Evidence PDF via WeasyPrint. Optional: install with `uv sync --extra pdf` (needs pango).

Each render runs in a short-lived child process (`python -m youtrained.pdf`). WeasyPrint keeps
roughly 100 KB per document in process-wide caches; rendered in the server process that leaked
the 700 MB container limit about once a day. A child gives it all back on exit.
"""

from __future__ import annotations

import os
import subprocess
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

RENDER_TIMEOUT_S = 90
EXIT_UNAVAILABLE = 3  # child could not import WeasyPrint or its native libraries

INSTALL_HINT = (
    "PDF export needs WeasyPrint and its native libraries: run "
    "`brew install pango` then `uv sync --extra pdf`."
)


class PdfUnavailable(RuntimeError):
    pass


class PdfFailed(RuntimeError):
    """The render child crashed, ran out of memory, or timed out."""


def _prefer_killing_me() -> None:
    """Called first thing in the child. If the container runs out of memory mid-render, make the
    kernel's OOM killer pick this process rather than the server (Linux only; harmless elsewhere).

    Done in the child, not via `preexec_fn`: a fork hook that runs Python code can deadlock in a
    multithreaded parent such as the uvicorn worker pool (seen in production: every render timed out).
    """
    try:
        Path("/proc/self/oom_score_adj").write_text("1000")
    except OSError:
        pass


def html_to_pdf(html: str, base_url: str | None = None) -> bytes:
    cmd = [sys.executable, "-m", "youtrained.pdf"]
    if base_url:
        cmd.append(base_url)
    try:
        proc = subprocess.run(
            cmd,
            input=html.encode("utf-8"),
            capture_output=True,
            timeout=RENDER_TIMEOUT_S,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise PdfFailed(f"PDF render exceeded {RENDER_TIMEOUT_S}s") from exc
    if proc.returncode == EXIT_UNAVAILABLE:
        raise PdfUnavailable(INSTALL_HINT)
    if proc.returncode != 0 or not proc.stdout.startswith(b"%PDF"):
        tail = proc.stderr.decode("utf-8", "replace").strip().splitlines()[-3:]
        raise PdfFailed(f"PDF render failed (exit {proc.returncode}): {' | '.join(tail)}")
    return proc.stdout


def _render_in_child(html: str, base_url: str | None) -> bytes:
    """Body of the child process; separate so it can be exercised in-process by tests."""
    try:
        from weasyprint import HTML
    except Exception as exc:  # ImportError or missing native libs (pango/cairo)
        raise PdfUnavailable(INSTALL_HINT) from exc
    return HTML(string=html, base_url=base_url).write_pdf()


def main() -> int:
    _prefer_killing_me()
    html = sys.stdin.buffer.read().decode("utf-8")
    base_url = sys.argv[1] if len(sys.argv) > 1 else None
    try:
        pdf = _render_in_child(html, base_url)
    except PdfUnavailable as exc:
        print(exc, file=sys.stderr)
        return EXIT_UNAVAILABLE
    sys.stdout.buffer.write(pdf)
    sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
