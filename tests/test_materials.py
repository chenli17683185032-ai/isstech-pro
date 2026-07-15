"""Local materials are immutable, content-addressed, and safely classified."""

from __future__ import annotations

from io import BytesIO
import json
from pathlib import Path
import zipfile

import pytest

from isstech_replay.materials import (
    MaterialIntegrityError,
    MaterialService,
    MaterialTooLargeError,
)
from isstech_replay.models.materials import MaterialStatus
from isstech_replay.storage import WorkflowStorage
from tools import ingest_materials as ingest_cli


PDF_BYTES = b"%PDF-1.7\nREDACTED PDF CONTENT\n%%EOF\n"


def _service(tmp_path: Path, *, max_bytes: int = 1024 * 1024) -> MaterialService:
    return MaterialService(data_dir=tmp_path / "data", max_bytes=max_bytes)


def _docx_bytes() -> bytes:
    output = BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types />")
        archive.writestr("word/document.xml", "<document>REDACTED</document>")
    return output.getvalue()


def test_duplicate_ingest_returns_same_material_and_one_blob(tmp_path: Path) -> None:
    service = _service(tmp_path)
    first = service.ingest_stream(
        BytesIO(PDF_BYTES),
        original_name="specification.pdf",
        declared_mime_type="application/pdf",
    )
    second = service.ingest_stream(
        BytesIO(PDF_BYTES),
        original_name="specification.pdf",
        declared_mime_type="application/pdf",
    )

    assert first.material.status is MaterialStatus.READY
    assert first.blob_created is True
    assert first.deduplicated is False
    assert second.material.id == first.material.id
    assert second.blob_created is False
    assert second.deduplicated is True
    assert service.storage.table_count("material_blobs") == 1
    assert service.storage.table_count("materials") == 1

    original = service.resolve_original(first.material)
    assert original.read_bytes() == PDF_BYTES
    assert original.stat().st_mode & 0o777 == 0o400
    assert list(service.staging_root.glob("*")) == []


def test_same_content_different_names_share_blob_but_keep_references(tmp_path: Path) -> None:
    service = _service(tmp_path)
    first = service.ingest_stream(BytesIO(PDF_BYTES), original_name="one.pdf")
    second = service.ingest_stream(BytesIO(PDF_BYTES), original_name="two.pdf")

    assert first.material.id != second.material.id
    assert first.material.original_path == second.material.original_path
    assert second.blob_created is False
    assert second.deduplicated is False
    assert service.storage.table_count("material_blobs") == 1
    assert service.storage.table_count("materials") == 2


def test_office_zip_is_detected_without_extension_guessing(tmp_path: Path) -> None:
    service = _service(tmp_path)
    result = service.ingest_stream(
        BytesIO(_docx_bytes()),
        original_name="proposal.docx",
        declared_mime_type=(
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        ),
    )
    assert result.material.status is MaterialStatus.READY
    assert result.material.detected_mime_type.endswith("wordprocessingml.document")


def test_extension_and_declared_mime_mismatch_requires_review(tmp_path: Path) -> None:
    service = _service(tmp_path)
    png = b"\x89PNG\r\n\x1a\n" + b"REDACTED"
    result = service.ingest_stream(
        BytesIO(png),
        original_name="pretends-to-be-pdf.pdf",
        declared_mime_type="application/pdf",
    )
    assert result.material.status is MaterialStatus.NEEDS_REVIEW
    assert result.material.detected_mime_type == "image/png"
    assert "expects application/pdf" in result.material.review_reason
    assert "declared MIME application/pdf" in result.material.review_reason


def test_oversized_stream_leaves_no_record_or_original(tmp_path: Path) -> None:
    service = _service(tmp_path, max_bytes=4)
    with pytest.raises(MaterialTooLargeError):
        service.ingest_stream(BytesIO(b"12345"), original_name="too-large.bin")
    assert list(service.staging_root.glob("*")) == []
    assert list(service.originals_root.rglob("blob")) == []
    assert not (service.data_dir / "workflow-center.sqlite3").exists()


def test_interrupted_stream_leaves_no_valid_record(tmp_path: Path) -> None:
    class InterruptedStream:
        def __init__(self) -> None:
            self.calls = 0

        def read(self, size: int) -> bytes:
            del size
            self.calls += 1
            if self.calls == 1:
                return b"partial"
            raise OSError("REDACTED interrupted read")

    service = _service(tmp_path)
    with pytest.raises(OSError, match="interrupted"):
        service.ingest_stream(InterruptedStream(), original_name="partial.txt")  # type: ignore[arg-type]
    assert list(service.staging_root.glob("*")) == []
    assert list(service.originals_root.rglob("blob")) == []
    assert not (service.data_dir / "workflow-center.sqlite3").exists()


@pytest.mark.parametrize("name", ["../escape.pdf", "folder/file.pdf", "folder\\file.pdf"])
def test_path_like_original_names_are_rejected(tmp_path: Path, name: str) -> None:
    service = _service(tmp_path)
    with pytest.raises(ValueError, match="path separators"):
        service.ingest_stream(BytesIO(PDF_BYTES), original_name=name)
    assert not service.material_root.exists()


def test_symbolic_link_input_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "source.pdf"
    source.write_bytes(PDF_BYTES)
    link = tmp_path / "link.pdf"
    link.symlink_to(source)

    with pytest.raises(ValueError, match="symbolic-link"):
        _service(tmp_path).ingest_path(link)


def test_existing_corrupt_blob_is_detected_not_overwritten(tmp_path: Path) -> None:
    service = _service(tmp_path)
    result = service.ingest_stream(BytesIO(PDF_BYTES), original_name="one.pdf")
    original = service.resolve_original(result.material)
    original.chmod(0o600)
    original.write_bytes(b"CORRUPTED")

    with pytest.raises(MaterialIntegrityError, match="size does not match"):
        service.ingest_stream(BytesIO(PDF_BYTES), original_name="two.pdf")
    assert original.read_bytes() == b"CORRUPTED"
    assert service.storage.table_count("materials") == 1


def test_derived_directory_never_overlaps_original_blob(tmp_path: Path) -> None:
    service = _service(tmp_path)
    result = service.ingest_stream(BytesIO(PDF_BYTES), original_name="one.pdf")
    original = service.resolve_original(result.material)
    derived = service.derived_directory(result.material.id)
    assert not original.is_relative_to(derived)
    assert not derived.is_relative_to(original.parent)
    assert service.get(result.material.id) == result.material
    assert service.list() == (result.material,)


def test_ingest_cli_accepts_directory_and_recursive_mode(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = tmp_path / "incoming"
    nested = source / "nested"
    nested.mkdir(parents=True)
    (source / "one.pdf").write_bytes(PDF_BYTES)
    (source / "notes.txt").write_text("REDACTED notes", encoding="utf-8")
    (nested / "two.pdf").write_bytes(PDF_BYTES + b"2")
    data_dir = tmp_path / "data"

    assert ingest_cli.main([str(source), "--data-dir", str(data_dir), "--json"]) == 0
    first = json.loads(capsys.readouterr().out)
    assert first["ingested_count"] == 2
    assert first["failed_count"] == 0

    assert (
        ingest_cli.main(
            [str(source), "--recursive", "--data-dir", str(data_dir), "--json"]
        )
        == 0
    )
    second = json.loads(capsys.readouterr().out)
    assert second["ingested_count"] == 3
    storage = WorkflowStorage(data_dir / "workflow-center.sqlite3")
    assert storage.table_count("material_blobs") == 3
    assert storage.table_count("materials") == 3


def test_ingest_cli_refuses_runtime_data_as_input(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    source = data_dir / "do-not-reingest.txt"
    source.write_text("REDACTED", encoding="utf-8")

    assert ingest_cli.main([str(source), "--data-dir", str(data_dir), "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ingested_count"] == 0
    assert payload["failed_count"] == 1
    assert "runtime data directory" in payload["failures"][0]["message"]
