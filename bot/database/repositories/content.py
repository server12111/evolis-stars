from sqlalchemy import select

from bot.database.models import ContentItem
from bot.database.repositories.base import BaseRepository

CONTENT_KEYS: dict[str, str] = {
    "welcome": "👋 Приветствие",
    "main_menu": "🏠 Главное меню",
    "earn": "💸 Заработать",
    "withdraw": "⭐ Вывод",
    "bonus": "🎁 Бонус",
    "tasks": "📋 Задания",
    "games": "🎮 Игры",
    "profile": "👤 Профиль",
    "top": "🏆 Топ",
}

DEFAULT_TEXTS: dict[str, str] = {
    "welcome": (
        "👋 <b>Добро пожаловать!</b>\n\n"
        "Это реферальный бот. Приглашай друзей и зарабатывай ⭐!"
    ),
    "main_menu": "Выбери раздел:",
    "earn": (
        "💸 <b>Заработать</b>\n\n"
        "Приглашай друзей по своей реферальной ссылке и получай звезды за "
        "каждого спонсора, на которого они подписались!\n\n"
        "🔹 Спонсоры Telegram: <b>{tg_reward} ⭐</b>\n"
        "🔹 Web спонсоры: <b>{web_reward} ⭐</b>\n\n"
        "⚠️ <i>Реферал будет засчитан и принесёт награду только в том "
        "случае, если подпишется минимум на {min_sponsors} {min_sponsors_word}.</i>\n\n"
        "👑 <b>VIP-система и бонусы</b>\n"
        "Получай дополнительные бонусы при достижении целей:\n"
        "• 10 рефералов: +{bonus_10} ⭐\n"
        "• 25 рефералов: +{bonus_25} ⭐\n"
        "• 30 рефералов: +{bonus_30} ⭐\n"
        "• 50 рефералов: +{bonus_50} ⭐ и статус VIP! 🌟\n"
        "• 55 рефералов: +{bonus_55} ⭐\n"
        "• 60 рефералов: +{bonus_60} ⭐\n"
        "• 70 рефералов: +{bonus_70} ⭐\n\n"
        "🚀 После 70 рефералов вы продолжаете получать <b>+{bonus_70} ⭐</b> "
        "за КАЖДОГО следующего реферала!"
    ),
    "withdraw": (
        "⭐ <b>Вывод средств</b>\n\n"
        "💰 Твой баланс: <b>{balance} ⭐</b>\n\n"
        "Выбери сумму для вывода:"
    ),
    "bonus": (
        "🎁 <b>Ежедневный бонус</b>\n\n"
        "Получай случайный бонус каждые 24 часа!"
    ),
    "tasks": (
        "📋 <b>Задания</b>\n\n"
        "Выполняй задания и получай <b>0.3 ⭐</b> за каждое!"
    ),
    "games": (
        "🎮 <b>Игры</b>\n\n"
        "Испытай удачу! Баланс: <b>{balance} ⭐</b>"
    ),
    "profile": (
        "👤 <b>Профиль</b>\n\n"
        "Имя: <b>{name}</b>\n"
        "ID: <code>{user_id}</code>\n"
        "Username: {username}\n\n"
        "💰 Баланс: <b>{balance} ⭐</b>\n"
        "👥 Рефералов: <b>{referrals}</b>"
    ),
    "top": "🏆 <b>Топ игроков</b>",
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
