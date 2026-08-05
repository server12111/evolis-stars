import json
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from bot.services.sponsor_waves import (
    evaluate_waves,
    sponsor_wave_markup,
    sponsor_wave_text,
)


def offers(prefix: str, count: int, start: int = 0) -> list[dict]:
    return [
        {
            "name": f"Channel {index}",
            "url": f"https://t.me/{prefix}{index}",
        }
        for index in range(start, start + count)
    ]


def user() -> SimpleNamespace:
    return SimpleNamespace(
        sponsor_wave=0,
        sponsor_wave_one=None,
        sponsor_wave_two=None,
    )


@patch("bot.services.sponsor_waves.WAVE_SIZE", 6)
class SponsorWaveTests(unittest.TestCase):
    def test_one_wave_has_no_one_of_two_label(self) -> None:
        current = user()
        state = evaluate_waves(
            current,
            tgrass_result=offers("tg", 4),
            botohub_result=[],
        )

        self.assertEqual((state.status, state.wave, state.total_waves), ("pending", 1, 1))
        text = sponsor_wave_text(state.wave, state.total_waves)
        self.assertNotIn("Волна 1 из 2", text)
        self.assertNotIn("Волна 1", text)
        self.assertNotIn("волна", text.lower())

    def test_wave_is_limited_to_six_with_no_second_wave(self) -> None:
        current = user()
        state = evaluate_waves(
            current,
            tgrass_result=offers("tg", 20),
            botohub_result=[],
        )

        self.assertEqual((state.wave, state.total_waves), (1, 1))
        self.assertEqual(len(state.items or []), 6)
        self.assertEqual(len(json.loads(current.sponsor_wave_one)), 6)
        self.assertIsNone(current.sponsor_wave_two)
        buttons = sponsor_wave_markup(state.items or [])
        url_buttons = [
            button
            for row in buttons.inline_keyboard
            for button in row
            if button.url
        ]
        self.assertEqual(len(url_buttons), 6)

    def test_markup_includes_check_and_skip_buttons(self) -> None:
        buttons = sponsor_wave_markup(offers("tg", 2))
        callback_data = [
            button.callback_data
            for row in buttons.inline_keyboard
            for button in row
            if button.callback_data
        ]
        self.assertIn("sponsor_check", callback_data)
        self.assertIn("sponsor_skip", callback_data)

    def test_single_wave_completes_without_unlocking_a_second_wave(self) -> None:
        current = user()
        evaluate_waves(
            current,
            tgrass_result=offers("tg", 12),
            botohub_result=[],
        )
        # Only the first 6 offers are ever frozen — the rest never become a
        # second wave since MAX_WAVES caps the sponsor wall at one wave.
        self.assertIsNone(current.sponsor_wave_two)

        still_first = evaluate_waves(
            current,
            tgrass_result=offers("tg", 1, start=2) + offers("tg", 6, start=6),
            botohub_result=[],
        )
        self.assertEqual((still_first.status, still_first.wave), ("pending", 1))

        completed = evaluate_waves(
            current,
            tgrass_result=offers("tg", 6, start=6),
            botohub_result=[],
        )
        self.assertEqual((completed.status, current.sponsor_wave), ("complete", 3))

    def test_wave_is_reissued_after_new_offers_appear(self) -> None:
        current = user()
        evaluate_waves(
            current,
            tgrass_result=offers("tg", 12),
            botohub_result=[],
        )
        evaluate_waves(
            current,
            tgrass_result=offers("tg", 6, start=6),
            botohub_result=[],
        )
        completed = evaluate_waves(
            current,
            tgrass_result=[],
            botohub_result=[],
        )
        self.assertEqual((completed.status, current.sponsor_wave), ("complete", 3))

        new_offers = evaluate_waves(
            current,
            tgrass_result=offers("new", 12),
            botohub_result=[],
        )
        self.assertEqual((new_offers.status, current.sponsor_wave), ("pending", 1))

    def test_failure_of_current_provider_never_skips_wave(self) -> None:
        current = user()
        evaluate_waves(
            current,
            tgrass_result=offers("tg", 2),
            botohub_result=[],
        )
        state = evaluate_waves(
            current,
            tgrass_result=RuntimeError("offline"),
            botohub_result=[],
        )
        self.assertEqual((state.status, current.sponsor_wave), ("unavailable", 1))

    def test_initial_provider_failure_does_not_freeze_incomplete_waves(self) -> None:
        current = user()
        state = evaluate_waves(
            current,
            tgrass_result=offers("tg", 2),
            botohub_result=RuntimeError("offline"),
        )
        self.assertEqual(state.status, "unavailable")
        self.assertEqual(current.sponsor_wave, 0)
        self.assertIsNone(current.sponsor_wave_one)

    def test_failure_of_unrelated_provider_does_not_block_saved_wave(self) -> None:
        current = user()
        evaluate_waves(
            current,
            tgrass_result=offers("tg", 2),
            botohub_result=[],
        )
        state = evaluate_waves(
            current,
            tgrass_result=offers("tg", 2),
            botohub_result=RuntimeError("offline"),
        )
        self.assertEqual(state.status, "pending")

    def test_duplicate_url_from_integrations_is_shown_once(self) -> None:
        current = user()
        duplicate = [{"name": "Same", "url": "https://t.me/same/"}]
        state = evaluate_waves(
            current,
            tgrass_result=duplicate + offers("tg", 2),
            botohub_result=[{"name": "Same", "url": "https://t.me/same"}],
        )
        saved = json.loads(current.sponsor_wave_one)
        self.assertEqual(len(saved), 3)
        self.assertEqual(len(state.items or []), 3)

    def test_saved_progress_survives_a_restart(self) -> None:
        first_process = user()
        evaluate_waves(
            first_process,
            tgrass_result=offers("tg", 8),
            botohub_result=[],
        )
        restored = SimpleNamespace(
            sponsor_wave=first_process.sponsor_wave,
            sponsor_wave_one=first_process.sponsor_wave_one,
            sponsor_wave_two=first_process.sponsor_wave_two,
        )
        # Only tg0-tg5 were ever frozen into the (single) wave; tg4/tg5 are
        # still reported as outstanding by the provider after the restart.
        state = evaluate_waves(
            restored,
            tgrass_result=offers("tg", 2, start=4),
            botohub_result=[],
        )
        self.assertEqual((state.status, state.wave), ("pending", 1))
        self.assertEqual(
            {item["url"] for item in state.items or []},
            {"https://t.me/tg4", "https://t.me/tg5"},
        )


if __name__ == "__main__":
    unittest.main()
