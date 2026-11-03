import io
from pathlib import Path

import pytest
from pypdf import PdfReader, PdfWriter

from tests.support import make_pdf
from trade_extract.domain.provenance import BBox
from trade_extract.domain.readers import PageInput, TextCharacter
from trade_extract.services.page_model import render_container
from trade_extract.services.readers.text_layer import verify_text_box


def test_pdf_geometry_is_real_and_citations_must_match(tmp_path: Path):
    path = tmp_path / "digital.pdf"
    path.write_bytes(make_pdf([["Invoice ABC-123"]]))
    rendered = next(
        render_container(
            path, "application/pdf", document="test", dpi=72, thumbnail_max_px=128, quality=85
        )
    )
    page = PageInput(0, characters=rendered.characters)
    assert page.text == "Invoice ABC-123"
    assert rendered.characters[0].bbox.x0 > 0.08
    assert rendered.characters[0].bbox.y0 < 0.07
    chars = rendered.characters[8:]
    box = BBox(
        x0=min(c.bbox.x0 for c in chars),
        y0=min(c.bbox.y0 for c in chars),
        x1=max(c.bbox.x1 for c in chars),
        y1=max(c.bbox.y1 for c in chars),
    )
    assert verify_text_box(page, "ABC-123", box)
    assert not verify_text_box(page, "invented", box)
    assert not verify_text_box(page, "ABC-123", BBox(x0=0, y0=0, x1=1, y1=1))


@pytest.mark.parametrize(
    "rotation, expected",
    [
        (90, (750 / 792, 51.116 / 612, 758.592 / 792, 52.256 / 612)),
        (180, ((612 - 52.256) / 612, 750 / 792, (612 - 51.116) / 612, 758.592 / 792)),
        (
            270,
            ((792 - 758.592) / 792, (612 - 52.256) / 612, (792 - 750) / 792, (612 - 51.116) / 612),
        ),
    ],
)
def test_pdf_rotation_coordinates(tmp_path, rotation, expected):
    # Helvetica I at (50, 750), size 12 has outline rectangle
    # (51.116, 750, 52.256, 758.592) in this synthetic PDF's 612 x 792 media box.
    # Expected rectangles are calculated directly in the rotated page coordinate system.
    writer = PdfWriter()
    writer.add_page(PdfReader(io.BytesIO(make_pdf([["I"]]))).pages[0].rotate(rotation))
    path = tmp_path / "rotated.pdf"
    writer.write(path)
    rendered = next(
        render_container(
            path, "application/pdf", document="test", dpi=72, thumbnail_max_px=128, quality=85
        )
    )
    box = rendered.characters[0].bbox
    assert (box.x0, box.y0, box.x1, box.y1) == pytest.approx(expected, abs=0.000001)


def test_numeric_span_cannot_end_inside_a_larger_amount():
    chars = tuple(
        TextCharacter(char, BBox(x0=index / 10, y0=0, x1=(index + 1) / 10, y1=0.1))
        for index, char in enumerate("100")
    )
    page = PageInput(0, characters=chars)
    assert not verify_text_box(page, "10", BBox(x0=0, y0=0, x1=0.2, y1=0.1))
    assert verify_text_box(page, "100", BBox(x0=0, y0=0, x1=0.3, y1=0.1))


def test_direct_text_reader_excludes_conditioning_refusals():
    import json

    from trade_extract.domain.inference import InferenceRequest, ProviderResult
    from trade_extract.providers.mock import MockProvider
    from trade_extract.services.readers.text_layer import TextLayerReader

    provider = MockProvider({("commercial_invoice", "a" * 64): [ProviderResult({}, "mock")]})
    request = InferenceRequest("commercial_invoice", "a" * 64, {}, "")
    box = BBox(x0=0.1, y0=0.1, x1=0.2, y1=0.2)
    good = PageInput(0, characters=(TextCharacter("GOOD", box),))
    bad = PageInput(
        1,
        characters=(TextCharacter("REFUSED", box),),
        unreadable=False,
        conditioning_status="unreadable",
    )
    reader = TextLayerReader()
    assert reader.read(provider, request, (bad,)) is None
    assert not provider.requests
    reader.read(provider, request, (good, bad))
    evidence = json.loads(provider.requests[0].evidence.split("Text geometry:\n")[1])
    assert [entry["page_index"] for entry in evidence] == [0]
    assert evidence[0]["characters"][0]["text"] == "GOOD"
    assert "REFUSED" not in provider.requests[0].evidence
