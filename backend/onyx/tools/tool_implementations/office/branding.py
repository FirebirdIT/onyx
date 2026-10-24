"""Brand colours for the files the office tools create.

The tools make neutral files, so anyone can use them for their own company.
A builder applies a brand only when a caller passes one."""

from dataclasses import dataclass


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
