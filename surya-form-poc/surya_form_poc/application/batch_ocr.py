import json
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from time import perf_counter
from uuid import uuid4

from surya_form_poc.application.extract_ocr import ExtractOcrCommand, ExtractOcrUseCase
from surya_form_poc.domain.models import OcrMode
from surya_form_poc.infrastructure.csv_export import write_batch_results

logger = logging.getLogger(__name__)
IMAGE_EXTENSIONS = {".bmp", ".gif", ".jpeg", ".jpg", ".png", ".tif", ".tiff", ".webp"}


class BatchOcrService:
    """In-memory, single-worker queue for sequential directory OCR jobs."""

    def __init__(self, use_case: ExtractOcrUseCase, output_root: Path) -> None:
        self._use_case = use_case
        self._output_root = output_root
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ocr-directory")
        self._jobs: dict[str, dict] = {}
        self._lock = Lock()

    def submit(self, directory: Path) -> dict:
        images = sorted(path for path in directory.rglob("*") if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS)
        job_id = uuid4().hex
        output_directory = self._output_root / job_id
        job = {
            "job_id": job_id,
            "status": "queued",
            "source_directory": str(directory),
            "output_directory": str(output_directory),
            "total_images": len(images),
            "processed_images": 0,
            "failed_images": 0,
            "created_at": self._now(),
            "files": [],
        }
        with self._lock:
            self._jobs[job_id] = job
        self._executor.submit(self._run, job_id, images, output_directory)
        return self.get(job_id)

    def get(self, job_id: str) -> dict:
        with self._lock:
            if job_id not in self._jobs:
                raise KeyError(job_id)
            return dict(self._jobs[job_id])

    def _run(self, job_id: str, images: list[Path], output_directory: Path) -> None:
        output_directory.mkdir(parents=True, exist_ok=True)
        self._update(job_id, status="running", started_at=self._now())
        spreadsheet_rows: list[tuple[str, float, str, str]] = []
        for image in images:
            started_at = perf_counter()
            try:
                response = self._use_case.execute(ExtractOcrCommand(image, OcrMode.STRUCTURED, False))
                elapsed_seconds = perf_counter() - started_at
                files = self._write_artifacts(output_directory, image, response, elapsed_seconds)
                spreadsheet_rows.extend(self._spreadsheet_rows(image.name, elapsed_seconds, response))
                self._update(job_id, processed_images_delta=1, files_append=files)
                logger.info("Directory OCR completed: job_id=%s file=%s", job_id, image.name)
            except Exception as error:
                report = self._write_failure_report(output_directory, image, perf_counter() - started_at, error)
                self._update(job_id, processed_images_delta=1, failed_images_delta=1, files_append=[str(report)])
                logger.exception("Directory OCR failed: job_id=%s file=%s", job_id, image.name)
        csv_path = output_directory / "ocr_batch_results.csv"
        write_batch_results(csv_path, spreadsheet_rows)
        self._update(job_id, files_append=[str(csv_path)])
        self._update(job_id, status="completed", completed_at=self._now())

    @staticmethod
    def _spreadsheet_rows(image_name: str, elapsed_seconds: float, response: dict) -> list[tuple[str, float, str, str]]:
        return [(image_name, round(elapsed_seconds, 2), field["Key"], field["Value"])
                for item in response["result"]["Document"] for field in [item["Field"]]]

    def _write_artifacts(self, output_directory: Path, image: Path, response: dict, elapsed_seconds: float) -> list[str]:
        prefix = image.stem
        artifacts = {
            f"{prefix}_full_text.txt": response["ocr"]["full_text"],
            f"{prefix}_fulltextLimpio.txt": response["ocr"]["fulltextLimpio"],
            f"{prefix}_fulltext_detail.json": json.dumps(response["ocr"].get("pages", []), ensure_ascii=False, indent=2),
            f"{prefix}_result.json": json.dumps(response["result"], ensure_ascii=False, indent=2),
            f"{prefix}_report.json": json.dumps(self._report(image, elapsed_seconds, "completed"), ensure_ascii=False, indent=2),
        }
        paths = []
        for filename, content in artifacts.items():
            path = output_directory / filename
            path.write_text(content, encoding="utf-8")
            paths.append(str(path))
        return paths

    def _write_failure_report(self, output_directory: Path, image: Path, elapsed_seconds: float, error: Exception) -> Path:
        path = output_directory / f"{image.stem}_report.json"
        report = self._report(image, elapsed_seconds, "failed")
        report["error"] = str(error)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    @staticmethod
    def _report(image: Path, elapsed_seconds: float, status: str) -> dict:
        return {
            "image": {"name": image.name, "extension": image.suffix.lower(), "size_bytes": image.stat().st_size},
            "status": status,
            "processing_time_seconds": round(elapsed_seconds, 2),
            "tokens_input": None,
            "tokens_output": None,
            "generation_prompt": None,
            "metrics_note": "surya_ocr CLI does not expose per-request token usage or its internal generation prompt.",
        }

    def _update(self, job_id: str, *, status: str | None = None, processed_images_delta: int = 0, failed_images_delta: int = 0, files_append: list[str] | None = None, **values: object) -> None:
        with self._lock:
            job = self._jobs[job_id]
            if status:
                job["status"] = status
            job["processed_images"] += processed_images_delta
            job["failed_images"] += failed_images_delta
            if files_append:
                job["files"].extend(files_append)
            job.update(values)

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()
