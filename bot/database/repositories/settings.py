import math

from sqlalchemy import Float, String, cast, func, select, update

from bot.database.models import BotSettings
from bot.database.repositories.base import BaseRepository

DEFAULT_SETTINGS: dict[str, str] = {
    "referral_reward": "9",
    "referral_reward_above_5": "10.5",
    "referral_reward_top": "13.5",
    "referral_reward_premium": "13.5",
    "min_sponsors_for_reward": "3",
    "referral_bonus_10": "0.1",
    "referral_bonus_20": "0.2",
    "referral_bonus_30": "0.3",
    "referral_bonus_40": "0.4",
    "referral_bonus_50": "0.5",
    "referral_bonus_65": "1",
    "referral_bonus_80": "1.1",
    "referral_bonus_90": "1.2",
    "referral_bonus_100": "1.5",
    "referral_bonus_120": "1.7",
    "referral_bonus_140": "1.9",
    "referral_bonus_145": "1.5",
    "referral_bonus_150": "2",
    "referral_bonus_155": "2.2",
    "referral_bonus_170": "2.2",
    "referral_bonus_190": "2.4",
    "referral_bonus_200": "3",
    "referral_bonus_250": "3",
    "referral_bonus_350": "5",
    "referral_bonus_450": "5",
    # Recurring per-referral bonus: from the referral count in the key
    # onward, EVERY new referral additionally earns this amount on top of
    # the base reward (replaces the previous tier's rate, doesn't stack).
    "referral_recurring_200": "1",
    "referral_recurring_300": "1.67",
    "referral_recurring_400": "2.5",
    "referral_recurring_500": "3",
    "bonus_min": "0.1",
    "bonus_max": "1.0",
    "bonus_enabled": "1",
    "withdraw_enabled": "1",
    # RP⭐️ granted per 1 Telegram Star purchased via "🔄 Обмен" — admin-
    # configurable, affects only purchases made after a change (the rate is
    # snapshotted into each RpPurchase row at purchase time).
    "rp_exchange_rate": "1",
    "games_enabled": "1",
    "tasks_enabled": "1",
    "tasks_reward": "0.3",
    "duel_commission": "20",
    "duel_min_refs": "3",
    "lottery_min_refs": "3",
    "games_min_refs": "3",
    "payments_channel_id": "",
    "payments_channel_link": "",
    "admin_channel_id": "",
    # Custom chat-broadcast texts (and photos/buttons) go here for admin
    # approve/reject before they can ever be sent by the scheduler.
    "broadcast_moderation_channel_id": "",
    # game settings
    "game_football_enabled": "1",
    "game_football_min_bet": "1.0",
    "game_football_max_bet": "500",
    "game_football_bet_step": "1.0",
    "game_football_daily_limit": "0",
    "game_football_coeff_goal": "2.0",
    "game_football_coeff_miss": "1.33",
    "game_basketball_enabled": "1",
    "game_basketball_min_bet": "1.0",
    "game_basketball_max_bet": "500",
    "game_basketball_bet_step": "1.0",
    "game_basketball_daily_limit": "0",
    "game_basketball_coeff_clean": "4.0",
    "game_basketball_coeff_any": "2.0",
    "game_basketball_coeff_stuck": "4.0",
    "game_basketball_coeff_miss": "2.0",
    "game_bowling_enabled": "1",
    "game_bowling_min_bet": "1.0",
    "game_bowling_max_bet": "500",
    "game_bowling_bet_step": "1.0",
    "game_bowling_daily_limit": "0",
    "game_bowling_coeff_strike": "4.8",
    "game_bowling_coeff_partial": "1.2",
    "game_bowling_coeff_miss": "4.8",
    "game_dice_enabled": "1",
    "game_dice_min_bet": "1.0",
    "game_dice_max_bet": "500",
    "game_dice_bet_step": "1.0",
    "game_dice_daily_limit": "0",
    "game_dice_coeff": "1.6",
    "game_slots_enabled": "1",
    "game_slots_min_bet": "1.0",
    "game_slots_max_bet": "500",
    "game_slots_bet_step": "1.0",
    "game_slots_daily_limit": "0",
    "game_slots_coeff1": "42.2",
    "game_slots_coeff2": "3.0",
    "game_darts_enabled": "1",
    "game_darts_min_bet": "1.0",
    "game_darts_max_bet": "500",
    "game_darts_bet_step": "1.0",
    "game_darts_daily_limit": "0",
    "game_darts_coeff_bullseye": "3.0",
    "game_darts_coeff_bounce": "1.7",
    # Videos (file_id из Telegram, задаётся через админку)
    "wheel_max_bet": "500",
    "wheel_video_50x": "",
    "wheel_video_01x": "",
    "case_1_video": "",
    "case_3_video": "",
    "case_5_video": "",
    # Mines
    "mines_enabled": "1",
    "mines_min_bet": "1",
    "mines_max_bet": "500",
    "mines_house_edge": "0.20",
    "mines_house_edge_punish": "0.45",
    "mines_max_coeff": "10",
    "mines_total_bet": "0",
    "mines_total_payout": "0",
    # Tower
    "tower_enabled": "1",
    "tower_levels": "8",
    "tower_min_bet": "1",
    "tower_max_bet": "500",
    "tower_coeff_0": "1.05",
    "tower_coeff_1": "1.20",
    "tower_coeff_2": "1.40",
    "tower_coeff_3": "1.65",
    "tower_coeff_4": "1.95",
    "tower_coeff_5": "2.30",
    "tower_coeff_6": "2.70",
    "tower_coeff_7": "3.20",
    "tower_total_bet": "0",
    "tower_total_payout": "0",
    # Auction
    "auction_enabled": "1",
    "auction_commission": "0.20",
    # Group-chat feature
    "chat_min_members": "250",
    "chat_bonus_commission": "0.07",
    "chat_promo_reward": "0.3",
    "chat_promo_reward_vip": "0.5",
    "chat_promo_min_messages": "500",
    # Chat games
    "roulette_enabled": "1",
    "roulette_min_bet": "1.0",
    "roulette_max_bet": "500",
    "roulette_coeff_red_black": "2.2",
    "roulette_coeff_green": "8.8",
    "roulette_total_bet": "0",
    "roulette_total_payout": "0",
    "chat_tower_enabled": "1",
    "chat_tower_min_bet": "1.0",
    "chat_tower_max_bet": "500",
    "chat_tower_levels": "8",
    "chat_tower_coeff_0": "1.05",
    "chat_tower_coeff_1": "1.20",
    "chat_tower_coeff_2": "1.40",
    "chat_tower_coeff_3": "1.65",
    "chat_tower_coeff_4": "1.95",
    "chat_tower_coeff_5": "2.30",
    "chat_tower_coeff_6": "2.70",
    "chat_tower_coeff_7": "3.20",
    "chat_tower_total_bet": "0",
    "chat_tower_total_payout": "0",
    "maze_enabled": "1",
    "maze_min_bet": "1.0",
    "maze_max_bet": "500",
    "maze_house_edge": "0.15",
    "maze_max_coeff": "10.0",
    "maze_total_bet": "0",
    "maze_total_payout": "0",
    "doors_enabled": "1",
    "doors_min_bet": "1.0",
    "doors_max_bet": "500",
    "door_coeff_1": "1.05",
    "door_coeff_2": "1.15",
    "door_coeff_3": "1.30",
    "door_coeff_4": "1.50",
    "door_coeff_5": "1.75",
    "door_coeff_6": "2.10",
    "door_coeff_7": "2.50",
    "door_coeff_8": "3.00",
    "door_coeff_9": "3.80",
    "door_coeff_10": "5.00",
    "doors_total_bet": "0",
    "doors_total_payout": "0",
    # Sponsor wall
    "sponsor_max_channels": "10",
    # PiarFlow
    "piarflow_max_sponsors": "100",
    # Wheel/Cases stats
    "wheel_total_bet": "0",
    "wheel_total_payout": "0",
    "case_1_total_bet": "0",
    "case_1_total_payout": "0",
    "case_3_total_bet": "0",
    "case_3_total_payout": "0",
    "case_5_total_bet": "0",
    "case_5_total_payout": "0",
    # Random button (free daily spin)
    "random_cooldown_hours": "24",
    # Owner-authored custom chat broadcast — a free feature; only the
    # shortest interval an owner is allowed to set is configurable.
    "broadcast_min_interval_seconds": "300",
    # ToS / privacy policy links (Telegraph pages)
    "tos_user_agreement_url": "https://telegra.ph/Polzovatelskoe-soglashenie-EvolisStars-08-03-2",
    "tos_privacy_policy_url": "https://telegra.ph/Politika-konfidencialnosti-EvolisStars-08-03",
    # VC withdrawal -- a second withdrawal currency alongside Telegram
    # Stars. Tiered by requested VC amount (see bot/handlers/vc_withdraw.py
    # for the boundaries: 10k-25k/25k-50k/50k-100k/100k-300k/300k-500k),
    # each tier its own admin-editable "1 RP⭐️ = N VC" rate -- bigger
    # withdrawals convert at a better rate.
    "withdraw_vc_enabled": "1",
    "vc_min_withdrawal": "10000",
    "vc_max_withdrawal": "500000",
    "vc_rate_tier1": "1000",
    "vc_rate_tier2": "1250",
    "vc_rate_tier3": "1500",
    "vc_rate_tier4": "2000",
    "vc_rate_tier5": "2500",
    # Mandatory-subscription gate shown right before a VC request is
    # submitted (not the general sponsor wall) -- empty disables the gate.
    "vc_mandatory_channel": "https://t.me/VirusikChat",
    # Crypto withdrawal -- a third withdrawal currency alongside Telegram
    # Stars and VC. One flat $-per-RP⭐️ anchor for both sub-methods (see
    # bot/handlers/crypto_withdraw.py): "usdt_send" pays out in $ directly
    # (via the @send bot, to the user's Telegram username), "ton" converts
    # that same $ value into TON at the live rate (bot/services/ton_price.py).
    "withdraw_crypto_enabled": "1",
    "crypto_min_withdrawal": "50",
    "crypto_rp_usd_rate": "0.005",
}


class SettingsRepository(BaseRepository):
    async def get(self, key: str, default: str = "") -> str:
        row = await self.session.get(BotSettings, key)
        if row:
            return row.value
        return DEFAULT_SETTINGS.get(key, default)

    async def get_float(self, key: str, default: float = 0.0) -> float:
        val = await self.get(key, str(default))
        try:
            number = float(val)
            return number if math.isfinite(number) else default
        except (ValueError, TypeError):
            return default

    async def get_int(self, key: str, default: int = 0) -> int:
        val = await self.get(key, str(default))
        try:
            number = float(val)
            return int(number) if math.isfinite(number) else default
        except (ValueError, TypeError, OverflowError):
            return default

    async def get_bool(self, key: str, default: bool = True) -> bool:
        val = await self.get(key, "1" if default else "0")
        return val.strip().lower() in {"1", "true", "yes", "on"}

    async def get_prefixed(self, prefix: str) -> dict[str, str]:
        """All key/value pairs whose key starts with `prefix`, in one query --
        for callers that would otherwise issue one get() per item in a loop
        (e.g. checking a per-item "recently skipped" flag for every task in
        a list)."""
        result = await self.session.execute(
            select(BotSettings).where(BotSettings.key.startswith(prefix))
        )
        return {row.key: row.value for row in result.scalars().all()}

    async def set(self, key: str, value: str) -> None:
        row = await self.session.get(BotSettings, key)
        if row:
            row.value = value
        else:
            self.session.add(BotSettings(key=key, value=value))
        await self.session.commit()

    async def stage(self, key: str, value: str) -> None:
        row = await self.session.get(BotSettings, key)
        if row:
            row.value = value
        else:
            self.session.add(BotSettings(key=key, value=value))
        await self.session.flush()

    async def add_float(self, key: str, delta: float) -> None:
        result = await self.session.execute(
            update(BotSettings)
            .where(BotSettings.key == key)
            .values(
                value=cast(
                    func.round(cast(BotSettings.value, Float) + delta, 4),
                    String,
                )
            )
            .execution_options(synchronize_session=False)
        )
        if result.rowcount == 0:
            self.session.add(BotSettings(key=key, value=str(round(delta, 4))))

    async def seed_defaults(self) -> None:
        for key, value in DEFAULT_SETTINGS.items():
            existing = await self.session.get(BotSettings, key)
            if existing is None:
                self.session.add(BotSettings(key=key, value=value))
        await self.session.commit()
