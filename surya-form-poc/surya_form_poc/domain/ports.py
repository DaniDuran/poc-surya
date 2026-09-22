from pathlib import Path
from typing import Protocol


class OcrProvider(Protocol):
    def analyze(self, document: Path) -> list[dict]: ...
