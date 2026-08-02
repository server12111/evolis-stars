import math

from sqlalchemy import Float, String, cast, func, update

from bot.database.models import BotSettings
from bot.database.repositories.base import BaseRepository

DEFAULT_SETTINGS: dict[str, str] = {
    "referral_reward": "4",
    "min_sponsors_for_reward": "3",
    "referral_bonus_10": "0.1",
    "referral_bonus_25": "0.3",
    "referral_bonus_30": "0.4",
    "referral_bonus_50": "0.7",
    "referral_bonus_55": "0.8",
    "referral_bonus_60": "0.9",
    "referral_bonus_70": "1.0",
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
    "game_football_coeff_goal": "1.5",
    "game_football_coeff_miss": "2.2",
    "game_basketball_enabled": "1",
    "game_basketball_min_bet": "1.0",
    "game_basketball_bet_step": "1.0",
    "game_basketball_daily_limit": "0",
    "game_basketball_coeff_clean": "4.0",
    "game_basketball_coeff_any": "2.2",
    "game_basketball_coeff_stuck": "4.0",
    "game_basketball_coeff_miss": "1.5",
    "game_bowling_enabled": "1",
    "game_bowling_min_bet": "1.0",
    "game_bowling_bet_step": "1.0",
    "game_bowling_daily_limit": "0",
    "game_bowling_coeff_strike": "5.0",
    "game_bowling_coeff_partial": "2.0",
    "game_bowling_coeff_miss": "4.0",
    "game_dice_enabled": "1",
    "game_dice_min_bet": "1.0",
    "game_dice_bet_step": "1.0",
    "game_dice_daily_limit": "0",
    "game_dice_coeff": "1.9",
    "game_slots_enabled": "1",
    "game_slots_min_bet": "1.0",
    "game_slots_bet_step": "1.0",
    "game_slots_daily_limit": "0",
    "game_slots_coeff1": "10.0",
    "game_slots_coeff2": "2.0",
    "game_darts_enabled": "1",
    "game_darts_min_bet": "1.0",
    "game_darts_bet_step": "1.0",
    "game_darts_daily_limit": "0",
    "game_darts_coeff_bullseye": "5.0",
    "game_darts_coeff_bounce": "5.0",
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
    "mines_house_edge_punish": "0.20",
    "mines_max_coeff": "10",
    "mines_total_bet": "0",
    "mines_total_payout": "0",
    # Tower — 20% house edge: coeff_k = 0.80 * 1.5^k
    "tower_enabled": "1",
    "tower_levels": "8",
    "tower_min_bet": "1",
    "tower_coeff_0": "1.20",
    "tower_coeff_1": "1.50",
    "tower_coeff_2": "1.90",
    "tower_coeff_3": "2.40",
    "tower_coeff_4": "3.00",
    "tower_coeff_5": "3.75",
    "tower_coeff_6": "4.75",
    "tower_coeff_7": "6.00",
    # Auction
    "auction_enabled": "1",
    "auction_commission": "0.20",
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
