# Surya Form POC

POC local con Litestar y Surya OCR para documentos. Surya devuelve bloques OCR en orden de lectura, etiqueta de layout, HTML, polígono, `bbox` y confianza; las tablas se devuelven como HTML estructurado. La API no llama a proveedores cloud.

## Arquitectura

- `domain`: contrato del proveedor y modo de salida.
- `application`: caso de uso que forma la respuesta full-text o estructurada.
- `infrastructure`: adaptador que ejecuta `surya_ocr` y lee su JSON.
- `presentation`: API REST Litestar.

## Flujo de procesamiento

1. El cliente envía una ruta relativa bajo `/media` o carga un archivo mediante la API Litestar.
2. La API valida que la ruta solicitada permanezca dentro del volumen de documentos y entrega el archivo a `surya_ocr`.
3. Surya prepara la imagen y la envía al servidor local `llama.cpp`, que carga los pesos GGUF de Surya 2 y el proyector multimodal. La imagen no se envía a proveedores cloud.
4. El modelo visión-lenguaje interpreta la página y Surya devuelve bloques con HTML, texto, polígonos, cajas, etiquetas de layout y confianza.
5. La aplicación conserva esa respuesta en `ocr.pages` cuando se solicita el modo `structured`; además genera `ocr.full_text` y `ocr.fulltextLimpio`, una versión sin HTML, tildes ni caracteres especiales.
6. Finalmente lee `/schema/E3.json`, montado desde la carpeta local `./schema`, y construye `result.Document`. Cada campo se entrega como `{"Field":{"Key":"<nombre del esquema>","Value":"<valor>"}}`. Los campos sin evidencia OCR quedan vacíos; `Crops` queda como `[]` porque este POC no recibe un proceso de recortes.

## Procesamiento de directorios

`POST /v1/ocr/directory` recibe una ruta relativa a `/media`, crea un trabajo asíncrono y responde inmediatamente con su `job_id`. Un único worker procesa las imágenes de forma secuencial para no competir por CPU ni por el único slot de inferencia. Consulte `GET /v1/ocr/directory/{job_id}` para ver el estado, el avance y los archivos generados.

Los artefactos se guardan en `./output/<job_id>` en el host, montado como `/output` en el contenedor. Para cada imagen se generan cinco archivos y cada lote incluye un consolidado CSV:

- `<imagen>_full_text.txt`: texto OCR original sin HTML.
- `<imagen>_fulltextLimpio.txt`: texto normalizado sin caracteres especiales.
- `<imagen>_fulltext_detail.json`: páginas y bloques nativos de Surya.
- `<imagen>_result.json`: extracción `Document/Field/Key/Value` basada en `E3.json`.
- `<imagen>_report.json`: atributos del archivo, estado y duración. El CLI de Surya no expone de forma fiable los tokens ni el prompt interno por solicitud; esos campos aparecen como `null` con una nota explicativa.
- `ocr_batch_results.csv`: una fila por campo extraído con `nombreArchivo`, `processing_time_seconds`, `Key` y `value`.

## Requisitos on-premise

Surya 2 necesita un backend VLM. Esta POC está ajustada para la estación actual (i7-1255U, 16 GB RAM e Intel UHD): la API usa un servidor local `llama.cpp` en CPU, limitado a 8 hilos y una solicitud simultánea. No usa `vLLM`, CUDA ni `gpus: all`.

Antes de iniciar, transfiera a `./models/surya-ocr-2-gguf` los dos artefactos aprobados de `datalab-to/surya-ocr-2-gguf`:

- `surya-2.gguf`
- `surya-2-mmproj.gguf`

No hay descargas en ejecución (`HF_HUB_OFFLINE=1`). En Docker Desktop asigne al menos 10 GB de memoria; la POC reserva hasta 10 GB para la inferencia y deja recursos para Windows. El rendimiento esperado es de POC, no de procesamiento masivo: procese una solicitud a la vez y use documentos cortos durante las pruebas iniciales.

También revise la licencia de los pesos antes de uso comercial: el código de Surya es Apache-2.0, pero los pesos tienen una licencia distinta.

## Ejecución

```powershell
Copy-Item .env.example .env
docker compose up --build
```

La primera carga del modelo puede tardar varios minutos. Espere que `surya-inference` inicie antes de invocar la API.

Las dos POC comparten el volumen `C:\Users\michael.duran\Downloads\20260908 (1)\20260907`, montado como `/media` en modo solo lectura.

El esquema de extracción se monta en `/schema` desde `./schema`. La variable `OCR_SCHEMA_ROOT=/schema` debe estar definida en `.env`.

```powershell
Invoke-RestMethod http://localhost:8003/v1/ocr/path -Method Post -ContentType 'application/json' -Body '{"path":"6000000026.tif","mode":"structured","include_image":false}'
```

Para procesar todas las imágenes de una carpeta bajo el volumen de documentos:

```powershell
Invoke-RestMethod http://localhost:8003/v1/ocr/directory -Method Post -ContentType 'application/json' -Body '{"path":"lote-e3"}'
```

`full_text` concatena el contenido OCR; `fulltextLimpio` elimina HTML y caracteres especiales. `structured` preserva la respuesta nativa por página y bloque. `result.Document` entrega los campos definidos por `E3.json` con el formato `Field/Key/Value`.
