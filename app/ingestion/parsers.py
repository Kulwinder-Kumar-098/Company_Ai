"""Document parsers."""

from pathlib import Path
from typing import TypedDict

from pypdf import PdfReader


class ExtractedPage(TypedDict):
    source: str
    page_number: int
    text: str


def extract_pdf_pages(pdf_path: str | Path) -> list[ExtractedPage]:
    """
    Extract text from a PDF, preserving its filename and page numbers.

    Page numbers are 1-based for human-readable citations.
    Pages without extractable text are retained with empty text.
    """
    path = Path(pdf_path)

    if not path.is_file():
        raise FileNotFoundError(f"PDF file not found: {path}")

    try:
        reader = PdfReader(str(path))

        if reader.is_encrypted:
            raise ValueError(
                f"Encrypted PDFs are not supported: {path.name}"
            )

        extracted_pages: list[ExtractedPage] = []

        for page_number, page in enumerate(reader.pages, start=1):
            text = page.extract_text() or ""

            # Normalize line endings and remove null characters.
            text = text.replace("\x00", "").replace("\r\n", "\n")
            text = text.replace("\r", "\n").strip()

            extracted_pages.append(
                {
                    "source": path.name,
                    "page_number": page_number,
                    "text": text,
                }
            )

        return extracted_pages

    except ValueError:
        raise
    except Exception as exc:
        raise RuntimeError(
            f"Failed to extract text from {path.name}: {exc}"
        ) from exc
