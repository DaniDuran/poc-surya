"""CSV export compatible with Excel in Spanish-language Windows locales."""

import csv
from pathlib import Path


def write_batch_results(path: Path, rows: list[tuple[str, float, str, str]]) -> None:
    """Write a BOM-prefixed, semicolon-delimited CSV with one row per field."""
    with path.open("w", encoding="utf-8-sig", newline="") as output:
        writer = csv.writer(output, delimiter=";")
        writer.writerow(("nombreArchivo", "processing_time_seconds", "Key", "value"))
        writer.writerows(rows)
