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
            "result": self._extract_result(clean_text, pages, full_text),
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

    def _extract_result(
        self, clean_text: str, pages: list[dict] | None = None, source_text: str | None = None
    ) -> dict:
        schema_text = (self._schema_root / "E3.json").read_text(encoding="utf-8-sig")
        try:
            fields = json.loads(schema_text)["typologies"][0]["fields"]
        except json.JSONDecodeError:
            # Some operational schemas include unescaped quotation marks in descriptions.
            # Field names remain authoritative and are sufficient for the response contract.
            fields = [{"name": name} for name in re.findall(r'"name"\s*:\s*"([^"]+)"', schema_text)]
        values = self._e3_values(clean_text, pages or [], source_text)
        return {
            "Document": [
                {"Field": {"Key": field["name"], "Value": values.get(field["name"], "")}}
                for field in fields
            ]
        }

    @staticmethod
    def _e3_values(text: str, pages: list[dict] | None = None, source_text: str | None = None) -> dict[str, str]:
        def capture(pattern: str, stop: str = "") -> str:
            match = re.search(pattern, text, re.IGNORECASE)
            if not match:
                return ""
            value = match.group(1).strip()
            if stop:
                value = re.split(stop, value, maxsplit=1, flags=re.IGNORECASE)[0].strip()
            return value

        def date_after(label: str) -> str:
            match = re.search(label + r".*?(\d\s*\d)\s+(\d\s*\d)\s+((?:\d\s*){4})(?!\d)", text, re.IGNORECASE)
            if not match:
                return ""
            day, month, year = (re.sub(r"\s", "", component) for component in match.groups())
            try:
                return date(int(year), int(month), int(day)).strftime("%d-%m-%Y")
            except ValueError:
                return ""

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
            "CorreoElectronico": ExtractOcrUseCase._email_from_section(source_text or text),
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
        values["NivelEstudio"] = ExtractOcrUseCase._selected_option(
            pages or [], "NIVEL DE ESTUDIO", ("NINGUNO", "PRIMARIA", "BACHILLERATO", "TECNICO", "PROFESIONAL")
        )
        values["NovedadHuellas"] = json.dumps(
            ExtractOcrUseCase._checkbox_states(
                pages or [],
                "AMPUTACION AMBAS MANOS",
                {
                    "AmputacionAmbasManos": "AMPUTACION AMBAS MANOS",
                    "EnfermedadManosHuellas": "ENFERM MANOS Y O HUELLAS",
                    "MalformacionExtremidades": "MALFORM EXTREMIDADES",
                },
            ),
            ensure_ascii=False,
        )
        document_number = capture(r"NUMERO\s+DE\s+DOCUMENTO\s+(.+?)\s+FECHA\s+DE\s+EXPEDICION")
        values["NumeroDocumento"] = re.sub(r"\D", "", document_number)
        values["Crops"] = "[]"
        return values

    @staticmethod
    def _selected_option(pages: list[dict], heading: str, options: tuple[str, ...]) -> str:
        states = ExtractOcrUseCase._checkbox_states(
            pages, heading, {option.title(): option for option in options}, return_empty_when_absent=True
        )
        return next((label for label, selected in states.items() if selected), "")

    @staticmethod
    def _checkbox_states(
        pages: list[dict], heading: str, options: dict[str, str], *, return_empty_when_absent: bool = False
    ) -> dict[str, bool]:
        blocks = [block for page in pages for block in page.get("blocks", []) if not block.get("skipped")]
        heading_index = next(
            (i for i, block in enumerate(blocks) if heading in ExtractOcrUseCase._normalized_markup(block.get("html", ""))), None
        )
        if heading_index is None:
            return {} if return_empty_when_absent else {name: False for name in options}

        # A field can be emitted as one table block or as several neighbouring blocks.  Preserve
        # the OCR's checkbox representation before removing its HTML tags.
        markup = " ".join(ExtractOcrUseCase._checkbox_markup(block.get("html", "")) for block in blocks[heading_index : heading_index + 5])
        visible = re.sub(r"<[^>]+>", " ", markup)
        visible = re.sub(r"[^A-Z0-9\[\]\s]", " ", ExtractOcrUseCase._normalized_markup(visible))
        visible = re.sub(r"\s+", " ", visible).strip()

        states: dict[str, bool] = {}
        option_matches = [
            match
            for option in options.values()
            for match in re.finditer(rf"\b{re.escape(option)}\b", visible)
        ]
        for name, option in options.items():
            match = next((candidate for candidate in option_matches if candidate.group() == option), None)
            if not match:
                states[name] = False
                continue
            next_option_start = min(
                (candidate.start() for candidate in option_matches if candidate.start() > match.end()), default=len(visible)
            )
            marker = re.search(r"\[\s*([X ])\s*\]|\b(X)\b", visible[match.end() : next_option_start])
            states[name] = bool(marker and (marker.group(1) == "X" or marker.group(2) == "X"))
        return states

    @staticmethod
    def _checkbox_markup(markup: str) -> str:
        def input_marker(match: re.Match[str]) -> str:
            attributes = match.group(1)
            return " [X] " if re.search(r"\bchecked(?:\s*=|\b)", attributes, re.IGNORECASE) else " [ ] "

        marked_up = re.sub(r"<input\b([^>]*)>", input_marker, html.unescape(markup), flags=re.IGNORECASE)
        return marked_up.translate(str.maketrans({"☒": "[X]", "☑": "[X]", "✓": "[X]", "✔": "[X]", "☐": "[ ]"}))

    @staticmethod
    def _date_from_section(pages: list[dict], heading: str) -> str:
        blocks = [block for page in pages for block in page.get("blocks", []) if not block.get("skipped")]
        heading_index = next((i for i, block in enumerate(blocks) if heading in ExtractOcrUseCase._normalized_markup(block.get("html", ""))), None)
        if heading_index is None:
            return ""
        markup = " ".join(block.get("html", "") for block in blocks[heading_index : heading_index + 3])
        visible_text = re.sub(r"<[^>]+>", " ", ExtractOcrUseCase._normalized_markup(markup))
        match = re.search(r"\b(\d\s*\d)\s+(\d\s*\d)\s+((?:\d\s*){4})(?!\d)", visible_text)
        if not match:
            return ""
        day, month, year = (re.sub(r"\s", "", component) for component in match.groups())
        try:
            return date(int(year), int(month), int(day)).strftime("%d-%m-%Y")
        except ValueError:
            return ""

    @staticmethod
    def _email_from_section(text: str) -> str:
        section = re.search(
            r"CORREO\s+ELECTR(?:O|Ó)NICO\s+(.+?)(?=\s+(?:TEL(?:E|É)FONO|CIUDAD|DIRECCI(?:O|Ó)N|LEE\s+BRAILLE|TIPO\s+DE\s+DISCAPACIDAD)\b|$)",
            text,
            re.IGNORECASE | re.DOTALL,
        )
        if not section:
            return ""
        candidate = re.sub(r"\s*([@._%+\-])\s*", r"\1", section.group(1))
        candidate = re.sub(r"\s+", " ", candidate)
        match = re.search(r"[A-Z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Z0-9-]+(?:\.[A-Z0-9-]+)+", candidate, re.IGNORECASE)
        return match.group(0) if match else ""

    @staticmethod
    def _normalized_markup(markup: str) -> str:
        return unicodedata.normalize("NFKD", html.unescape(markup)).encode("ascii", "ignore").decode("ascii").upper()
