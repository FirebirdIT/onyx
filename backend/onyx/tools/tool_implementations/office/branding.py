"""Brand colours for the files the office tools create.

The stock product keeps the library defaults (`OFFICE_BRAND` is None). A
white-label build sets its colours once here, and every builder reads them."""

from dataclasses import dataclass

from onyx.configs.constants import PRODUCT_BRAND


@dataclass(frozen=True)
class OfficeBrand:
    name: str
    # Hex colours without '#', the form python-docx, python-pptx and openpyxl take.
    primary: str
    on_primary: str
    tint: str
    rule: str
    chart_colors: tuple[str, ...]


DEEPX_OFFICE_BRAND = OfficeBrand(
    name="Deep X",
    primary="0A4D6B",
    on_primary="FFFFFF",
    tint="E6EEF2",
    rule="B8CCD6",
    chart_colors=(
        "0A4D6B",
        "2A9D8F",
        "E9C46A",
        "F4A261",
        "E76F51",
        "6C8EAD",
        "8AB17D",
        "B56576",
    ),
)

OFFICE_BRAND: OfficeBrand | None = (
    DEEPX_OFFICE_BRAND if PRODUCT_BRAND == "deepx" else None
)
