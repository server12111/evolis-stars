import os
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DATA_DIR = Path(
    os.getenv("DATA_DIR", str(_PROJECT_ROOT / "data"))
).expanduser()
_DATA_DIR.mkdir(parents=True, exist_ok=True)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    bot_token: str
    admin_ids: str = ""
    database_url: str = f"sqlite+aiosqlite:///{_DATA_DIR / 'bot.db'}"
    redis_url: str = ""
    instance_lock_path: str = str(_PROJECT_ROOT / ".bot-instance.lock")

    admin_channel_id: str = ""
    payments_channel_id: str = ""
    payments_channel_link: str = ""

    tgrass_code: str = ""
    botohub_key: str = ""
    botohub_views_key: str = ""
    piarflow_key: str = ""
    flyerhub_key: str = ""

    bot_username: str = ""

    @property
    def admin_id_list(self) -> list[int]:
        result: list[int] = []
        for value in self.admin_ids.split(","):
            value = value.strip()
            if not value:
                continue
            try:
                result.append(int(value))
            except ValueError:
                continue
        return result


@lru_cache
def get_settings() -> Settings:
    return Settings()
