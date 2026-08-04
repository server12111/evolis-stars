import unittest

from bot.services.chat_games import (
    CHAT_TOWER_DEFAULT_COEFFS,
    DOORS_SAFE_PER_LEVEL,
    DOORS_SAFE_PER_LEVEL_PUNISH,
    ROULETTE_CUBES,
    ROULETTE_CUBES_PUNISH,
    _DOORS_DEFAULT_COEFFS,
    doors_generate_safe_positions,
    maze_base_coeff,
    roulette_spin,
)


class RoulettePunishModeTests(unittest.TestCase):
    def test_normal_pool_can_land_on_every_color(self) -> None:
        seen = {roulette_spin(punish=False) for _ in range(500)}
        self.assertEqual(seen, {"white", "black", "red", "green"})

    def test_punish_pool_never_lands_green(self) -> None:
        seen = {roulette_spin(punish=True) for _ in range(500)}
        self.assertNotIn("green", seen)

    def test_punish_pool_shifts_weight_to_white(self) -> None:
        normal_white_weight = dict(ROULETTE_CUBES)["white"]
        punish_white_weight = dict(ROULETTE_CUBES_PUNISH)["white"]
        self.assertGreater(punish_white_weight, normal_white_weight)


class DoorsPunishModeTests(unittest.TestCase):
    def test_normal_mode_has_two_safe_doors(self) -> None:
        self.assertEqual(len(doors_generate_safe_positions(punish=False)), DOORS_SAFE_PER_LEVEL)

    def test_punish_mode_has_one_safe_door(self) -> None:
        self.assertEqual(len(doors_generate_safe_positions(punish=True)), DOORS_SAFE_PER_LEVEL_PUNISH)

    def test_coeff_table_matches_the_configured_values(self) -> None:
        self.assertEqual(
            _DOORS_DEFAULT_COEFFS,
            [1.05, 1.15, 1.30, 1.50, 1.75, 2.10, 2.50, 3.00, 3.80, 5.00],
        )

    def test_coeffs_strictly_increase_with_level(self) -> None:
        self.assertEqual(_DOORS_DEFAULT_COEFFS, sorted(_DOORS_DEFAULT_COEFFS))


class MazeCoeffTests(unittest.TestCase):
    def test_coeff_grows_monotonically_with_steps(self) -> None:
        coeffs = [maze_base_coeff(step, house_edge=0.2, max_coeff=10.0) for step in range(1, 6)]
        self.assertEqual(coeffs, sorted(coeffs))

    def test_step_zero_is_a_push(self) -> None:
        self.assertEqual(maze_base_coeff(0, house_edge=0.2, max_coeff=10.0), 1.0)


class TowerCoeffTableTests(unittest.TestCase):
    def test_coeff_table_matches_the_configured_values(self) -> None:
        self.assertEqual(
            CHAT_TOWER_DEFAULT_COEFFS,
            [1.05, 1.20, 1.40, 1.65, 1.95, 2.30, 2.70, 3.20],
        )

    def test_coeffs_strictly_increase_with_level(self) -> None:
        self.assertEqual(CHAT_TOWER_DEFAULT_COEFFS, sorted(CHAT_TOWER_DEFAULT_COEFFS))


if __name__ == "__main__":
    unittest.main()
