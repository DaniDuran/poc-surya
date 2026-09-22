import base64
import html
import json
import mimetypes
import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from surya_form_poc.domain.models import OcrMode
from surya_form_poc.domain.ports import OcrProvider


@dataclass(frozen=True)
class ExtractOcrCommand:
    path: Path
    mode: OcrMode
    include_image: bool


class ExtractOcrUseCase:
    def __init__(self, provider: OcrProvider, schema_root: Path) -> None:
        self._provider = provider
        self._schema_root = schema_root

    def execute(self, command: ExtractOcrCommand) -> dict:
        pages = self._provider.analyze(command.path)
        full_text = self._full_text(pages)
        clean_text = self._clean_text(full_text)
        response = {
            "file_name": command.path.name,
            "mode": command.mode,
            "ocr": {"full_text": full_text, "fulltextLimpio": clean_text},
            "result": self._extract_result(clean_text, pages),
        }
        if command.mode is OcrMode.STRUCTURED:
            response["ocr"]["pages"] = pages
        if command.include_image:
            mime_type = mimetypes.guess_type(command.path.name)[0] or "application/octet-stream"
            encoded = base64.b64encode(command.path.read_bytes()).decode("ascii")
            response["image"] = {"mime_type": mime_type, "data_url": f"data:{mime_type};base64,{encoded}"}
        return response

    @staticmethod
    def _full_text(pages: list[dict]) -> str:
        blocks = (block for page in pages for block in page.get("blocks", []) if not block.get("skipped"))
        return "\n".join(html.unescape(re.sub(r"<[^>]+>", " ", block.get("html", "")).strip()) for block in blocks if block.get("html")).strip()

    @staticmethod
    def _clean_text(value: str) -> str:
        normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
        normalized = normalized.replace("\\", " ")
        normalized = re.sub(r"[^A-Za-z0-9@\s]", " ", normalized)
        return re.sub(r"\s+", " ", normalized).strip()

    def _extract_result(self, clean_text: str, pages: list[dict] | None = None) -> dict:
        schema_text = (self._schema_root / "E3.json").read_text(encoding="utf-8-sig")
        try:
            fields = json.loads(schema_text)["typologies"][0]["fields"]
        except json.JSONDecodeError:
            # Some operational schemas include unescaped quotation marks in descriptions.
            # Field names remain authoritative and are sufficient for the response contract.
            fields = [{"name": name} for name in re.findall(r'"name"\s*:\s*"([^"]+)"', schema_text)]
        values = self._e3_values(clean_text, pages or [])
        return {
            "Document": [
                {"Field": {"Key": field["name"], "Value": values.get(field["name"], "")}}
                for field in fields
            ]
        }

    @staticmethod
    def _e3_values(text: str, pages: list[dict] | None = None) -> dict[str, str]:
        def capture(pattern: str, stop: str = "") -> str:
            match = re.search(pattern, text, re.IGNORECASE)
            if not match:
                return ""
            value = match.group(1).strip()
            if stop:
                value = re.split(stop, value, maxsplit=1, flags=re.IGNORECASE)[0].strip()
            return value

        def date_after(label: str) -> str:
            match = re.search(label + r".*?(\d{2})\s+(\d{2})\s+(\d)\s*(\d)\s*(\d)\s*(\d)", text, re.IGNORECASE)
            return f"{match.group(1)}-{match.group(2)}-{''.join(match.groups()[2:])}" if match else ""

        def phone_between(pattern: str) -> str:
            value = capture(pattern)
            digits = re.sub(r"\D", "", value)
            return value if re.fullmatch(r"[\d\s()+-]{7,25}", value) and 7 <= len(digits) <= 15 else ""

        values = {
            "NumFormulario": capture(r"FORMULARIO\s+NO\s+(\d+)"),
            "CodPuesto": capture(r"COD\s+PUESTO\s+(\d+)"),
            "FechaInscripcion": date_after(r"FECHA\s+DE\s+INSCRIPCION"),
            "FechaExpedicion": date_after(r"FECHA\s+DE\s+EXPEDICION"),
            "PrimerApellido": capture(r"PRIMER\s+APELLIDO\s+(.+?)\s+SEGUNDO\s+APELLIDO"),
            "SegundoApellido": capture(r"SEGUNDO\s+APELLIDO\s+(.+?)\s+PRIMER\s+NOMBRE"),
            "PrimerNombre": capture(r"PRIMER\s+NOMBRE\s+(.+?)\s+SEGUNDO\s+NOMBRE"),
            "SegundoNombre": capture(r"SEGUNDO\s+NOMBRE\s+(.+?)\s+NIVEL\s+DE\s+ESTUDIO"),
            "CorreoElectronico": capture(r"CORREO\s+ELECTRONICO\s+(.+?)\s+HUELLA"),
            "CiudadMunicipio": capture(r"CIUDAD\s+MUNICIPIO\s+DE\s+RESIDENCIA\s+(.+?)\s+DIRECCION"),
            "DireccionResidencia": capture(r"DIRECCION\s+Y\s+O\s+LUGAR\s+DE\s+RESIDENCIA\s+(.+?)\s+TELEFONO\s+MOVIL"),
            "TelefonoMovil": phone_between(r"TELEFONO\s+MOVIL\s+(.+?)\s+TELEFONO\s+(?:FIJO|FUO)"),
            "TelefonoFijo": phone_between(r"TELEFONO\s+(?:FIJO|FUO)\s+(.+?)\s+LEE\s+BRAILLE"),
        }
        values["FechaInscripcion"] = ExtractOcrUseCase._date_from_section(pages or [], "FECHA DE INSCRIPCION") or values["FechaInscripcion"]
        values["FechaExpedicion"] = ExtractOcrUseCase._date_from_section(pages or [], "FECHA DE EXPEDICION") or values["FechaExpedicion"]
        values["TipoDocumento"] = ExtractOcrUseCase._selected_option(
            pages or [], "TIPO DE DOCUMENTO", ("CEDULA DE CIUDADANIA", "CEDULA DE EXTRANJERIA")
        )
        values["LeeBraile"] = ExtractOcrUseCase._selected_option(pages or [], "LEE BRAILLE", ("SI", "NO"))
        document_number = capture(r"NUMERO\s+DE\s+DOCUMENTO\s+(.+?)\s+FECHA\s+DE\s+EXPEDICION")
        values["NumeroDocumento"] = re.sub(r"\D", "", document_number)
        values["Crops"] = "[]"
        return values

    @staticmethod
    def _selected_option(pages: list[dict], heading: str, options: tuple[str, ...]) -> str:
        blocks = [block for page in pages for block in page.get("blocks", []) if not block.get("skipped")]
        heading_index = next((i for i, block in enumerate(blocks) if heading in ExtractOcrUseCase._normalized_markup(block.get("html", ""))), None)
        if heading_index is None:
            return ""
        options_pattern = "|".join(map(re.escape, options))
        for block in blocks[heading_index : heading_index + 3]:
            markup = ExtractOcrUseCase._normalized_markup(block.get("html", ""))
            for option in options:
                pattern = rf"\b{re.escape(option)}\b(?:(?!{options_pattern}).)*?<INPUT[^>]*\bCHECKED(?:=|\b)"
                if re.search(pattern, markup, re.DOTALL):
                    return option.title()
        return ""

    @staticmethod
    def _date_from_section(pages: list[dict], heading: str) -> str:
        blocks = [block for page in pages for block in page.get("blocks", []) if not block.get("skipped")]
        heading_index = next((i for i, block in enumerate(blocks) if heading in ExtractOcrUseCase._normalized_markup(block.get("html", ""))), None)
        if heading_index is None:
            return ""
        for block in blocks[heading_index + 1 : heading_index + 3]:
            markup = ExtractOcrUseCase._normalized_markup(block.get("html", ""))
            if not all(label in markup for label in ("DIA", "MES", "ANO")):
                continue
            visible_text = re.sub(r"<[^>]+>", " ", markup)
            digits = "".join(re.findall(r"\d", visible_text))
            if len(digits) < 8:
                continue
            day, month, year = int(digits[:2]), int(digits[2:4]), int(digits[4:8])
            try:
                return date(year, month, day).strftime("%d-%m-%Y")
            except ValueError:
                return ""
        return ""

    @staticmethod
    def _normalized_markup(markup: str) -> str:
        return unicodedata.normalize("NFKD", html.unescape(markup)).encode("ascii", "ignore").decode("ascii").upper()
