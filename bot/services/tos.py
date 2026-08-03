from sqlalchemy.ext.asyncio import AsyncSession

from bot.database.repositories.settings import SettingsRepository

_DEFAULT_USER_AGREEMENT_URL = "https://telegra.ph/Polzovatelskoe-soglashenie-EvolisStars-08-03-2"
_DEFAULT_PRIVACY_POLICY_URL = "https://telegra.ph/Politika-konfidencialnosti-EvolisStars-08-03"


async def get_tos_urls(session: AsyncSession) -> tuple[str, str]:
    repo = SettingsRepository(session)
    user_agreement_url = await repo.get("tos_user_agreement_url", _DEFAULT_USER_AGREEMENT_URL)
    privacy_policy_url = await repo.get("tos_privacy_policy_url", _DEFAULT_PRIVACY_POLICY_URL)
    return user_agreement_url, privacy_policy_url
