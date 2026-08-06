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

    def test_single_wave_never_unlocks_a_second_wave(self) -> None:
        current = user()
        evaluate_waves(
            current,
            tgrass_result=offers("tg", 12),
            botohub_result=[],
        )
        # Only the first 6 offers are ever frozen initially — MAX_WAVES
        # caps the sponsor wall at one wave, so there's never a genuine
        # "wave 2" — but a resolved slot in wave 1 can still be topped up
        # from the rest of the same 12-offer pool (see the top-up tests
        # below), it just never spills into a second wave/step.
        self.assertIsNone(current.sponsor_wave_two)

        still_first = evaluate_waves(
            current,
            tgrass_result=offers("tg", 1, start=2) + offers("tg", 6, start=6),
            botohub_result=[],
        )
        self.assertEqual((still_first.status, still_first.wave, still_first.total_waves), ("pending", 1, 1))
        self.assertIsNone(current.sponsor_wave_two)

        # Provider reports nothing left at all -> genuinely exhausted.
        completed = evaluate_waves(
            current,
            tgrass_result=[],
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

    def test_wave_tops_up_with_a_replacement_when_a_sponsor_resolves(self) -> None:
        """A sponsor dropping out of `remaining` (confirmed subscribed)
        must not just shrink the wave -- a fresh, not-yet-shown candidate
        from the provider's current batch should fill the gap."""
        current = user()
        evaluate_waves(
            current,
            tgrass_result=offers("tg", 4),  # tg0-tg3, under wave_size(6)
            botohub_result=[],
        )
        self.assertEqual(len(json.loads(current.sponsor_wave_one)), 4)

        # tg0 no longer reported by the provider (user subscribed to it);
        # tg4 is a brand-new candidate that wasn't available before.
        state = evaluate_waves(
            current,
            tgrass_result=offers("tg", 3, start=1) + offers("tg", 1, start=4),
            botohub_result=[],
        )

        self.assertEqual(state.status, "pending")
        shown_urls = {item["url"] for item in state.items or []}
        self.assertEqual(shown_urls, {"https://t.me/tg1", "https://t.me/tg2", "https://t.me/tg3", "https://t.me/tg4"})
        self.assertNotIn("https://t.me/tg0", shown_urls)

        # tg0's resolved history must survive in storage -- referral-reward
        # sponsor counting reads sponsor_wave_one/two directly and needs
        # every sponsor ever offered, not just the ones still pending.
        saved_urls = {item["url"] for item in json.loads(current.sponsor_wave_one)}
        self.assertIn("https://t.me/tg0", saved_urls)
        self.assertEqual(len(saved_urls), 5)

    def test_top_up_never_duplicates_a_sponsor_still_pending_in_the_wave(self) -> None:
        """The new-candidate pool for top-up must exclude anything already
        in `saved` -- including an item that's simultaneously still
        pending (still reported by its provider) -- or it would show
        twice."""
        current = user()
        evaluate_waves(
            current,
            tgrass_result=offers("tg", 2),  # tg0, tg1
            botohub_result=[],
        )
        # tg0 resolves; tg1 is still pending and still reported as-is (no
        # brand-new candidate available this round).
        state = evaluate_waves(
            current,
            tgrass_result=offers("tg", 1, start=1),
            botohub_result=[],
        )
        shown_urls = [item["url"] for item in state.items or []]
        self.assertEqual(shown_urls, ["https://t.me/tg1"])

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
