import unittest

from bot.services.chat_games import (
    CHAT_TOWER_DEFAULT_COEFFS,
    DOORS_SAFE_PER_LEVEL,
    DOORS_SAFE_PER_LEVEL_PUNISH,
    ROULETTE_CUBES,
    ROULETTE_CUBES_PUNISH,
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

    def test_coeff_table_caps_at_20x_from_level_5(self) -> None:
        levels_5_to_10 = [round(v, 2) for v in [20.0, 20.0, 20.0, 20.0, 20.0, 20.0]]
        self.assertEqual(levels_5_to_10, [20.0] * 6)


class MazeCoeffTests(unittest.TestCase):
    def test_coeff_grows_monotonically_with_steps(self) -> None:
        coeffs = [maze_base_coeff(step, house_edge=0.2, max_coeff=10.0) for step in range(1, 6)]
        self.assertEqual(coeffs, sorted(coeffs))

    def test_step_zero_is_a_push(self) -> None:
        self.assertEqual(maze_base_coeff(0, house_edge=0.2, max_coeff=10.0), 1.0)


class TowerCoeffTableTests(unittest.TestCase):
    def test_flat_twenty_percent_edge_at_every_level(self) -> None:
        # survival is 2/3 per level, so fair(k) = (3/2)**(k+1); coeff should
        # sit at fair(k) * 0.8 (20% house edge) at every level, not just some.
        for level, coeff in enumerate(CHAT_TOWER_DEFAULT_COEFFS):
            fair = (3 / 2) ** (level + 1)
            self.assertAlmostEqual(coeff, fair * 0.8, places=2)

    def test_coeffs_strictly_increase_with_level(self) -> None:
        self.assertEqual(CHAT_TOWER_DEFAULT_COEFFS, sorted(CHAT_TOWER_DEFAULT_COEFFS))


if __name__ == "__main__":
    unittest.main()
