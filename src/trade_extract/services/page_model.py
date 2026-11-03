from __future__ import annotations

import io
import math
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from trade_extract.domain import errors
from trade_extract.domain.provenance import BBox
from trade_extract.domain.readers import TextCharacter

#: Explicit decompression-bomb ceiling for Pillow-decoded image containers.
_MAX_IMAGE_PIXELS = 100_000_000

#: Stored page images are JPEG so the quality knob has an effect; PNG has no quality.
IMAGE_CONTENT_TYPE = "image/jpeg"
_PDF_POINTS_PER_INCH = 72.0


@dataclass(frozen=True)
class PageProbe:
    page_index: int
    text: str


@dataclass(frozen=True)
class PageRendering:
    """One rendered page, yielded before the next page is touched.

    ``image_bytes`` and ``thumbnail_bytes`` are encoded JPEGs. ``text`` is the
    embedded text layer for digital PDFs and empty for image containers.
    """

    page_index: int
    image_bytes: bytes
    thumbnail_bytes: bytes
    width_px: int
    height_px: int
    dpi: int
    rotation_deg: float
    text: str
    characters: tuple[TextCharacter, ...] = ()


@dataclass(frozen=True)
class ContainerProbe:
    page_count: int
    pages: tuple[PageProbe, ...] = field(default_factory=tuple)
    has_text_layer: bool = False
    #: True when the page cap was breached before any text extraction was attempted.
    page_cap_breached: bool = False

    @property
    def text(self) -> str:
        return "\n".join(page.text for page in self.pages)


def probe_container(
    path: str | Path,
    *,
    document: str,
    page_limit: int | None = None,
) -> ContainerProbe:
    """Return page count and per-page embedded text for a validated container.

    ``pypdfium2`` is imported lazily so that importing the API application never pulls a
    PDF/vision stack into the web process. When ``page_limit`` is supplied and the
    container exceeds it, the probe returns the real page count without extracting text
    from every page: the caller rejects the file at no extra parsing cost.
    """

    import pypdfium2 as pdfium

    path = Path(path)
    try:
        document_handle = pdfium.PdfDocument(str(path))
    except pdfium.PdfiumError as exc:
        message = str(exc).lower()
        if "password" in message or "encrypt" in message:
            raise errors.encrypted_document(document) from exc
        raise errors.malformed_document(document) from exc
    except Exception as exc:  # noqa: BLE001 - the parser surfaces several error shapes
        raise errors.malformed_document(document) from exc

    try:
        page_count = len(document_handle)
    except Exception as exc:  # noqa: BLE001
        document_handle.close()
        raise errors.malformed_document(document) from exc

    if page_count <= 0:
        document_handle.close()
        raise errors.malformed_document(document, "the container has zero pages")

    if page_limit is not None and page_count > page_limit:
        document_handle.close()
        return ContainerProbe(
            page_count=page_count,
            pages=(),
            has_text_layer=False,
            page_cap_breached=True,
        )

    pages: list[PageProbe] = []
    try:
        for index in range(page_count):
            page = document_handle[index]
            try:
                text_page = page.get_textpage()
                try:
                    text = text_page.get_text_bounded() or ""
                finally:
                    text_page.close()
            finally:
                page.close()
            pages.append(PageProbe(page_index=index, text=text))
    except errors.DomainError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise errors.malformed_document(document) from exc
    finally:
        document_handle.close()

    return ContainerProbe(
        page_count=page_count,
        pages=tuple(pages),
        has_text_layer=any(page.text.strip() for page in pages),
    )


def probe_image(
    path: str | Path,
    *,
    document: str,
    page_limit: int | None = None,
) -> ContainerProbe:
    """Decode an image container with Pillow.

    Magic bytes select the type, but decoding is what proves the container is intact.
    A truncated or undecodable image is ``malformed_document``; a multi-page TIFF
    contributes its real frame count to the page caps. A frame count above the
    remaining budget is returned without decoding every frame, and Pillow's
    decompression-bomb ceiling is set explicitly. Images have no text layer.
    """

    from PIL import Image, UnidentifiedImageError

    Image.MAX_IMAGE_PIXELS = _MAX_IMAGE_PIXELS
    path = Path(path)
    try:
        with Image.open(path) as image:
            frame_count = int(getattr(image, "n_frames", 1))
            if frame_count <= 0:
                raise errors.malformed_document(document, "the container has zero pages")
            if page_limit is not None and frame_count > page_limit:
                return ContainerProbe(
                    page_count=frame_count,
                    pages=(),
                    has_text_layer=False,
                    page_cap_breached=True,
                )
            for index in range(frame_count):
                if index:
                    image.seek(index)
                if image.width * image.height > _MAX_IMAGE_PIXELS:
                    # Pillow only warns between its ceiling and twice it; decode anyway and a
                    # compressed file turns into gigabytes of texture store. Refuse instead.
                    raise errors.malformed_document(
                        document,
                        "the frame exceeds the 100-megapixel decode budget; downscale it",
                    )
                image.load()
    except errors.DomainError:
        raise
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError) as exc:
        raise errors.malformed_document(document, "the image data cannot be decoded") from exc
    except Exception as exc:  # noqa: BLE001 - Pillow surfaces plugin-specific errors
        raise errors.malformed_document(document, "the image data cannot be decoded") from exc

    pages = tuple(PageProbe(page_index=index, text="") for index in range(frame_count))
    return ContainerProbe(page_count=frame_count, pages=pages, has_text_layer=False)


def render_container(
    path: str | Path,
    content_type: str,
    *,
    document: str,
    dpi: int,
    thumbnail_max_px: int,
    quality: int,
) -> Iterator[PageRendering]:
    """Yield one rendering per page, in source order, one page at a time.

    PDF pages are rasterised at ``dpi`` with pypdfium2; image containers are
    decoded frame by frame with Pillow. The generator uploads nothing itself:
    the caller stores each page before advancing, so peak memory stays at one
    page image plus its thumbnail. ``pypdfium2`` and Pillow are imported inside
    the functions so the API image remains reader-free.

    Unreadable sources and render failures raise ``processing_failed``; the
    worker's retry/dead path owns the terminal outcome.
    """

    if content_type == "application/pdf":
        yield from _render_pdf(
            path,
            document=document,
            dpi=dpi,
            thumbnail_max_px=thumbnail_max_px,
            quality=quality,
        )
        return
    yield from _render_image(
        path,
        document=document,
        dpi=dpi,
        thumbnail_max_px=thumbnail_max_px,
        quality=quality,
    )


def _encode_jpeg(image: Any, quality: int) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=quality, optimize=True)
    return buffer.getvalue()


def _text_characters(
    text_page: Any, width: float, height: float, rotation: int = 0
) -> tuple[TextCharacter, ...]:
    """PDFium character rectangles transformed from PDF bottom-left to normalized top-left."""
    characters = []
    if rotation in (90, 270):
        width, height = height, width
    for index in range(text_page.count_chars()):
        text = text_page.get_text_range(index, 1)
        left, bottom, right, top = text_page.get_charbox(index)

        # PDFium can report glyph overhangs outside the media box. Clip to the page.
        def clip(value: float) -> float:
            return min(1.0, max(0.0, value))

        bbox = BBox(
            x0=clip(left / width),
            y0=clip(1 - top / height),
            x1=clip(right / width),
            y1=clip(1 - bottom / height),
        )
        if rotation == 90:
            bbox = BBox(x0=1 - bbox.y1, y0=bbox.x0, x1=1 - bbox.y0, y1=bbox.x1)
        elif rotation == 180:
            bbox = BBox(x0=1 - bbox.x1, y0=1 - bbox.y1, x1=1 - bbox.x0, y1=1 - bbox.y0)
        elif rotation == 270:
            bbox = BBox(x0=bbox.y0, y0=1 - bbox.x1, x1=bbox.y1, y1=1 - bbox.x0)
        for character in text:
            characters.append(TextCharacter(character, bbox))
    return tuple(characters)


def _thumbnail(image: Any, max_px: int) -> Any:
    from PIL import Image

    thumbnail = image.copy()
    thumbnail.thumbnail((max_px, max_px), Image.Resampling.LANCZOS)
    return thumbnail


def _source_dpi(source: Any, fallback: int) -> int:
    """Return the image's own DPI when it records one, else the configured DPI."""

    try:
        value = source.info.get("dpi")
    except Exception:  # noqa: BLE001 - plugin metadata is optional
        return fallback
    if isinstance(value, (tuple, list)):
        value = value[0] if value else None
    try:
        resolved = int(round(float(value)))
    except (TypeError, ValueError):
        return fallback
    return resolved if resolved > 0 else fallback


def _render_pdf(
    path: str | Path,
    *,
    document: str,
    dpi: int,
    thumbnail_max_px: int,
    quality: int,
) -> Iterator[PageRendering]:
    import pypdfium2 as pdfium

    scale = dpi / _PDF_POINTS_PER_INCH
    try:
        document_handle = pdfium.PdfDocument(str(Path(path)))
    except Exception as exc:  # noqa: BLE001 - several parser error shapes
        raise errors.processing_failed(document, "the PDF cannot be opened") from exc

    try:
        page_count = len(document_handle)
        if page_count <= 0:
            raise errors.processing_failed(document, "the PDF has zero pages")
        for index in range(page_count):
            page = document_handle[index]
            try:
                page_width_pt, page_height_pt = page.get_size()
                pixel_width = math.ceil(page_width_pt * scale)
                pixel_height = math.ceil(page_height_pt * scale)
                if pixel_width * pixel_height > _MAX_IMAGE_PIXELS:
                    raise errors.processing_failed(
                        document,
                        "the rendered page exceeds the 100-megapixel budget; "
                        "lower PAGE_RENDER_DPI or split the file",
                    )
                text_page = page.get_textpage()
                try:
                    text = text_page.get_text_bounded() or ""
                    characters = _text_characters(
                        text_page, page_width_pt, page_height_pt, page.get_rotation()
                    )
                finally:
                    text_page.close()
                rotation_deg = float(page.get_rotation())
                bitmap = page.render(scale=scale)
                try:
                    image = bitmap.to_pil().convert("RGB")
                    width_px, height_px = image.size
                    image_bytes = _encode_jpeg(image, quality)
                    thumbnail_bytes = _encode_jpeg(_thumbnail(image, thumbnail_max_px), quality)
                finally:
                    bitmap.close()
            finally:
                page.close()
            yield PageRendering(
                page_index=index,
                image_bytes=image_bytes,
                thumbnail_bytes=thumbnail_bytes,
                width_px=width_px,
                height_px=height_px,
                dpi=dpi,
                rotation_deg=rotation_deg,
                text=text,
                characters=characters,
            )
    except errors.DomainError:
        raise
    except Exception as exc:  # noqa: BLE001 - pypdfium2 surfaces several error shapes
        raise errors.processing_failed(document, "the PDF pages could not be rendered") from exc
    finally:
        document_handle.close()


def _render_image(
    path: str | Path,
    *,
    document: str,
    dpi: int,
    thumbnail_max_px: int,
    quality: int,
) -> Iterator[PageRendering]:
    from PIL import Image, UnidentifiedImageError

    Image.MAX_IMAGE_PIXELS = _MAX_IMAGE_PIXELS
    try:
        with Image.open(Path(path)) as source:
            frame_count = int(getattr(source, "n_frames", 1))
            if frame_count <= 0:
                raise errors.processing_failed(document, "the image has zero frames")
            for index in range(frame_count):
                if index:
                    source.seek(index)
                frame_dpi = _source_dpi(source, dpi)
                frame = source.convert("RGB")
                width_px, height_px = frame.size
                if width_px <= 0 or height_px <= 0:
                    raise errors.processing_failed(document, "the frame has no pixels")
                image_bytes = _encode_jpeg(frame, quality)
                thumbnail_bytes = _encode_jpeg(_thumbnail(frame, thumbnail_max_px), quality)
                yield PageRendering(
                    page_index=index,
                    image_bytes=image_bytes,
                    thumbnail_bytes=thumbnail_bytes,
                    width_px=width_px,
                    height_px=height_px,
                    dpi=frame_dpi,
                    rotation_deg=0.0,
                    text="",
                )
    except errors.DomainError:
        raise
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError) as exc:
        raise errors.processing_failed(document, "the image data cannot be decoded") from exc
    except Exception as exc:  # noqa: BLE001 - Pillow surfaces plugin-specific errors
        raise errors.processing_failed(document, "the image data cannot be decoded") from exc
