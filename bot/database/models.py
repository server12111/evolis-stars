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
    referral_insufficient_notified: Mapped[bool] = mapped_column(Boolean, default=False)
    sponsors_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    tos_accepted: Mapped[bool] = mapped_column(Boolean, default=False)
    tos_gate_shown: Mapped[bool] = mapped_column(Boolean, default=False)
    sponsor_wave: Mapped[int] = mapped_column(Integer, default=0)
    sponsor_wave_one: Mapped[str | None] = mapped_column(Text, nullable=True)
    sponsor_wave_two: Mapped[str | None] = mapped_column(Text, nullable=True)
    tasks_completed_count: Mapped[int] = mapped_column(Integer, default=0)
    is_blocked: Mapped[bool] = mapped_column(Boolean, default=False)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    is_vip: Mapped[bool] = mapped_column(Boolean, default=False)
    is_premium: Mapped[bool] = mapped_column(Boolean, default=False)  # Telegram Premium, synced from tg_user.is_premium on every update
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
    last_bonus_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_random_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    free_game_credits: Mapped[int] = mapped_column(Integer, default=0)  # unused, kept for backward compat — superseded by free_game_credit_amount
    # Amount (1/2/3 ⭐) of the single outstanding free-game credit from the
    # Random button, or NULL if none is pending. A fresh Random press
    # overwrites this (a stale unused credit is simply replaced).
    free_game_credit_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)

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
    game_type: Mapped[str] = mapped_column(String(32))  # football/basketball/bowling/dice/slots/darts/wheel/case_1/case_3/case_5/roulette/safe/maze/doors
    bet: Mapped[Decimal] = mapped_column(Numeric(14, 4))
    result: Mapped[str] = mapped_column(String(8))  # win / lose
    payout: Mapped[Decimal] = mapped_column(Numeric(14, 4), default=Decimal("0"))
    played_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)  # set only for the 4 chat-only games
    bet_choice: Mapped[str | None] = mapped_column(String(16), nullable=True)  # e.g. roulette color bet on; reusable for future bet-target games
    result_choice: Mapped[str | None] = mapped_column(String(16), nullable=True)  # e.g. roulette color that actually landed


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


# ─── OwnSponsor ───────────────────────────────────────────────────────────────
# Admin-curated cross-promotion (own channels/chats/bots), gated in front of
# the daily bonus claim — distinct from the paid tgrass/botohub/piarflow
# sponsor wall and unrelated to referral rewards.

class OwnSponsor(Base):
    __tablename__ = "own_sponsors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(16))  # "channel" | "bot"
    target: Mapped[str | None] = mapped_column(Text, nullable=True)  # @username/id for get_chat_member (channel only)
    url: Mapped[str] = mapped_column(Text)  # link shown to the user
    name: Mapped[str] = mapped_column(String(128))
    target_count: Mapped[int] = mapped_column(Integer)
    current_count: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class OwnSponsorCompletion(Base):
    __tablename__ = "own_sponsor_completions"
    __table_args__ = (
        UniqueConstraint("sponsor_id", "user_id", name="uq_own_sponsor_completion_sponsor_user"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sponsor_id: Mapped[int] = mapped_column(Integer, ForeignKey("own_sponsors.id", ondelete="CASCADE"))
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


# ─── Chat (group-chat feature) ────────────────────────────────────────────────

class Chat(Base):
    __tablename__ = "chats"

    chat_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    title: Mapped[str] = mapped_column(String(256), default="")
    owner_user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    member_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending / active / left
    broadcast_opt_in: Mapped[bool] = mapped_column(Boolean, default=False)
    ads_revenue_paid_thresholds: Mapped[int] = mapped_column(Integer, default=0)
    ads_bonus_paid: Mapped[bool] = mapped_column(Boolean, default=False)
    added_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    left_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_admin_sync_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_click_ad_posted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class ChatMembership(Base):
    __tablename__ = "chat_memberships"
    __table_args__ = (
        UniqueConstraint("chat_id", "user_id", name="uq_chat_membership_chat_user"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("chats.chat_id", ondelete="CASCADE"))
    # Not FK'd to users.user_id — a chat member can accrue message history for
    # years before ever pressing /start in a private DM with the bot.
    user_id: Mapped[int] = mapped_column(BigInteger)
    joined_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    message_count: Mapped[int] = mapped_column(Integer, default=0)
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    is_admin_in_chat: Mapped[bool] = mapped_column(Boolean, default=False)
    left_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class ChatPromoCode(Base):
    __tablename__ = "chat_promo_codes"
    __table_args__ = (
        UniqueConstraint("chat_id", "code", name="uq_chat_promo_chat_code"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("chats.chat_id", ondelete="CASCADE"))
    code: Mapped[str] = mapped_column(String(64))
    usage_limit: Mapped[int] = mapped_column(Integer, default=0)  # 0 = unlimited
    used_count: Mapped[int] = mapped_column(Integer, default=0)
    created_by: Mapped[int] = mapped_column(BigInteger)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class ChatPromoUse(Base):
    __tablename__ = "chat_promo_uses"
    __table_args__ = (
        UniqueConstraint("code_id", "user_id", name="uq_chat_promo_use_code_user"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code_id: Mapped[int] = mapped_column(Integer, ForeignKey("chat_promo_codes.id", ondelete="CASCADE"))
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.user_id", ondelete="CASCADE"))
    reward_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    used_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class ChatBonusCode(Base):
    __tablename__ = "chat_bonus_codes"
    __table_args__ = (
        UniqueConstraint("chat_id", "code", name="uq_chat_bonus_chat_code"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("chats.chat_id", ondelete="CASCADE"))
    code: Mapped[str] = mapped_column(String(64))
    reward_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    usage_limit: Mapped[int] = mapped_column(Integer)
    used_count: Mapped[int] = mapped_column(Integer, default=0)
    commission_rate: Mapped[Decimal] = mapped_column(Numeric(5, 4), default=Decimal("0.07"))
    total_charged: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    min_days_in_chat: Mapped[int] = mapped_column(Integer, default=0)
    min_messages: Mapped[int] = mapped_column(Integer, default=0)
    condition_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    mode: Mapped[str] = mapped_column(String(16), default="self_serve")  # self_serve / contest
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class ChatBonusUse(Base):
    __tablename__ = "chat_bonus_uses"
    __table_args__ = (
        UniqueConstraint("code_id", "user_id", name="uq_chat_bonus_use_code_user"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code_id: Mapped[int] = mapped_column(Integer, ForeignKey("chat_bonus_codes.id", ondelete="CASCADE"))
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.user_id", ondelete="CASCADE"))
    reward_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    awarded_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)  # set only for contest-mode manual picks
    used_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class ChatAdSend(Base):
    """Append-only attribution ledger — one row per successful BotoHub DM ad
    send that we attribute to a chat's registered membership. count(*) per
    chat_id is the "views" metric for that chat's ad revenue share."""

    __tablename__ = "chat_ad_sends"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("chats.chat_id", ondelete="CASCADE"))
    user_id: Mapped[int] = mapped_column(BigInteger)
    sent_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class ChatLinkButton(Base):
    """Global-admin-managed click-tracked link button, attachable to any
    bot-posted content (DM or in-chat)."""

    __tablename__ = "link_buttons"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    label: Mapped[str] = mapped_column(String(128))
    destination_url: Mapped[str] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class ChatLinkClick(Base):
    __tablename__ = "link_clicks"
    __table_args__ = (
        UniqueConstraint("link_id", "user_id", name="uq_link_click_link_user"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    link_id: Mapped[int] = mapped_column(Integer, ForeignKey("link_buttons.id", ondelete="CASCADE"))
    user_id: Mapped[int] = mapped_column(BigInteger)
    chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)  # which chat the click's message lived in, NULL for DM
    clicked_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class ChatGameRound(Base):
    """Persisted in-progress round for the multi-step chat games (Doors,
    Maze, Tower) — deleted on cash-out/bust/finish/timeout. The unique
    constraint also doubles as the "already have an active round" guard."""

    __tablename__ = "chat_game_rounds"
    __table_args__ = (
        UniqueConstraint("chat_id", "user_id", "game_type", name="uq_chat_game_round_active"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger)
    user_id: Mapped[int] = mapped_column(BigInteger)
    game_type: Mapped[str] = mapped_column(String(16))  # doors / maze / tower
    bet: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    level: Mapped[int] = mapped_column(Integer, default=0)
    state_json: Mapped[str] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
