from dataclasses import replace

from trade_extract.domain.inference import InferenceRequest, ProviderResult
from trade_extract.domain.provenance import BBox, ReaderKind
from trade_extract.domain.readers import PageInput
from trade_extract.providers.base import ModelProvider


class TextLayerReader:
    name = ReaderKind.TEXT_LAYER

    def read(
        self, provider: ModelProvider, request: InferenceRequest, pages: tuple[PageInput, ...]
    ) -> ProviderResult | None:
        import json

        evidence = [
            {
                "page_index": page.page_index,
                "characters": [
                    {"text": char.text, "bbox": char.bbox.model_dump()} for char in page.characters
                ],
            }
            for page in pages
            if not page.unreadable and page.characters
        ]
        if not evidence:
            return None
        return provider.text_reason(
            replace(
                request,
                pages=(),
                evidence=(
                    request.evidence
                    + '\nRequired provenance.reader: "text_layer"'
                    + "\nText geometry:\n"
                    + json.dumps(evidence)
                ),
            )
        )


def verify_text_box(page: PageInput, text: str, bbox: BBox) -> bool:
    """Require an actual contiguous character span and its exact source rectangle."""
    if not text:
        return False
    source = page.text
    start = source.find(text)
    while start >= 0:
        end = start + len(text)
        if (start and source[start - 1].isalnum() and text[0].isalnum()) or (
            end < len(source) and source[end].isalnum() and text[-1].isalnum()
        ):
            start = source.find(text, start + 1)
            continue
        chars = page.characters[start : start + len(text)]
        if len(chars) == len(text):
            expected = BBox(
                x0=min(c.bbox.x0 for c in chars),
                y0=min(c.bbox.y0 for c in chars),
                x1=max(c.bbox.x1 for c in chars),
                y1=max(c.bbox.y1 for c in chars),
            )
            if all(
                abs(getattr(expected, key) - getattr(bbox, key)) <= 0.00001
                for key in ("x0", "y0", "x1", "y1")
            ):
                return True
        start = source.find(text, start + 1)
    return False
