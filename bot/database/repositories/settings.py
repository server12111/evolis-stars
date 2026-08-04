import math

from sqlalchemy import Float, String, cast, func, update

from bot.database.models import BotSettings
from bot.database.repositories.base import BaseRepository

DEFAULT_SETTINGS: dict[str, str] = {
    "referral_reward": "4",
    "referral_reward_premium": "6",
    "min_sponsors_for_reward": "3",
    "referral_bonus_10": "0.05",
    "referral_bonus_15": "0.1",
    "referral_bonus_20": "0.15",
    "referral_bonus_25": "0.25",
    "referral_bonus_30": "0.5",
    "referral_bonus_35": "0.75",
    "referral_bonus_40": "0.8",
    "referral_bonus_45": "0.9",
    "referral_bonus_50": "1",
    "referral_bonus_55": "1.1",
    "referral_bonus_60": "1.3",
    "referral_bonus_67": "1.5",
    "referral_bonus_70": "1.75",
    "referral_bonus_76": "1.8",
    "referral_bonus_80": "1.9",
    "referral_bonus_90": "2",
    "referral_bonus_100": "1",
    "bonus_min": "0.1",
    "bonus_max": "1.0",
    "bonus_enabled": "1",
    "withdraw_enabled": "1",
    "withdraw_min": "15",
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
    # game settings
    "game_football_enabled": "1",
    "game_football_min_bet": "1.0",
    "game_football_bet_step": "1.0",
    "game_football_daily_limit": "0",
    "game_football_coeff_goal": "2.0",
    "game_football_coeff_miss": "1.33",
    "game_basketball_enabled": "1",
    "game_basketball_min_bet": "1.0",
    "game_basketball_bet_step": "1.0",
    "game_basketball_daily_limit": "0",
    "game_basketball_coeff_clean": "4.0",
    "game_basketball_coeff_any": "2.0",
    "game_basketball_coeff_stuck": "4.0",
    "game_basketball_coeff_miss": "2.0",
    "game_bowling_enabled": "1",
    "game_bowling_min_bet": "1.0",
    "game_bowling_bet_step": "1.0",
    "game_bowling_daily_limit": "0",
    "game_bowling_coeff_strike": "4.8",
    "game_bowling_coeff_partial": "1.2",
    "game_bowling_coeff_miss": "4.8",
    "game_dice_enabled": "1",
    "game_dice_min_bet": "1.0",
    "game_dice_bet_step": "1.0",
    "game_dice_daily_limit": "0",
    "game_dice_coeff": "1.6",
    "game_slots_enabled": "1",
    "game_slots_min_bet": "1.0",
    "game_slots_bet_step": "1.0",
    "game_slots_daily_limit": "0",
    "game_slots_coeff1": "42.2",
    "game_slots_coeff2": "3.0",
    "game_darts_enabled": "1",
    "game_darts_min_bet": "1.0",
    "game_darts_bet_step": "1.0",
    "game_darts_daily_limit": "0",
    "game_darts_coeff_bullseye": "4.8",
    "game_darts_coeff_bounce": "4.8",
    # Videos (file_id из Telegram, задаётся через админку)
    "wheel_video_50x": "",
    "wheel_video_01x": "",
    "case_1_video": "",
    "case_3_video": "",
    "case_5_video": "",
    # Mines
    "mines_enabled": "1",
    "mines_min_bet": "1",
    "mines_house_edge": "0.20",
    "mines_house_edge_punish": "0.45",
    "mines_max_coeff": "10",
    "mines_total_bet": "0",
    "mines_total_payout": "0",
    # Tower — 20% house edge at every level: coeff_k = 0.80 * 1.5^k
    "tower_enabled": "1",
    "tower_levels": "8",
    "tower_min_bet": "1",
    "tower_coeff_0": "1.20",
    "tower_coeff_1": "1.80",
    "tower_coeff_2": "2.70",
    "tower_coeff_3": "4.05",
    "tower_coeff_4": "6.08",
    "tower_coeff_5": "9.11",
    "tower_coeff_6": "13.67",
    "tower_coeff_7": "20.50",
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
    "chat_promo_min_days": "2",
    "chat_promo_min_messages": "500",
    # Chat games
    "roulette_enabled": "1",
    "roulette_min_bet": "1.0",
    "roulette_coeff_red_black": "2.2",
    "roulette_coeff_green": "8.8",
    "roulette_total_bet": "0",
    "roulette_total_payout": "0",
    "chat_tower_enabled": "1",
    "chat_tower_min_bet": "1.0",
    "chat_tower_levels": "8",
    "chat_tower_coeff_0": "1.20",
    "chat_tower_coeff_1": "1.80",
    "chat_tower_coeff_2": "2.70",
    "chat_tower_coeff_3": "4.05",
    "chat_tower_coeff_4": "6.08",
    "chat_tower_coeff_5": "9.11",
    "chat_tower_coeff_6": "13.67",
    "chat_tower_coeff_7": "20.50",
    "chat_tower_total_bet": "0",
    "chat_tower_total_payout": "0",
    "maze_enabled": "1",
    "maze_min_bet": "1.0",
    "maze_house_edge": "0.15",
    "maze_max_coeff": "10.0",
    "maze_total_bet": "0",
    "maze_total_payout": "0",
    "doors_enabled": "1",
    "doors_min_bet": "1.0",
    "door_coeff_1": "1.6",
    "door_coeff_2": "3.2",
    "door_coeff_3": "6.4",
    "door_coeff_4": "12.8",
    "door_coeff_5": "20.0",
    "door_coeff_6": "20.0",
    "door_coeff_7": "20.0",
    "door_coeff_8": "20.0",
    "door_coeff_9": "20.0",
    "door_coeff_10": "20.0",
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
    "random_stake": "3.0",
    "random_cooldown_hours": "24",
    # ToS / privacy policy links (Telegraph pages)
    "tos_user_agreement_url": "https://telegra.ph/Polzovatelskoe-soglashenie-EvolisStars-08-03-2",
    "tos_privacy_policy_url": "https://telegra.ph/Politika-konfidencialnosti-EvolisStars-08-03",
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
