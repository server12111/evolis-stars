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
        "Приглашай друзей по реферальной ссылке и получай награду!\n\n"
        "👥 Приглашено: <b>{referrals}</b>\n"
        "🔗 Твоя ссылка:\n<code>{link}</code>"
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
