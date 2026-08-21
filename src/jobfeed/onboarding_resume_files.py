"""Validate, extract, and privately persist original résumé uploads."""

from __future__ import annotations

import hashlib
import io
import os
import re
import tempfile
from pathlib import Path

from docx import Document
from pypdf import PdfReader

from jobfeed.onboarding_resume_types import StoredResume

_ALLOWED_EXTENSIONS = frozenset({".pdf", ".docx", ".md", ".txt"})
_MAX_UPLOAD_BYTES = 10 * 1024 * 1024


class ResumeFileStore:
    """Validate, extract, and privately persist original résumé uploads."""

    def __init__(self, root: Path, max_bytes: int = _MAX_UPLOAD_BYTES) -> None:
        """Create a store constrained to ``data/resumes``."""
        self._root = root.resolve()
        self._max_bytes = max_bytes

    def save(self, filename: str, content: bytes) -> StoredResume:
        """Validate, extract, and atomically save one original résumé.

        Args:
            filename: Browser-supplied original filename.
            content: Uploaded bytes.

        Returns:
            Stored path and locally extracted text.

        Raises:
            ValueError: If the type, size, encoding, or text is invalid.
        """
        original_name = _safe_filename(filename)
        extension = Path(original_name).suffix.lower()
        if extension not in _ALLOWED_EXTENSIONS:
            raise ValueError("Upload a PDF, DOCX, Markdown, or plain text résumé")
        if not content:
            raise ValueError("The résumé file is empty")
        if len(content) > self._max_bytes:
            raise ValueError("The résumé file must be 10 MB or smaller")
        extracted_text = _extract_text(extension, content).strip()
        if not extracted_text:
            raise ValueError("The résumé contains no extractable text")

        digest = hashlib.sha256(content).hexdigest()[:12]
        stored_name = f"{digest}-{original_name}"
        self._root.mkdir(parents=True, exist_ok=True)
        path = self._root / stored_name
        _write_private_bytes(path, content)
        return StoredResume(
            path=path,
            stored_name=stored_name,
            original_name=original_name,
            extracted_text=extracted_text,
        )

    def delete(self, stored_name: str) -> None:
        """Delete one superseded upload only when it belongs to this store.

        Args:
            stored_name: Store-generated basename to remove.

        Raises:
            ValueError: If the name cannot resolve inside this store.
        """
        candidate = (self._root / Path(stored_name).name).resolve()
        if candidate.parent != self._root:
            raise ValueError("Invalid stored résumé name")
        candidate.unlink(missing_ok=True)


def _safe_filename(filename: str) -> str:
    name = Path(filename.replace("\\", "/")).name.strip()
    cleaned = re.sub(r"[^A-Za-z0-9._ -]", "-", name)
    return cleaned or "resume"


def _extract_text(extension: str, content: bytes) -> str:
    if extension in {".md", ".txt"}:
        try:
            return content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("The résumé must contain valid UTF-8 text") from exc
    if extension == ".docx":
        try:
            document = Document(io.BytesIO(content))
        except Exception as exc:
            raise ValueError("The DOCX résumé could not be read") from exc
        return "\n".join(paragraph.text for paragraph in document.paragraphs)
    try:
        reader = PdfReader(io.BytesIO(content))
        if reader.is_encrypted:
            raise ValueError("Encrypted PDF résumés are not supported")
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("The PDF résumé could not be read") from exc


def _write_private_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


__all__ = ["ResumeFileStore"]
