import logging
from pathlib import Path
from tempfile import NamedTemporaryFile
from time import perf_counter

from anyio import to_thread
from typing import Annotated

from litestar import Litestar, Request, get, post
from litestar.datastructures import UploadFile
from litestar.enums import RequestEncodingType
from litestar.exceptions import HTTPException
from litestar.params import Body
from pydantic import BaseModel, Field

from surya_form_poc.application.extract_ocr import ExtractOcrCommand, ExtractOcrUseCase
from surya_form_poc.application.batch_ocr import BatchOcrService
from surya_form_poc.domain.models import OcrMode
from surya_form_poc.infrastructure.settings import Settings
from surya_form_poc.infrastructure.surya_provider import SuryaCliProvider

logger = logging.getLogger(__name__)


class PathOcrRequest(BaseModel):
    path: str = Field(examples=["6000000026.tif"])
    mode: OcrMode = OcrMode.FULL_TEXT
    include_image: bool = False


class DirectoryOcrRequest(BaseModel):
    path: str = Field(examples=["lote-e3"])


def _safe_path(root: Path, relative_path: str) -> Path:
    candidate = (root / relative_path).resolve()
    if not candidate.is_relative_to(root.resolve()) or not candidate.is_file():
        raise HTTPException(status_code=404, detail="Document not found under OCR_DOCUMENTS_ROOT.")
    return candidate


def _safe_directory(root: Path, relative_path: str) -> Path:
    candidate = (root / relative_path).resolve()
    if not candidate.is_relative_to(root.resolve()) or not candidate.is_dir():
        raise HTTPException(status_code=404, detail="Directory not found under OCR_DOCUMENTS_ROOT.")
    return candidate


@get("/health", sync_to_thread=False)
def health() -> dict:
    return {"status": "ok", "engine": "Surya OCR (on-premise)"}


@post("/v1/ocr/path", sync_to_thread=True)
def extract_from_path(data: PathOcrRequest, request: Request) -> dict:
    path = _safe_path(request.app.state.documents_root, data.path)
    started_at = perf_counter()
    logger.info("OCR path request started: file=%s mode=%s", path.name, data.mode.value)
    try:
        result = request.app.state.ocr_use_case.execute(ExtractOcrCommand(path, data.mode, data.include_image))
    except Exception:
        logger.exception("OCR path request failed: file=%s elapsed_seconds=%.2f", path.name, perf_counter() - started_at)
        raise
    logger.info("OCR path request completed: file=%s elapsed_seconds=%.2f", path.name, perf_counter() - started_at)
    return result


@post("/v1/ocr/directory", status_code=202, sync_to_thread=True)
def extract_from_directory(data: DirectoryOcrRequest, request: Request) -> dict:
    directory = _safe_directory(request.app.state.documents_root, data.path)
    job = request.app.state.batch_ocr_service.submit(directory)
    logger.info("Directory OCR queued: job_id=%s directory=%s images=%s", job["job_id"], directory.name, job["total_images"])
    return job


@get("/v1/ocr/directory/{job_id:str}", sync_to_thread=True)
def directory_ocr_status(job_id: str, request: Request) -> dict:
    try:
        return request.app.state.batch_ocr_service.get(job_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="OCR directory job not found.") from error


@post("/v1/ocr/upload")
async def extract_from_upload(
    request: Request,
    file: Annotated[UploadFile, Body(media_type=RequestEncodingType.MULTI_PART)],
    mode: OcrMode = OcrMode.FULL_TEXT,
    include_image: bool = False,
) -> dict:
    with NamedTemporaryFile(suffix=Path(file.filename or "document.bin").suffix, delete=False) as temporary:
        temporary.write(await file.read())
        path = Path(temporary.name)
    try:
        started_at = perf_counter()
        logger.info("OCR upload request started: file=%s mode=%s", file.filename, mode.value)
        try:
            result = await to_thread.run_sync(
                request.app.state.ocr_use_case.execute,
                ExtractOcrCommand(path, mode, include_image),
            )
        except Exception:
            logger.exception("OCR upload request failed: file=%s elapsed_seconds=%.2f", file.filename, perf_counter() - started_at)
            raise
        logger.info("OCR upload request completed: file=%s elapsed_seconds=%.2f", file.filename, perf_counter() - started_at)
        return result
    finally:
        path.unlink(missing_ok=True)


def create_app() -> Litestar:
    settings = Settings()
    app = Litestar(
        route_handlers=[health, extract_from_path, extract_from_directory, directory_ocr_status, extract_from_upload]
    )
    app.state.documents_root = settings.ocr_documents_root
    app.state.ocr_use_case = ExtractOcrUseCase(SuryaCliProvider(), settings.ocr_schema_root)
    app.state.batch_ocr_service = BatchOcrService(app.state.ocr_use_case, settings.ocr_output_root)
    return app


app = create_app()
