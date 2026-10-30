"""Content-based duplicate detection for ingested documents."""

import hashlib
import re
import unicodedata
from collections import Counter

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.document import Document

_SIMHASH_BITS = 64
_ADVISORY_LOCK_KEYS = (1146047317, 1296388676)
_WORD_PATTERN = re.compile(r"\w+", flags=re.UNICODE)


def normalize_document_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text).casefold()
    return re.sub(r"\s+", " ", normalized).strip()


def calculate_content_hash(text: str) -> str:
    normalized = normalize_document_text(text)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def calculate_text_fingerprint(text: str) -> str | None:
    """Create a stable 64-bit SimHash over weighted word trigrams."""
    normalized = normalize_document_text(text)
    words = _WORD_PATTERN.findall(normalized)
    if len(words) < 12:
        return None

    features = [
        " ".join(words[index : index + 3])
        for index in range(len(words) - 2)
    ]
    feature_counts = Counter(features)
    weights = [0] * _SIMHASH_BITS

    for feature, frequency in feature_counts.items():
        digest = hashlib.blake2b(
            feature.encode("utf-8"),
            digest_size=_SIMHASH_BITS // 8,
        ).digest()
        value = int.from_bytes(digest, byteorder="big")
        for bit_index in range(_SIMHASH_BITS):
            bit = (value >> (_SIMHASH_BITS - bit_index - 1)) & 1
            weights[bit_index] += frequency if bit else -frequency

    fingerprint = 0
    for bit_weight in weights:
        fingerprint = (fingerprint << 1) | int(bit_weight >= 0)
    return f"{fingerprint:016x}"


def simhash_similarity(first: str, second: str) -> float:
    first_value = int(first, 16)
    second_value = int(second, 16)
    distance = (first_value ^ second_value).bit_count()
    return 1.0 - distance / _SIMHASH_BITS


def find_duplicate_review_match(
    db: Session,
    document: Document,
    text: str,
) -> None:
    """Set review metadata without exposing or reusing another document."""
    content_hash = calculate_content_hash(text)
    fingerprint = calculate_text_fingerprint(text)

    if db.bind is not None and db.bind.dialect.name == "postgresql":
        db.execute(
            select(
                func.pg_advisory_xact_lock(
                    *_ADVISORY_LOCK_KEYS,
                )
            )
        )

    exact_match = db.scalar(
        select(Document.id)
        .where(
            Document.id != document.id,
            Document.content_hash == content_hash,
        )
        .limit(1)
    )
    if exact_match is not None:
        document.content_hash = content_hash
        document.text_fingerprint = fingerprint
        document.duplicate_review_status = "exact_content"
        document.duplicate_similarity = 1.0
        return

    candidates = (
        db.scalars(
            select(Document.text_fingerprint).where(
                Document.id != document.id,
                Document.text_fingerprint.is_not(None),
            )
        ).all()
        if fingerprint is not None
        else []
    )
    best_similarity = max(
        (
            simhash_similarity(fingerprint, candidate)
            for candidate in candidates
            if fingerprint is not None and candidate is not None
        ),
        default=0.0,
    )

    document.content_hash = content_hash
    document.text_fingerprint = fingerprint
    if best_similarity >= settings.document_near_duplicate_threshold:
        document.duplicate_review_status = "near_duplicate"
        document.duplicate_similarity = best_similarity
    else:
        document.duplicate_review_status = "none"
        document.duplicate_similarity = None
