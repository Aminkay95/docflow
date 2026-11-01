from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from trade_extract.domain import errors

#: Explicit decompression-bomb ceiling for Pillow-decoded image containers.
_MAX_IMAGE_PIXELS = 100_000_000


@dataclass(frozen=True)
class PageProbe:
    page_index: int
    text: str


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
