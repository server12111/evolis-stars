from datetime import datetime, date

from sqlalchemy import select, func

from bot.database.models import GameSession
from bot.database.repositories.base import BaseRepository


class GameRepository(BaseRepository):
    async def add(
        self,
        user_id: int,
        game_type: str,
        bet: float,
        result: str,
        payout: float,
    ) -> GameSession:
        gs = GameSession(
            user_id=user_id,
            game_type=game_type,
            bet=bet,
            result=result,
            payout=payout,
        )
        self.session.add(gs)
        await self.session.commit()
        return gs

    async def daily_count(self, user_id: int, game_type: str) -> int:
        today = datetime.combine(date.today(), datetime.min.time())
        result = await self.session.execute(
            select(func.count(GameSession.id)).where(
                GameSession.user_id == user_id,
                GameSession.game_type == game_type,
                GameSession.played_at >= today,
            )
        )
        return result.scalar() or 0

    async def total_games(self) -> int:
        result = await self.session.execute(select(func.count(GameSession.id)))
        return result.scalar() or 0

    async def today_games(self) -> int:
        today = datetime.combine(date.today(), datetime.min.time())
        result = await self.session.execute(
            select(func.count(GameSession.id)).where(GameSession.played_at >= today)
        )
        return result.scalar() or 0

    async def win_count(self) -> int:
        result = await self.session.execute(
            select(func.count(GameSession.id)).where(GameSession.result == "win")
        )
        return result.scalar() or 0

    async def stats_by_type(self, game_type: str) -> dict:
        result = await self.session.execute(
            select(
                func.count(GameSession.id).label("total"),
                func.sum(GameSession.bet).label("total_bet"),
                func.sum(GameSession.payout).label("total_payout"),
                func.count(GameSession.id).filter(GameSession.result == "win").label("wins"),
            ).where(GameSession.game_type == game_type)
        )
        row = result.one()
        total = row.total or 0
        total_bet = float(row.total_bet or 0)
        total_payout = float(row.total_payout or 0)
        wins = row.wins or 0
        return {
            "total": total,
            "wins": wins,
            "profit": round(total_bet - total_payout, 2),
            "win_rate": round(wins / total * 100, 1) if total else 0,
        }
