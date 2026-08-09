"""Document text extraction. Uses PyMuPDF for PDFs; plain text for .txt."""

from __future__ import annotations

from pathlib import Path


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
