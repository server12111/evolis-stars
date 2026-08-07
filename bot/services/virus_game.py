import random
from datetime import timedelta
from decimal import Decimal
from typing import NamedTuple

COOLDOWN = timedelta(hours=24)

INFECT_CHANCE = 0.40

AMMO_COST = Decimal("5")
AMMO_SUCCESS_CHANCE = 0.30

MEDICINE_COST = Decimal("10")
HERBS_COST = Decimal("1")
HERBS_SUCCESS_CHANCE = 0.35

INTERFERENCE_CHANCE = 0.05
INTERFERENCE_TEXT = "🦠 Вирус помешал вам... Попробуйте ещё раз."


class VirusType(NamedTuple):
    label: str
    chance: float
    payout_mult: Decimal
    hourly_income: Decimal


VIRUS_TYPES: dict[str, VirusType] = {
    "light": VirusType("лёгкий", 0.60, Decimal("1.2"), Decimal("0.05")),
    "normal": VirusType("обычный", 0.35, Decimal("1.5"), Decimal("0.10")),
    "dangerous": VirusType("опасный", 0.05, Decimal("1.7"), Decimal("0.40")),
}


def roll_infect_success() -> bool:
    return random.random() < INFECT_CHANCE


def roll_ammo_success() -> bool:
    return random.random() < AMMO_SUCCESS_CHANCE


def roll_herbs_success() -> bool:
    return random.random() < HERBS_SUCCESS_CHANCE


def roll_interference() -> bool:
    return random.random() < INTERFERENCE_CHANCE


def roll_virus_type() -> str:
    keys = list(VIRUS_TYPES.keys())
    weights = [VIRUS_TYPES[key].chance for key in keys]
    return random.choices(keys, weights=weights, k=1)[0]


def infection_payout(stake: Decimal, virus_type: str) -> Decimal:
    return (stake * VIRUS_TYPES[virus_type].payout_mult).quantize(Decimal("0.01"))
