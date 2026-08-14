"""Document text extraction + office→PDF conversion.

- PDFs: PyMuPDF (fitz) for text + page count.
- Plain text (.txt/.md): read as UTF-8.
- Office docs (.pptx/.docx/.xlsx/.doc/.ppt/.xls): converted to PDF via
  LibreOffice headless first, then parsed as a PDF. The conversion gives us
  both a renderable PDF (for the in-app viewer) and extractable text.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

# Office formats handled via LibreOffice conversion.
OFFICE_SUFFIXES = {".pptx", ".docx", ".xlsx", ".doc", ".ppt", ".xls"}


def extract_text(path: Path, mime: str) -> tuple[str, int]:
    """Extract text from a file. Returns (text, page_count).

    page_count is pages for PDFs, 1 for plain text.
    """
    suffix = path.suffix.lower()
    if suffix == ".pdf" or mime == "application/pdf":
        return _extract_pdf(path)
    # Default: treat as plain text (handles .txt, .md).
    text = path.read_text(encoding="utf-8", errors="replace")
    return text, 1


def _extract_pdf(path: Path) -> tuple[str, int]:
    import fitz  # PyMuPDF

    doc = fitz.open(path)
    try:
        pages: list[str] = []
        for page in doc:
            pages.append(page.get_text("text"))
        return "\n\n".join(pages).strip(), doc.page_count
    finally:
        doc.close()


def convert_office_to_pdf(source: Path) -> Path:
    """Convert an office document (.pptx/.docx/.xlsx/.doc/.ppt/.xls) to PDF.

    Uses LibreOffice headless. Returns the path to the produced PDF in a
    temporary directory (caller is responsible for moving/deleting it).

    Raises RuntimeError if LibreOffice (soffice) is not installed or the
    conversion fails.
    """
    soffice = shutil.which("soffice")
    if not soffice:
        raise RuntimeError(
            "LibreOffice (soffice) is not installed — cannot convert office "
            "documents. Install it with `brew install --cask libreoffice`."
        )

    # Use a unique temp dir per call. LibreOffice uses a single-user profile
    # per UserInstallation URL, so a unique one per call avoids concurrent
    # uploads contending for the same profile lock.
    with tempfile.TemporaryDirectory(prefix="soffice_") as outdir:
        profile_dir = Path(outdir) / "profile"
        cmd = [
            soffice,
            "--headless",
            "--norestore",
            "--nodefault",
            f"-env:UserInstallation=file://{profile_dir}",
            "--convert-to",
            "pdf",
            "--outdir",
            outdir,
            str(source),
        ]
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"LibreOffice conversion failed (exit {result.returncode}): "
                f"{result.stderr.strip() or result.stdout.strip()}"
            )

        produced = Path(outdir) / f"{source.stem}.pdf"
        if not produced.exists():
            raise RuntimeError(
                "LibreOffice conversion produced no output file."
            )

        # Copy out of the temp dir before it's removed.
        dest = Path(tempfile.mkdtemp(prefix="soffice_out_")) / f"{source.stem}.pdf"
        shutil.move(str(produced), str(dest))
        return dest

