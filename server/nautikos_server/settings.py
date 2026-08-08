from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "server/.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    nautikos_data_root: Path = Path("/home/jovyan/work/caspiansea/data-v2")
    nautikos_allowed_origins: str = "https://nautikos-caspian.vercel.app"
    cdse_s3_endpoint: str = "https://eodata.dataspace.copernicus.eu"
    cdse_s3_access_key: str = ""
    cdse_s3_secret_key: str = ""

    @property
    def allowed_origins(self) -> list[str]:
        return [value.strip() for value in self.nautikos_allowed_origins.split(",") if value.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()

