import json
import logging
import select
import subprocess
from time import monotonic
from pathlib import Path
from tempfile import TemporaryDirectory

logger = logging.getLogger(__name__)
OCR_TIMEOUT_SECONDS = 1_800


class SuryaCliProvider:
    """Stable boundary around Surya's CLI and its documented results.json schema."""

    @staticmethod
    def _results_path(output_directory: str, document: Path) -> Path:
        return Path(output_directory) / document.stem / "results.json"

    def analyze(self, document: Path) -> list[dict]:
        with TemporaryDirectory() as output_directory:
            process = subprocess.Popen(
                ["surya_ocr", str(document), "--output_dir", output_directory],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            logger.info("Surya CLI started: file=%s pid=%s", document.name, process.pid)
            started_at = monotonic()
            next_progress_at = started_at + 30
            output_lines: list[str] = []
            assert process.stdout is not None
            while process.poll() is None:
                readable, _, _ = select.select([process.stdout], [], [], 1)
                if readable:
                    line = process.stdout.readline().strip()
                    if line:
                        output_lines.append(line)
                        logger.info("Surya CLI output: file=%s message=%s", document.name, line)
                elapsed = monotonic() - started_at
                if elapsed >= next_progress_at:
                    logger.info("Surya CLI still processing: file=%s elapsed_seconds=%.0f", document.name, elapsed)
                    next_progress_at += 30
                if elapsed >= OCR_TIMEOUT_SECONDS:
                    process.kill()
                    raise subprocess.TimeoutExpired(process.args, OCR_TIMEOUT_SECONDS)
            output_lines.extend(line.strip() for line in process.stdout if line.strip())
            if process.returncode:
                logger.error("Surya CLI failed: file=%s exit_code=%s output=%s", document.name, process.returncode, "\n".join(output_lines[-20:]))
                raise subprocess.CalledProcessError(process.returncode, process.args, output="\n".join(output_lines))
            logger.info("Surya CLI completed: file=%s elapsed_seconds=%.2f", document.name, monotonic() - started_at)
            results_path = self._results_path(output_directory, document)
            if not results_path.is_file():
                raise FileNotFoundError(f"Surya completed without producing results at {results_path}.")
            results = json.loads(results_path.read_text(encoding="utf-8"))
        pages = results.get(document.stem)
        if pages is None:
            raise ValueError("Surya did not return OCR output for the requested document.")
        return pages
