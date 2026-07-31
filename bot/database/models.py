from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from bot.database.engine import Base

# ─── User ─────────────────────────────────────────────────────────────────────

class User(Base):
    __tablename__ = "users"

    user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    first_name: Mapped[str] = mapped_column(String(128), default="")
    stars_balance: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0"))
    referrals_count: Mapped[int] = mapped_column(Integer, default=0)
    referrer_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.user_id", ondelete="SET NULL"), nullable=True)
    referral_reward_given: Mapped[bool] = mapped_column(Boolean, default=False)
    referral_counted: Mapped[bool] = mapped_column(Boolean, default=False)
    sponsors_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    sponsor_wave: Mapped[int] = mapped_column(Integer, default=0)
    sponsor_wave_one: Mapped[str | None] = mapped_column(Text, nullable=True)
    sponsor_wave_two: Mapped[str | None] = mapped_column(Text, nullable=True)
    tasks_completed_count: Mapped[int] = mapped_column(Integer, default=0)
    is_blocked: Mapped[bool] = mapped_column(Boolean, default=False)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    is_vip: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
    last_bonus_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # game-specific counters (ported from SrvNkreferal)
    slots_777_count: Mapped[int] = mapped_column(Integer, default=0)
    darts_bullseye_count: Mapped[int] = mapped_column(Integer, default=0)


class ReferralReactivation(Base):
    """Immutable ledger entry for a rewarded referral return."""

    __tablename__ = "referral_reactivations"
    __table_args__ = (
        UniqueConstraint(
            "referred_user_id",
            "inactive_since",
            name="uq_referral_reactivation_cycle",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    referred_user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.user_id", ondelete="CASCADE"),
        nullable=False,
    )
    referrer_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.user_id", ondelete="CASCADE"),
        nullable=False,
    )
    inactive_since: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    returned_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.now(),
    )
    reward_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)


# ─── BotSettings ──────────────────────────────────────────────────────────────

class BotSettings(Base):
    __tablename__ = "bot_settings"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")


# ─── GameSession ──────────────────────────────────────────────────────────────

class GameSession(Base):
    __tablename__ = "game_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.user_id", ondelete="CASCADE"))
    game_type: Mapped[str] = mapped_column(String(32))  # football/basketball/bowling/dice/slots/darts/wheel/case_1/case_3/case_5
    bet: Mapped[Decimal] = mapped_column(Numeric(14, 4))
    result: Mapped[str] = mapped_column(String(8))  # win / lose
    payout: Mapped[Decimal] = mapped_column(Numeric(14, 4), default=Decimal("0"))
    played_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


# ─── Duel ─────────────────────────────────────────────────────────────────────

class Duel(Base):
    __tablename__ = "duels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    creator_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.user_id", ondelete="CASCADE"))
    joiner_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.user_id", ondelete="SET NULL"), nullable=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    status: Mapped[str] = mapped_column(String(16), default="waiting")  # waiting/confirming/active/finished/cancelled
    creator_roll: Mapped[int | None] = mapped_column(Integer, nullable=True)
    joiner_roll: Mapped[int | None] = mapped_column(Integer, nullable=True)
    winner_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


# ─── Lottery ──────────────────────────────────────────────────────────────────

class Lottery(Base):
    __tablename__ = "lotteries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    status: Mapped[str] = mapped_column(String(16), default="active")  # active / finished
    tickets_sold: Mapped[int] = mapped_column(Integer, default=0)
    total_collected: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0"))
    prize_pool: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0"))
    ticket_price: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("5"))
    ticket_limit: Mapped[int] = mapped_column(Integer, default=0)  # 0 = unlimited
    ref_required: Mapped[int] = mapped_column(Integer, default=0)
    channel_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    end_type: Mapped[str] = mapped_column(String(16), default="tickets")  # tickets / time / commission
    end_value: Mapped[float] = mapped_column(Numeric(14, 4), default=100)
    winner_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    drawn_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    tickets: Mapped[list["LotteryTicket"]] = relationship(back_populates="lottery", lazy="select")


class LotteryTicket(Base):
    __tablename__ = "lottery_tickets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    lottery_id: Mapped[int] = mapped_column(Integer, ForeignKey("lotteries.id", ondelete="CASCADE"))
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.user_id", ondelete="CASCADE"))
    bought_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    lottery: Mapped["Lottery"] = relationship(back_populates="tickets")


# ─── Withdrawal ───────────────────────────────────────────────────────────────

class Withdrawal(Base):
    __tablename__ = "withdrawals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.user_id", ondelete="CASCADE"))
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending / approved / rejected
    channel_message_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    admin_message_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    processed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


# ─── PromoCode ────────────────────────────────────────────────────────────────

class PromoCode(Base):
    __tablename__ = "promo_codes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(64), unique=True)
    reward_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    usage_limit: Mapped[int] = mapped_column(Integer, default=0)  # 0 = unlimited
    used_count: Mapped[int] = mapped_column(Integer, default=0)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    uses: Mapped[list["PromoUse"]] = relationship(back_populates="promo", lazy="select")


class PromoUse(Base):
    __tablename__ = "promo_uses"
    __table_args__ = (
        UniqueConstraint("code_id", "user_id", name="uq_promo_use_code_user"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code_id: Mapped[int] = mapped_column(Integer, ForeignKey("promo_codes.id", ondelete="CASCADE"))
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.user_id", ondelete="CASCADE"))
    used_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    promo: Mapped["PromoCode"] = relationship(back_populates="uses")


# ─── Task ─────────────────────────────────────────────────────────────────────

class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(256))
    description: Mapped[str] = mapped_column(Text, default="")
    url: Mapped[str] = mapped_column(Text, default="")
    photo_file_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    task_type: Mapped[str] = mapped_column(String(32), default="external_url")  # channel_sub / bot_start / external_url
    reward: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0.3"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    max_completions: Mapped[int] = mapped_column(Integer, default=0)  # 0 = unlimited
    completions_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class TaskCompletion(Base):
    __tablename__ = "task_completions"
    __table_args__ = (
        UniqueConstraint("task_id", "user_id", name="uq_task_completion_task_user"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(Integer, ForeignKey("tasks.id", ondelete="CASCADE"))
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.user_id", ondelete="CASCADE"))
    completed_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


# ─── ContentItem ──────────────────────────────────────────────────────────────

class ContentItem(Base):
    __tablename__ = "content_items"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    photo_file_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    video_file_id: Mapped[str | None] = mapped_column(String(256), nullable=True)


# ─── Auction ──────────────────────────────────────────────────────────────────

class AuctionRound(Base):
    __tablename__ = "auction_rounds"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    status: Mapped[str] = mapped_column(String(16), default="active")  # active / finished
    current_bid: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0"))
    current_bidder_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.user_id", ondelete="SET NULL"), nullable=True)
    prize_pool: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0"))
    start_at: Mapped[datetime] = mapped_column(DateTime)
    end_at: Mapped[datetime] = mapped_column(DateTime)
    winner_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    bids: Mapped[list["AuctionBid"]] = relationship(back_populates="round", lazy="select")


class AuctionBid(Base):
    __tablename__ = "auction_bids"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    round_id: Mapped[int] = mapped_column(Integer, ForeignKey("auction_rounds.id", ondelete="CASCADE"))
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.user_id", ondelete="CASCADE"))
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    bid_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    round: Mapped["AuctionRound"] = relationship(back_populates="bids")
