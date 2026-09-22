from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    ocr_documents_root: Path
    ocr_schema_root: Path
    ocr_output_root: Path

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
