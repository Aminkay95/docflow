from dataclasses import replace

from trade_extract.domain.inference import InferenceRequest, ProviderResult
from trade_extract.domain.provenance import ReaderKind
from trade_extract.domain.readers import PageInput
from trade_extract.providers.base import ModelProvider


class VisionReader:
    name = ReaderKind.VISION

    def read(
        self, provider: ModelProvider, request: InferenceRequest, pages: tuple[PageInput, ...]
    ) -> ProviderResult | None:
        if any(
            page.image is not None and page.image.page_index != page.page_index for page in pages
        ):
            raise ValueError("image_page_index_mismatch")
        images = tuple(page.image for page in pages if not page.unreadable and page.image)
        if not images:
            return None
        return provider.vision_extract(
            replace(
                request,
                pages=images,
                evidence=request.evidence
                + '\nRequired provenance.reader: "vision"'
                + "\nOnly cite page_index values with supplied images: "
                + str([image.page_index for image in images]),
            )
        )
