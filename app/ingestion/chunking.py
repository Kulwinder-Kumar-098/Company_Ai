"""Document chunking."""

"""Clean extracted document text and split it into overlapping chunks."""

import re
from typing import TypedDict

from app.ingestion.parsers import ExtractedPage


class DocumentChunk(TypedDict):
    chunk_id: str
    source: str
    page_number: int
    chunk_index: int
    text: str


def clean_text(text: str) -> str:
    """Normalize whitespace while preserving paragraph boundaries."""
    text = text.replace("\x00", "")
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    # Remove repeated horizontal whitespace.
    text = re.sub(r"[ \t]+", " ", text)

    # Normalize excessive blank lines.
    text = re.sub(r"\n[ \t]*\n+", "\n\n", text)

    # Remove spaces around line breaks.
    text = re.sub(r" *\n *", "\n", text)

    return text.strip()


def _split_long_text(text: str, chunk_size: int, overlap: int) -> list[str]:
    """Split long text using word boundaries where possible."""
    chunks: list[str] = []
    start = 0
    text_length = len(text)

    while start < text_length:
        end = min(start + chunk_size, text_length)

        # Prefer ending at a word boundary.
        if end < text_length:
            boundary = text.rfind(" ", start, end)
            if boundary > start:
                end = boundary

        piece = text[start:end].strip()

        if piece:
            chunks.append(piece)

        if end >= text_length:
            break

        # Ensure forward progress even with unusual input.
        next_start = max(start + 1, end - overlap)

        while next_start < text_length and text[next_start].isspace():
            next_start += 1

        start = next_start

    return chunks


def chunk_page(
    text: str,
    chunk_size: int = 1000,
    overlap: int = 150,
) -> list[str]:
    """
    Split one page into chunks.

    chunk_size and overlap are measured in characters, not tokens.
    Paragraph boundaries are preferred when possible.
    """
    if chunk_size <= 0:
        raise ValueError("chunk_size must be greater than zero")

    if overlap < 0 or overlap >= chunk_size:
        raise ValueError("overlap must be >= 0 and < chunk_size")

    cleaned = clean_text(text)

    if not cleaned:
        return []

    if len(cleaned) <= chunk_size:
        return [cleaned]

    paragraphs = [
        paragraph.strip()
        for paragraph in re.split(r"\n\s*\n", cleaned)
        if paragraph.strip()
    ]

    chunks: list[str] = []
    current = ""

    for paragraph in paragraphs:
        if len(paragraph) > chunk_size:
            if current:
                chunks.append(current)
                current = ""

            chunks.extend(
                _split_long_text(paragraph, chunk_size, overlap)
            )
            continue

        candidate = f"{current}\n\n{paragraph}" if current else paragraph

        if len(candidate) <= chunk_size:
            current = candidate
            continue

        if current:
            chunks.append(current)

        # Carry trailing context into the next chunk.
        if overlap:
            context = current[-overlap:].strip()
        else:
            context = ""

        if context:
            candidate = f"{context}\n\n{paragraph}"
        else:
            candidate = paragraph

        if len(candidate) <= chunk_size:
            current = candidate
        else:
            # A paragraph can exceed the available space after overlap.
            pieces = _split_long_text(candidate, chunk_size, overlap)
            chunks.extend(pieces[:-1])
            current = pieces[-1] if pieces else ""

    if current:
        chunks.append(current)

    return chunks


def chunk_pages(
    pages: list[ExtractedPage],
    chunk_size: int = 1000,
    overlap: int = 150,
) -> list[DocumentChunk]:
    """Chunk extracted pages and retain source metadata for citations."""
    result: list[DocumentChunk] = []

    for page in pages:
        page_chunks = chunk_page(
            page["text"],
            chunk_size=chunk_size,
            overlap=overlap,
        )

        for chunk_index, text in enumerate(page_chunks, start=1):
            result.append(
                {
                    "chunk_id": (
                        f"{page['source']}:"
                        f"p{page['page_number']}:"
                        f"c{chunk_index}"
                    ),
                    "source": page["source"],
                    "page_number": page["page_number"],
                    "chunk_index": chunk_index,
                    "text": text,
                }
            )

    return result
