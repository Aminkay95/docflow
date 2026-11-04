"""Conservative versioned per-page routing; no image imports."""

from trade_extract.config import Settings
from trade_extract.domain.provenance import ReaderKind
from trade_extract.domain.readers import PageFeatures, PageInput, PageRoute


def route_page(page: PageInput, settings: Settings, available: set[ReaderKind]) -> PageRoute:
    f = page.features or PageFeatures()
    policy: tuple[tuple[str, str | int | float | bool], ...] = (
        ("version", settings.routing_version),
        ("min_text_characters", settings.routing_min_text_characters),
        ("dense_row_threshold", settings.routing_dense_row_threshold),
    ) + tuple(
        (key, value)
        for key, value in settings.model_dump().items()
        if key.startswith(("ocr_", "table_"))
        or key in ("provider_max_images", "provider_max_image_bytes")
    )
    available = set(available)
    if not page.characters:
        available.discard(ReaderKind.TEXT_LAYER)
    if page.image is None or not page.vision_ready:
        available.discard(ReaderKind.VISION)
        available.discard(ReaderKind.PADDLE)
    availability = tuple((r.value, r in available) for r in ReaderKind)
    required = f.dense_table is True
    reasons: list[str] = []
    desired: tuple[ReaderKind, ...] = ()
    primary = None
    if page.is_unreadable:
        reasons.extend(page.unreadable_reasons or ("unreadable",))
    elif (
        len(page.characters) >= settings.routing_min_text_characters
        and f.dense_table is not True
        and ReaderKind.TEXT_LAYER in available
        and len(page.text.strip()) >= settings.routing_min_text_characters
        and not any(x is True for x in (f.photograph, f.skew, f.stamps, f.handwriting))
    ):
        desired = (ReaderKind.TEXT_LAYER,)
        primary = ReaderKind.TEXT_LAYER
        reasons.append("good_digital_text")
    elif f.flat_scan is True and all(
        x is False for x in (f.photograph, f.skew, f.stamps, f.handwriting)
    ):
        desired = (ReaderKind.PADDLE, ReaderKind.VISION)
        primary = ReaderKind.VISION
        reasons.append("clean_flat_scan_compare")
    else:
        desired = ((ReaderKind.TEXT_LAYER,) if page.characters else ()) + (
            ReaderKind.VISION,
            ReaderKind.PADDLE,
        )
        primary = ReaderKind.VISION
        reasons.append("marks_photo_or_unknown_features")
    if required and not page.is_unreadable:
        reasons.append("dense_table_requires_structure_and_native_crops")
    readers = tuple(r for r in desired if r in available)
    reasons.extend("reader_unavailable:" + r.value for r in desired if r not in available)
    return PageRoute(
        page.page_index,
        readers,
        primary if primary in readers else readers[0] if readers else None,
        tuple(reasons),
        required,
        policy,
        availability,
    )
