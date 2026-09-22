from pathlib import Path

from surya_form_poc.infrastructure.surya_provider import SuryaCliProvider
from surya_form_poc.application.batch_ocr import BatchOcrService

from surya_form_poc.application.extract_ocr import ExtractOcrCommand, ExtractOcrUseCase
from surya_form_poc.domain.models import OcrMode


class FakeSuryaProvider:
    def analyze(self, document: Path) -> list[dict]:
        return [{"blocks": [{"html": "<p>Formulario E3</p>", "confidence": 0.99, "polygon": [[0, 0]], "skipped": False}]}]


def test_structured_mode_preserves_surya_blocks_and_derives_text():
    schema_root = Path(__file__).parents[1] / "schema"
    result = ExtractOcrUseCase(FakeSuryaProvider(), schema_root).execute(
        ExtractOcrCommand(Path("6000000026.tif"), OcrMode.STRUCTURED, False)
    )

    assert result["ocr"]["full_text"] == "Formulario E3"
    assert result["ocr"]["fulltextLimpio"] == "Formulario E3"
    assert result["ocr"]["pages"][0]["blocks"][0]["confidence"] == 0.99
    assert result["result"]["Document"][0]["Field"]["Key"] == "TelefonoFijo"


def test_e3_values_extracts_labels_dates_and_document_number():
    values = ExtractOcrUseCase._e3_values(
        "FORMULARIO NO 6000000026 COD PUESTO 010010101 FECHA DE INSCRIPCION DIA MES ANO 12 08 2 0 2 6 "
        "NUMERO DE DOCUMENTO 10 11 2 36 4 35 57 FECHA DE EXPEDICION DIA MES ANO 02 05 2 0 0 8 "
        "PRIMER APELLIDO Perilla SEGUNDO APELLIDO Ospina PRIMER NOMBRE Yineth SEGUNDO NOMBRE Paola NIVEL DE ESTUDIO"
    )

    assert values["NumFormulario"] == "6000000026"
    assert values["FechaInscripcion"] == "12-08-2026"
    assert values["FechaExpedicion"] == "02-05-2008"
    assert values["NumeroDocumento"] == "101123643557"
    assert values["PrimerNombre"] == "Yineth"


def test_e3_values_keeps_blank_mobile_and_reads_native_checkbox():
    values = ExtractOcrUseCase._e3_values(
        "TELEFONO MOVIL CIUDAD MUNICIPIO DE RESIDENCIA Bogota TELEFONO FUO 315522 3318 LEE BRAILLE",
        [{"blocks": [
            {"html": "<p>LEE BRAILLE</p>", "skipped": False},
            {"html": "<p>SI NO <input checked=\"\" type=\"checkbox\"/></p>", "skipped": False},
        ]}],
    )

    assert values["TelefonoMovil"] == ""
    assert values["TelefonoFijo"] == "315522 3318"
    assert values["LeeBraile"] == "No"


def test_surya_provider_reads_results_from_document_subdirectory(tmp_path):
    document = tmp_path / "6000000026.tif"
    assert SuryaCliProvider._results_path(str(tmp_path), document) == tmp_path / "6000000026" / "results.json"


def test_batch_artifacts_are_written_as_five_files(tmp_path):
    image = tmp_path / "E3-1.tif"
    image.write_bytes(b"image")
    service = BatchOcrService(None, tmp_path)  # type: ignore[arg-type]
    files = service._write_artifacts(
        tmp_path,
        image,
        {"ocr": {"full_text": "raw", "fulltextLimpio": "clean", "pages": [{"blocks": []}]}, "result": {"Document": []}},
        12.34,
    )

    assert len(files) == 5
    assert (tmp_path / "E3-1_full_text.txt").read_text(encoding="utf-8") == "raw"
    assert (tmp_path / "E3-1_result.json").is_file()


def test_batch_csv_contains_one_row_per_field(tmp_path):
    service = BatchOcrService(None, tmp_path)  # type: ignore[arg-type]
    rows = service._spreadsheet_rows("E3-1.tif", 12.345, {"result": {"Document": [{"Field": {"Key": "LeeBraile", "Value": "No"}}]}})
    from surya_form_poc.infrastructure.csv_export import write_batch_results

    output = tmp_path / "ocr_batch_results.csv"
    write_batch_results(output, rows)
    assert rows == [("E3-1.tif", 12.35, "LeeBraile", "No")]
    assert output.read_text(encoding="utf-8-sig") == "nombreArchivo;processing_time_seconds;Key;value\nE3-1.tif;12.35;LeeBraile;No\n"


def test_directory_routes_are_registered(monkeypatch, tmp_path):
    monkeypatch.setenv("OCR_DOCUMENTS_ROOT", str(tmp_path))
    monkeypatch.setenv("OCR_SCHEMA_ROOT", str(tmp_path))
    monkeypatch.setenv("OCR_OUTPUT_ROOT", str(tmp_path))

    from surya_form_poc.presentation.api import create_app

    routes = {route.path for route in create_app().routes}

    assert "/v1/ocr/directory" in routes
    assert "/v1/ocr/directory/{job_id:str}" in routes
