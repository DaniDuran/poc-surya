FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 \
    PIP_INDEX_URL=https://pypi.org/simple \
    PIP_TRUSTED_HOST="pypi.org files.pythonhosted.org" \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_DEFAULT_TIMEOUT=120 \
    PIP_RETRIES=5

WORKDIR /app

RUN apt-get update && apt-get install --no-install-recommends -y libglib2.0-0 libgl1 && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY surya_form_poc ./surya_form_poc

RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir .

EXPOSE 8000
CMD ["uvicorn", "surya_form_poc.presentation.api:app", "--host", "0.0.0.0", "--port", "8000"]