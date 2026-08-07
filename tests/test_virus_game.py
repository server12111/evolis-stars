import unittest
from decimal import Decimal
from unittest.mock import patch

from bot.services.virus_game import (
    AMMO_SUCCESS_CHANCE,
    HERBS_SUCCESS_CHANCE,
    INFECT_CHANCE,
    INTERFERENCE_CHANCE,
    VIRUS_TYPES,
    infection_payout,
    roll_ammo_success,
    roll_herbs_success,
    roll_infect_success,
    roll_interference,
    roll_virus_type,
)


class RollHelpersTests(unittest.TestCase):
    def test_infect_roll_boundary(self) -> None:
        with patch("bot.services.virus_game.random.random", return_value=INFECT_CHANCE - 0.001):
            self.assertTrue(roll_infect_success())
        with patch("bot.services.virus_game.random.random", return_value=INFECT_CHANCE):
            self.assertFalse(roll_infect_success())

    def test_ammo_roll_boundary(self) -> None:
        with patch("bot.services.virus_game.random.random", return_value=AMMO_SUCCESS_CHANCE - 0.001):
            self.assertTrue(roll_ammo_success())
        with patch("bot.services.virus_game.random.random", return_value=AMMO_SUCCESS_CHANCE):
            self.assertFalse(roll_ammo_success())

    def test_herbs_roll_boundary(self) -> None:
        with patch("bot.services.virus_game.random.random", return_value=HERBS_SUCCESS_CHANCE - 0.001):
            self.assertTrue(roll_herbs_success())
        with patch("bot.services.virus_game.random.random", return_value=HERBS_SUCCESS_CHANCE):
            self.assertFalse(roll_herbs_success())

    def test_interference_roll_boundary(self) -> None:
        with patch("bot.services.virus_game.random.random", return_value=INTERFERENCE_CHANCE - 0.001):
            self.assertTrue(roll_interference())
        with patch("bot.services.virus_game.random.random", return_value=INTERFERENCE_CHANCE):
            self.assertFalse(roll_interference())

    def test_virus_type_weights_sum_to_one(self) -> None:
        total = sum(v.chance for v in VIRUS_TYPES.values())
        self.assertAlmostEqual(total, 1.0)

    def test_roll_virus_type_respects_weights(self) -> None:
        with patch("bot.services.virus_game.random.choices", return_value=["dangerous"]) as choices:
            result = roll_virus_type()
        self.assertEqual(result, "dangerous")
        keys, weights = choices.call_args.args[0], choices.call_args.kwargs["weights"]
        self.assertEqual(keys, list(VIRUS_TYPES.keys()))
        self.assertEqual(weights, [v.chance for v in VIRUS_TYPES.values()])

    def test_infection_payout_uses_type_multiplier(self) -> None:
        self.assertEqual(infection_payout(Decimal("10"), "light"), Decimal("12.00"))
        self.assertEqual(infection_payout(Decimal("10"), "normal"), Decimal("15.00"))
        self.assertEqual(infection_payout(Decimal("10"), "dangerous"), Decimal("17.00"))


if __name__ == "__main__":
    unittest.main()
