from sqlalchemy import select

from bot.database.models import ContentItem
from bot.database.repositories.base import BaseRepository

CONTENT_KEYS: dict[str, str] = {
    "welcome": "👋 Приветствие",
    "main_menu": "🏠 Главное меню",
    "earn": "💸 Заработать",
    "withdraw": "🌟 Вывод",
    "bonus": "🎁 Бонус",
    "tasks": "📋 Задания",
    "games": "🎮 Игры",
    "profile": "👤 Профиль",
    "top": "🏆 Топ",
    "tos": "📜 Соглашение",
}

DEFAULT_TEXTS: dict[str, str] = {
    "welcome": (
        "👋 <b>Добро пожаловать!</b>\n\n"
        "Здесь ты зарабатываешь RP⭐️: приглашай друзей, выполняй задания, "
        "забирай ежедневный бонус и пробуй удачу в играх.\n\n"
        "Выбери, с чего начать 👇"
    ),
    "main_menu": "Выбери раздел:",
    "earn": (
        "💸 <b>Заработать</b>\n\n"
        "Приглашай друзей по своей реферальной ссылке и получай звезды за "
        "каждого спонсора, на которого они подписались!\n\n"
        "🔹 <b>Награда зависит от числа спонсоров, на которых подпишется реферал:</b>\n"
        "<blockquote>"
        "• 0-{min_sponsors_minus_1} спонсоров — не засчитывается\n"
        "• 3-5 спонсоров: <b>{referral_reward} RP⭐️</b>\n"
        "• 6-8 спонсоров: <b>{referral_reward_above_5} RP⭐️</b>\n"
        "• 9+ спонсоров: <b>{referral_reward_top} RP⭐️</b>"
        "</blockquote>\n"
        "💎 Реферал с Telegram Premium: всегда <b>{referral_reward_premium} RP⭐️</b>, "
        "независимо от числа спонсоров\n\n"
        "⭐️ 1 Star⭐ = 3 RP⭐️\n\n"
        "⚠️ <i>Реферал будет засчитан и принесёт награду только в том "
        "случае, если подпишется минимум на {min_sponsors} {min_sponsors_word}.</i>\n\n"
        "👑 <b>Бонусы за рефералов</b>\n"
        "Получай дополнительные бонусы при достижении целей:\n"
        "<blockquote>"
        "• 10 рефералов: +{bonus_10} RP⭐️\n"
        "• 20 рефералов: +{bonus_20} RP⭐️\n"
        "• 30 рефералов: +{bonus_30} RP⭐️\n"
        "• 40 рефералов: +{bonus_40} RP⭐️\n"
        "• 50 рефералов: +{bonus_50} RP⭐️ и статус VIP! 🌟\n"
        "• 65 рефералов: +{bonus_65} RP⭐️\n"
        "• 80 рефералов: +{bonus_80} RP⭐️\n"
        "• 90 рефералов: +{bonus_90} RP⭐️\n"
        "• 100 рефералов: +{bonus_100} RP⭐️\n"
        "• 120 рефералов: +{bonus_120} RP⭐️\n"
        "• 140 рефералов: +{bonus_140} RP⭐️\n"
        "• 145 рефералов: +{bonus_145} RP⭐️\n"
        "• 150 рефералов: +{bonus_150} RP⭐️\n"
        "• 155 рефералов: +{bonus_155} RP⭐️\n"
        "• 170 рефералов: +{bonus_170} RP⭐️\n"
        "• 190 рефералов: +{bonus_190} RP⭐️\n"
        "• 200 рефералов: +{bonus_200} RP⭐️ и статус Premium! 👑\n"
        "• 250 рефералов: +{bonus_250} RP⭐️\n"
        "• 350 рефералов: +{bonus_350} RP⭐️\n"
        "• 450 рефералов: +{bonus_450} RP⭐️"
        "</blockquote>\n\n"
        "♻️ <b>Растущая ставка за каждого нового реферала:</b>\n"
        "<blockquote>"
        "• с {premium_threshold}-го: +{recurring_200} RP⭐️ за каждого 👑\n"
        "• с {sigma_threshold}-го: +{recurring_300} RP⭐️ за каждого 🐺 (статус Sigma)\n"
        "• с 400-го: +{recurring_400} RP⭐️ за каждого\n"
        "• с {good_threshold}-го: +{recurring_500} RP⭐️ за каждого 🌟 (статус Good)"
        "</blockquote>"
    ),
    "withdraw": (
        "RP⭐️ <b>Вывод средств</b>\n\n"
        "💰 Твой баланс: <b>{balance} RP⭐️</b>\n\n"
        "Курс вывода: <b>3 RP⭐️ = 1 Telegram ⭐</b>\n\n"
        "Звёзды выводятся на твой Telegram-аккаунт. Выбери сумму ниже — "
        "заявку рассмотрит администратор."
    ),
    "bonus": (
        "🎁 <b>Ежедневный бонус</b>\n\n"
        "Раз в 24 часа забирай случайный бонус от <b>{bonus_min} RP⭐️</b> до "
        "<b>{bonus_max} RP⭐️</b> — просто заходи и жми кнопку ниже."
    ),
    "tasks": (
        "📋 <b>Задания</b>\n\n"
        "Подписывайся на каналы и выполняй простые задания — за каждое "
        "получаешь <b>{tasks_reward} RP⭐️</b>."
    ),
    "games": (
        "🎮 <b>Игры</b>\n\n"
        "Кубики, футбол, слоты, мины, башня, дуэли и не только — "
        "испытай удачу!\n\n"
        "💰 Баланс: <b>{balance} RP⭐️</b>"
    ),
    "profile": (
        "👤 <b>Профиль</b>\n\n"
        "Имя: <b>{name}</b>\n"
        "ID: <code>{user_id}</code>\n"
        "Username: {username}\n\n"
        "💰 Баланс: <b>{balance} RP⭐️</b>\n"
        "👥 Рефералов: <b>{referrals}</b>\n\n"
        "Есть промокод? Активируй его кнопкой ниже 👇"
    ),
    "top": (
        "🏆 <b>Топ игроков</b>\n\n"
        "Лидеры по количеству рефералов и по балансу RP⭐️ — выбери рейтинг:"
    ),
    "tos": (
        "📜 Перед запуском бота вы принимаете пользовательское соглашение "
        "и политику конфиденциальности — ознакомьтесь по кнопкам ниже 🔗"
        "\n\n——"
    ),
}


class ContentRepository(BaseRepository):
    async def get(self, key: str) -> ContentItem | None:
        return await self.session.get(ContentItem, key)

    async def get_text(self, key: str) -> str:
        item = await self.get(key)
        if item and item.text:
            return item.text
        return DEFAULT_TEXTS.get(key, "")

    async def get_photo(self, key: str) -> str | None:
        item = await self.get(key)
        return item.photo_file_id if item else None

    async def all(self) -> list[ContentItem]:
        result = await self.session.execute(select(ContentItem).order_by(ContentItem.key))
        return list(result.scalars().all())

    async def reset_text(self, key: str) -> None:
        """Drop the stored override for `key`'s text so get_text() falls back
        to DEFAULT_TEXTS again — including any future code changes to it,
        unlike re-copying the current default text into the row."""
        item = await self.session.get(ContentItem, key)
        if item:
            item.text = None
            await self.session.commit()

    async def set_text(self, key: str, text: str) -> None:
        item = await self.session.get(ContentItem, key)
        if item:
            item.text = text
        else:
            self.session.add(ContentItem(key=key, text=text))
        await self.session.commit()

    async def set_photo(self, key: str, photo_file_id: str | None) -> None:
        item = await self.session.get(ContentItem, key)
        if item:
            item.photo_file_id = photo_file_id
        else:
            self.session.add(ContentItem(key=key, photo_file_id=photo_file_id))
        await self.session.commit()

    async def seed_defaults(self) -> None:
        for key in CONTENT_KEYS:
            existing = await self.session.get(ContentItem, key)
            if not existing:
                self.session.add(ContentItem(key=key, text=DEFAULT_TEXTS.get(key)))
        await self.session.commit()
