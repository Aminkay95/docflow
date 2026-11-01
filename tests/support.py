from __future__ import annotations

import hashlib
import io
import shutil
import tempfile
from pathlib import Path
from typing import Any

from trade_extract.services.limits import RawSubmission, SpooledDocument, detect_media_type


def make_pdf(page_texts: list[list[str]] | None = None) -> bytes:
    """Build a tiny valid PDF with an embedded text layer.

    Hand-rolled so tests do not depend on a PDF writer or a network fixture.
    """

    if page_texts is None:
        page_texts = [["Test document"]]

    page_count = len(page_texts)
    page_ids: list[int] = []
    next_id = 3
    for _ in range(page_count):
        page_ids.append(next_id)
        next_id += 2
    font_id = next_id
    content_ids = [page_id + 1 for page_id in page_ids]

    objects: list[str] = []
    objects.append("<< /Type /Catalog /Pages 2 0 R >>")
    kids = " ".join(f"{page_id} 0 R" for page_id in page_ids)
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {page_count} >>")
    for index in range(page_count):
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 {font_id} 0 R >> >> "
            f"/Contents {content_ids[index]} 0 R >>"
        )
        lines = ["BT", "/F1 12 Tf", "50 750 Td"]
        for line_number, line in enumerate(page_texts[index]):
            escaped = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            if line_number > 0:
                lines.append("0 -18 Td")
            lines.append(f"({escaped}) Tj")
        lines.append("ET")
        stream = "\n".join(lines)
        objects.append(f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream")
    objects.append("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    buffer = io.BytesIO()
    buffer.write(b"%PDF-1.4\n")
    offsets = [0]
    for number, body in enumerate(objects, start=1):
        offsets.append(buffer.tell())
        buffer.write(f"{number} 0 obj\n{body}\nendobj\n".encode("latin-1"))
    xref = buffer.tell()
    buffer.write(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    buffer.write(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        buffer.write(f"{offset:010d} 00000 n \n".encode("ascii"))
    buffer.write(
        (f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n").encode(
            "ascii"
        )
    )
    return buffer.getvalue()


def make_encrypted_pdf() -> bytes:
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    writer.encrypt("correct horse battery staple")
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def make_image(fmt: str = "JPEG", *, frames: int = 1) -> bytes:
    """Build a tiny, fully decodable image with Pillow."""

    from PIL import Image

    buffer = io.BytesIO()
    images = [Image.new("RGB", (16, 16), (32 * index % 255, 64, 96)) for index in range(frames)]
    if frames > 1:
        images[0].save(buffer, format=fmt, save_all=True, append_images=images[1:])
    else:
        images[0].save(buffer, format=fmt)
    return buffer.getvalue()


def make_multipage_tiff(frames: int = 3) -> bytes:
    return make_image("TIFF", frames=frames)


def truncated_image(data: bytes | None = None) -> bytes:
    payload = data if data is not None else make_image("JPEG")
    return payload[: len(payload) // 2]


JPEG_BYTES = make_image("JPEG")
DOCX_BYTES = b"PK\x03\x04" + b"\x00" * 128


def spooled_from_bytes(
    data: bytes, *, filename: str = "document.pdf", temp_dir: Path | None = None
) -> SpooledDocument:
    temp_dir = temp_dir or Path(tempfile.mkdtemp(prefix="trade-extract-test-"))
    path = temp_dir / filename
    path.write_bytes(data)
    detected = detect_media_type(data[:16])
    assert detected is not None
    return SpooledDocument(
        filename=filename,
        declared_content_type="application/octet-stream",
        temp_path=path,
        size_bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        detected_type=detected,
    )


def raw_submission(
    *documents: bytes,
    job_ref: str | None = None,
    webhook_url: str | None = None,
    filenames: list[str] | None = None,
) -> RawSubmission:
    temp_dir = Path(tempfile.mkdtemp(prefix="trade-extract-test-"))
    spooled = []
    for index, data in enumerate(documents):
        name = (
            (filenames or [])[index] if filenames and index < len(filenames) else f"doc-{index}.pdf"
        )
        spooled.append(spooled_from_bytes(data, filename=name, temp_dir=temp_dir))
    return RawSubmission(
        job_ref=job_ref,
        webhook_url=webhook_url,
        documents=spooled,
        temp_dir=temp_dir,
    )


def cleanup_raw(raw: RawSubmission | None) -> None:
    if raw is not None:
        shutil.rmtree(raw.temp_dir, ignore_errors=True)


def temp_snapshot() -> set[str]:
    return {str(path) for path in Path(tempfile.gettempdir()).glob("trade-extract-upload-*")}


def assert_no_envelope_content(payload: dict[str, Any]) -> None:
    text = str(payload)
    assert "page image" not in text.lower()
