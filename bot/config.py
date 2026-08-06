import os
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
# Inside any Docker container (Compose or plain `docker run`), /.dockerenv is
# always present — default to the volume-mounted /app/data there so the
# database is guaranteed to persist even if a hosting platform doesn't set
# DATA_DIR/DATABASE_URL itself. An explicit DATA_DIR env var still wins.
_IN_CONTAINER = Path("/.dockerenv").exists()
_DEFAULT_DATA_DIR = "/app/data" if _IN_CONTAINER else str(_PROJECT_ROOT / "data")
_DATA_DIR = Path(os.getenv("DATA_DIR", _DEFAULT_DATA_DIR)).expanduser()
_DATA_DIR.mkdir(parents=True, exist_ok=True)


class Settings(BaseSettings):
    # extra="ignore": an unrecognized .env line (typo, leftover var like the
    # raw DATA_DIR/DATABASE_URL that must be a real OS env var instead — see
    # _DATA_DIR above) would otherwise crash the whole bot at startup.
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    bot_token: str
    admin_ids: str = ""
    database_url: str = f"sqlite+aiosqlite:///{_DATA_DIR / 'bot.db'}"
    redis_url: str = ""
    instance_lock_path: str = str(_DATA_DIR / ".bot-instance.lock")

    admin_channel_id: str = ""
    payments_channel_id: str = ""
    payments_channel_link: str = ""
    broadcast_moderation_channel_id: str = ""

    tgrass_code: str = ""
    botohub_key: str = ""
    botohub_views_key: str = ""
    piarflow_key: str = ""
    flyerhub_key: str = ""
    flyerhub_webapp_url: str = ""
    # Separate FlyerHub bot key used only for the mandatory-subscription wall
    # (/get_tasks + /check_task). Must NOT be a "webapp"-type key -- those
    # reject both methods ("Prohibited method for a bot type"), which is
    # exactly why flyerhub_key above can't be reused here whenever
    # flyerhub_webapp_url is set.
    flyerhub_op_key: str = ""
    linkni_code: str = ""
    traffy_key: str = ""

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
