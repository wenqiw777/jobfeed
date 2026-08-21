"""Résumé onboarding upload, extraction, and profile-draft contracts."""

from __future__ import annotations

import io
import stat
import zipfile
from pathlib import Path

import pytest

from jobfeed.onboarding_resume import (
    ResumeDraftStore,
    ResumeFileStore,
    ResumeOnboardingService,
    parse_job_profile,
)
from jobfeed.onboarding_resume_types import JobProfile
from jobfeed.onboarding_types import ProviderOnboardingState

PRIVATE_FILE_MODE = 0o600


class FakeAnalyzer:
    """Return one complete, deterministic profile suggestion."""

    async def analyze(
        self,
        provider: str,
        model: str,
        resume_text: str,
    ) -> JobProfile:
        """Assert provider context and return an editable suggestion."""
        assert provider == "codex_cli"
        assert model == "gpt-5.6-sol"
        assert "Python platform engineer" in resume_text
        return _profile(desired_titles=["Platform Engineer"])


def _provider_state() -> ProviderOnboardingState:
    return ProviderOnboardingState(
        provider="codex_cli",
        connected=True,
        detailed_model="gpt-5.6-sol",
    )


def _profile(**updates: object) -> JobProfile:
    data: dict[str, object] = {
        "desired_titles": ["Software Engineer"],
        "seniority_levels": ["Senior"],
        "target_countries": ["United States"],
        "target_locations": ["New York, NY"],
        "work_modes": ["remote", "hybrid"],
        "industries": ["Developer tools"],
        "company_sizes": ["startup", "mid-size"],
        "work_authorization": "Authorized to work in the US",
        "hiring_timeline": "Available immediately",
        "excluded_titles": ["QA Engineer"],
        "excluded_companies": [],
        "excluded_locations": [],
        "excluded_keywords": ["unpaid"],
        "maximum_posting_age_days": 14,
        "resume_evidence": ["Built Python services"],
    }
    data.update(updates)
    return JobProfile.model_validate(data)


@pytest.mark.parametrize(
    ("filename", "content"),
    [
        ("resume.md", b"# Sample Candidate\n\nPython platform engineer"),
        ("resume.txt", b"Sample Candidate\nPython platform engineer"),
        ("resume.docx", lambda: _docx_bytes("Python platform engineer")),
        ("resume.pdf", lambda: _pdf_bytes("Python platform engineer")),
    ],
)
def test_supported_resume_uploads_are_private_and_extract_text(
    tmp_path: Path,
    filename: str,
    content: bytes | object,
) -> None:
    """Every frozen upload format is extracted locally and stored privately."""
    payload = content() if callable(content) else content
    store = ResumeFileStore(tmp_path / "data" / "resumes")

    saved = store.save(filename, payload)

    assert "Python platform engineer" in saved.extracted_text
    assert saved.path.parent == (tmp_path / "data" / "resumes").resolve()
    assert stat.S_IMODE(saved.path.stat().st_mode) == PRIVATE_FILE_MODE


@pytest.mark.parametrize(
    ("filename", "content", "message"),
    [
        ("resume.rtf", b"resume", "PDF, DOCX, Markdown, or plain text"),
        ("resume.txt", b"\xff\xfe", "valid UTF-8"),
        ("resume.pdf", lambda: _pdf_bytes(""), "no extractable text"),
    ],
)
def test_invalid_resume_uploads_are_rejected_without_persistence(
    tmp_path: Path,
    filename: str,
    content: bytes | object,
    message: str,
) -> None:
    """Unsupported, undecodable, and image-only documents fail clearly."""
    payload = content() if callable(content) else content
    store = ResumeFileStore(tmp_path / "data" / "resumes")

    with pytest.raises(ValueError, match=message):
        store.save(filename, payload)

    assert list((tmp_path / "data" / "resumes").glob("*")) == []


async def test_upload_analysis_edit_confirmation_and_resume_round_trip(
    tmp_path: Path,
) -> None:
    """The draft preserves extracted text, AI suggestions, and user edits."""
    drafts = ResumeDraftStore(tmp_path / "data" / "onboarding-resume.json")
    service = ResumeOnboardingService(
        files=ResumeFileStore(tmp_path / "data" / "resumes"),
        drafts=drafts,
        analyzer=FakeAnalyzer(),
        provider_state=_provider_state,
    )

    uploaded = service.upload("../resume.md", b"Python platform engineer")
    suggested = await service.analyze()
    edited = suggested.profile.model_copy(
        update={"desired_titles": ["Staff Platform Engineer"]}
    )
    confirmed = service.confirm(edited)
    resumed = drafts.load()

    assert uploaded.original_name == "resume.md"
    assert uploaded.stored_name.endswith("-resume.md")
    assert suggested.profile.desired_titles == ["Platform Engineer"]
    assert confirmed.is_confirmed is True
    assert resumed.profile is not None
    assert resumed.profile.desired_titles == ["Staff Platform Engineer"]
    assert resumed.is_confirmed is True


def test_malformed_analysis_response_is_rejected() -> None:
    """A provider cannot silently omit required structured profile fields."""
    with pytest.raises(ValueError, match="valid job profile"):
        parse_job_profile('{"desired_titles": ["Engineer"]}')


def _docx_bytes(text: str) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/'
            'content-types"><Default Extension="rels" ContentType="application/'
            'vnd.openxmlformats-package.relationships+xml"/><Default '
            'Extension="xml" ContentType="application/xml"/><Override '
            'PartName="/word/document.xml" ContentType="application/vnd.'
            'openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            "</Types>",
        )
        archive.writestr(
            "_rels/.rels",
            '<?xml version="1.0"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/'
            '2006/relationships"><Relationship Id="rId1" Type="http://schemas.'
            "openxmlformats.org/officeDocument/2006/relationships/"
            'officeDocument" Target="word/document.xml"/></Relationships>',
        )
        archive.writestr(
            "word/document.xml",
            '<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>'
            + text
            + "</w:t></w:r></w:p></w:body></w:document>",
        )
    return output.getvalue()


def _pdf_bytes(text: str) -> bytes:
    escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream = f"BT /F1 12 Tf 72 100 Td ({escaped}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 200] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length "
        + str(len(stream)).encode()
        + b" >>\nstream\n"
        + stream
        + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    document = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, body in enumerate(objects, start=1):
        offsets.append(len(document))
        document.extend(f"{index} 0 obj\n".encode() + body + b"\nendobj\n")
    xref = len(document)
    document.extend(f"xref\n0 {len(objects) + 1}\n".encode())
    document.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        document.extend(f"{offset:010d} 00000 n \n".encode())
    trailer = {"Size": len(objects) + 1, "Root": "1 0 R"}
    document.extend(
        b"trailer\n<< /Size "
        + str(trailer["Size"]).encode()
        + b" /Root 1 0 R >>\nstartxref\n"
        + str(xref).encode()
        + b"\n%%EOF\n"
    )
    return bytes(document)
