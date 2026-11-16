from dataclasses import replace

from trade_extract.domain.provenance import BBox
from trade_extract.domain.readers import PageInput, TextCharacter
from trade_extract.services.digital_bill_of_lading import read_manifest
from trade_extract.services.readers.text_layer import verify_text_box


def test_numbered_manifest_keeps_shared_cargo_separate_from_container_rows():
    lines = (
        "1 ABCU1234567 101001 10 Drum(s) 4 x 20'FCL/FCL CONTAINERS",
        "2 ABCU1234568 101002 10 Drum(s) CY/CY STC:",
        "3 ABCU1234569 101003 10 Drum(s) BITUMEN GRADE 80/100",
        "4 ABCU1234570 101004 10 Drum(s) HS CODE: 27132000",
    )
    page = PageInput(
        0,
        characters=tuple(
            TextCharacter(
                char,
                BBox(x0=col * 0.01, x1=col * 0.01 + 0.008, y0=y, y1=y + 0.008),
            )
            for index, line in enumerate(lines)
            for y in (0.4 + index * 0.015,)
            for col, char in enumerate(line + "\n")
        ),
    )
    source_hash = "sha256:" + "a" * 64

    result = read_manifest(page, source_hash, 100)

    assert result is not None
    assert [row.table_name for row in result.inventory.rows] == ["containers"] * 4 + ["line_items"]
    fields = {candidate.field_path: candidate.field for candidate in result.candidates}
    assert fields["containers[3].number"].value == "ABCU1234570"
    assert fields["line_items[0].description"].value == "BITUMEN GRADE 80/100"
    assert fields["line_items[0].hs_code"].value == "27132000"
    assert all(
        verify_text_box(page, field.value, field.provenance.bbox) for field in fields.values()
    )

    # An incomplete manifest must use the ordinary extraction path.
    incomplete = replace(page, characters=page.characters[: -len(lines[-1]) - 1])
    assert read_manifest(incomplete, source_hash, 100) is None
