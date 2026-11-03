from __future__ import annotations

import io
from pathlib import Path

import pytest
from PIL import Image

from tests.support import make_multipage_tiff, make_pdf
from trade_extract.domain.errors import DomainError
from trade_extract.services.page_model import render_container


def _write(tmp_path: Path, name: str, payload: bytes) -> Path:
    path = tmp_path / name
    path.write_bytes(payload)
    return path


def test_pdf_pages_are_rendered_in_order_and_text_is_captured(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        "mixed.pdf",
        make_pdf([["BILL OF LADING"], ["COMMERCIAL INVOICE"], ["PACKING LIST"]]),
    )
    pages = list(
        render_container(
            path,
            "application/pdf",
            document="mixed.pdf",
            dpi=72,
            thumbnail_max_px=64,
            quality=85,
        )
    )

    assert [page.page_index for page in pages] == [0, 1, 2]
    assert [page.text.strip() for page in pages] == [
        "BILL OF LADING",
        "COMMERCIAL INVOICE",
        "PACKING LIST",
    ]
    for page in pages:
        with Image.open(io.BytesIO(page.image_bytes)) as image:
            assert image.format == "JPEG"
            assert image.size == (page.width_px, page.height_px)
        assert page.dpi == 72
        assert page.rotation_deg == 0.0


def test_render_dpi_scales_the_raster(tmp_path: Path) -> None:
    path = _write(tmp_path, "one.pdf", make_pdf([["ONE"]]))
    low = list(
        render_container(
            path,
            "application/pdf",
            document="one.pdf",
            dpi=72,
            thumbnail_max_px=64,
            quality=85,
        )
    )[0]
    high = list(
        render_container(
            path,
            "application/pdf",
            document="one.pdf",
            dpi=144,
            thumbnail_max_px=64,
            quality=85,
        )
    )[0]

    assert high.width_px == low.width_px * 2
    assert high.height_px == low.height_px * 2
    assert high.dpi == 144


def test_multi_frame_tiff_renders_every_frame_without_text(tmp_path: Path) -> None:
    path = _write(tmp_path, "multi.tiff", make_multipage_tiff(3))
    pages = list(
        render_container(
            path,
            "image/tiff",
            document="multi.tiff",
            dpi=300,
            thumbnail_max_px=64,
            quality=85,
        )
    )

    assert [page.page_index for page in pages] == [0, 1, 2]
    assert all(page.text == "" for page in pages)
    for page in pages:
        with Image.open(io.BytesIO(page.image_bytes)) as image:
            assert image.size == (page.width_px, page.height_px)


def test_thumbnail_long_edge_is_capped_and_aspect_ratio_preserved(tmp_path: Path) -> None:
    path = _write(tmp_path, "page.pdf", make_pdf([["ONE"]]))
    page = list(
        render_container(
            path,
            "application/pdf",
            document="page.pdf",
            dpi=72,
            thumbnail_max_px=64,
            quality=85,
        )
    )[0]

    with Image.open(io.BytesIO(page.image_bytes)) as image:
        assert image.size == (page.width_px, page.height_px)
    with Image.open(io.BytesIO(page.thumbnail_bytes)) as thumbnail:
        assert max(thumbnail.size) == 64
        assert max(thumbnail.size) < max(page.width_px, page.height_px)
        assert thumbnail.width / thumbnail.height == pytest.approx(
            page.width_px / page.height_px, abs=0.02
        )


def test_page_image_quality_changes_the_encoded_length(tmp_path: Path) -> None:
    buffer = io.BytesIO()
    Image.effect_noise((128, 128), 64).convert("RGB").save(buffer, format="PNG")
    path = _write(tmp_path, "noise.png", buffer.getvalue())

    def render(quality: int):
        return list(
            render_container(
                path,
                "image/png",
                document="noise.png",
                dpi=300,
                thumbnail_max_px=64,
                quality=quality,
            )
        )[0]

    low = render(20)
    high = render(95)
    assert len(low.image_bytes) != len(high.image_bytes)


def test_image_page_uses_the_source_dpi_when_present(tmp_path: Path) -> None:
    buffer = io.BytesIO()
    Image.new("RGB", (64, 64), (32, 64, 96)).save(buffer, format="PNG", dpi=(150, 150))
    path = _write(tmp_path, "scan.png", buffer.getvalue())

    page = list(
        render_container(
            path,
            "image/png",
            document="scan.png",
            dpi=300,
            thumbnail_max_px=64,
            quality=85,
        )
    )[0]
    assert page.dpi == 150


def test_pdf_page_above_the_pixel_budget_is_a_processing_failure(tmp_path: Path) -> None:
    path = _write(tmp_path, "large.pdf", make_pdf([["ONE"]]))
    with pytest.raises(DomainError) as caught:
        list(
            render_container(
                path,
                "application/pdf",
                document="large.pdf",
                dpi=1200,
                thumbnail_max_px=64,
                quality=85,
            )
        )
    assert caught.value.code == "processing_failed"


def test_missing_source_is_a_processing_failure(tmp_path: Path) -> None:
    with pytest.raises(DomainError) as caught:
        list(
            render_container(
                tmp_path / "missing.pdf",
                "application/pdf",
                document="missing.pdf",
                dpi=300,
                thumbnail_max_px=64,
                quality=85,
            )
        )
    assert caught.value.code == "processing_failed"
    assert "missing.pdf" in caught.value.message


def test_image_exif_hint_survives_jpeg_rendering(tmp_path):
    from PIL import Image

    from trade_extract.services.page_model import render_container

    image = Image.new("RGB", (200, 300), "white")
    exif = Image.Exif()
    exif[274] = 6
    path = tmp_path / "oriented.jpg"
    image.save(path, exif=exif)
    page = next(
        render_container(
            path, "image/jpeg", document="photo", dpi=300, thumbnail_max_px=100, quality=85
        )
    )
    assert page.exif_orientation == 6
    assert (page.width_px, page.height_px) == (200, 300)


@pytest.mark.parametrize(
    "orientation,expected_size,expected_centre",
    [(6, (300, 200), (250, 30)), (8, (300, 200), (50, 170)), (3, (200, 300), (170, 250))],
)
def test_tiff_orientation_is_applied_once(tmp_path, orientation, expected_size, expected_centre):
    import io

    import numpy as np
    from PIL import Image, ImageDraw

    from trade_extract.config import Settings
    from trade_extract.services.conditioning import condition_page

    image = Image.new("RGB", (200, 300), "white")
    ImageDraw.Draw(image).rectangle((20, 40, 40, 60), fill="red")
    path = tmp_path / "oriented.tiff"
    image.save(path, tiffinfo={274: orientation})
    page = next(
        render_container(
            path, "image/tiff", document="photo", dpi=300, thumbnail_max_px=100, quality=95
        )
    )
    assert page.exif_orientation == 1
    assert (page.width_px, page.height_px) == expected_size
    result = condition_page(page, Settings())
    output = np.array(Image.open(io.BytesIO(result.image_bytes)))
    ys, xs = np.where((output[:, :, 0] > 180) & (output[:, :, 1] < 80) & (output[:, :, 2] < 80))
    assert (xs.mean(), ys.mean()) == pytest.approx(expected_centre, abs=2)
    assert not any(op.startswith("exif_orientation") for op in result.operations)
