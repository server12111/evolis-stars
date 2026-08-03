import json
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from bot.database.models import ChatGameRound
from bot.database.repositories.base import BaseRepository


class ChatGameRoundRepository(BaseRepository):
    async def get_active(self, chat_id: int, user_id: int, game_type: str) -> ChatGameRound | None:
        result = await self.session.execute(
            select(ChatGameRound).where(
                ChatGameRound.chat_id == chat_id,
                ChatGameRound.user_id == user_id,
                ChatGameRound.game_type == game_type,
            )
        )
        return result.scalar_one_or_none()

    async def create(
        self, chat_id: int, user_id: int, game_type: str, bet: float, state: dict
    ) -> ChatGameRound | None:
        """Returns None (instead of raising) if a round is already active —
        the unique constraint is the actual concurrency guard; the caller's
        prior get_active() check is just an optimization to skip the
        round-trip in the common case."""
        round_ = ChatGameRound(
            chat_id=chat_id,
            user_id=user_id,
            game_type=game_type,
            bet=Decimal(str(bet)),
            level=0,
            state_json=json.dumps(state),
        )
        self.session.add(round_)
        try:
            await self.session.flush()
        except IntegrityError:
            await self.session.rollback()
            return None
        await self.session.commit()
        return round_

    async def save_state(self, round_: ChatGameRound, state: dict, level: int | None = None) -> None:
        round_.state_json = json.dumps(state)
        if level is not None:
            round_.level = level
        await self.session.commit()

    async def delete(self, round_: ChatGameRound) -> None:
        await self.session.delete(round_)
        await self.session.commit()

    @staticmethod
    def load_state(round_: ChatGameRound) -> dict:
        return json.loads(round_.state_json)
