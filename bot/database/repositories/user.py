from datetime import date, datetime

from sqlalchemy import desc, func, select

from bot.database.models import User
from bot.database.repositories.base import BaseRepository


class UserRepository(BaseRepository):
    async def get(self, user_id: int) -> User | None:
        return await self.session.get(User, user_id)

    async def get_or_create(
        self,
        user_id: int,
        username: str | None,
        first_name: str,
        referrer_id: int | None = None,
        is_premium: bool = False,
    ) -> tuple[User, bool, datetime | None]:
        user = await self.session.get(User, user_id)
        if user:
            previous_last_seen_at = user.last_seen_at
            user.username = username
            user.first_name = first_name
            user.last_seen_at = datetime.utcnow()
            user.is_premium = is_premium
            await self.session.commit()
            return user, False, previous_last_seen_at
        user = User(
            user_id=user_id,
            username=username,
            first_name=first_name,
            referrer_id=referrer_id,
            is_premium=is_premium,
        )
        self.session.add(user)
        await self.session.commit()
        return user, True, None

    async def inactive_rewarded_referrals(
        self,
        referrer_id: int,
        inactive_before: datetime,
        offset: int = 0,
        limit: int = 8,
    ) -> tuple[list[User], int, int]:
        filters = (
            User.referrer_id == referrer_id,
            User.referral_counted.is_(True),
            User.referral_reward_given.is_(True),
            User.is_blocked.is_(False),
            User.last_seen_at <= inactive_before,
        )
        total = (
            await self.session.execute(
                select(func.count(User.user_id)).where(*filters)
            )
        ).scalar() or 0
        contactable_filter = (*filters, User.username.is_not(None), User.username != "")
        contactable = (
            await self.session.execute(
                select(func.count(User.user_id)).where(*contactable_filter)
            )
        ).scalar() or 0
        result = await self.session.execute(
            select(User)
            .where(*contactable_filter)
            .order_by(User.last_seen_at.asc(), User.user_id.asc())
            .offset(max(0, offset))
            .limit(max(1, limit))
        )
        return list(result.scalars().all()), int(total), int(contactable)

    async def total_count(self) -> int:
        result = await self.session.execute(select(func.count(User.user_id)))
        return result.scalar() or 0

    async def today_count(self) -> int:
        today = datetime.combine(date.today(), datetime.min.time())
        result = await self.session.execute(
            select(func.count(User.user_id)).where(User.created_at >= today)
        )
        return result.scalar() or 0

    async def total_balance(self) -> float:
        result = await self.session.execute(select(func.sum(User.stars_balance)))
        return float(result.scalar() or 0)

    async def top_by_referrals(self, limit: int = 10) -> list[User]:
        result = await self.session.execute(
            select(User).order_by(desc(User.referrals_count)).limit(limit)
        )
        return list(result.scalars().all())

    async def top_by_balance(self, limit: int = 10) -> list[User]:
        result = await self.session.execute(
            select(User).order_by(desc(User.stars_balance)).limit(limit)
        )
        return list(result.scalars().all())

    async def find_by_username(self, username: str) -> User | None:
        uname = username.lstrip("@")
        result = await self.session.execute(
            select(User).where(User.username == uname)
        )
        return result.scalar_one_or_none()

    async def all_active_ids(self) -> list[int]:
        result = await self.session.execute(
            select(User.user_id).where(User.is_blocked == False)
        )
        return list(result.scalars().all())
