import unittest

from bot.services.flyerhub import fh_task_to_sponsor_item


class FhTaskToSponsorItemTests(unittest.TestCase):
    def test_subscribe_channel_is_verifiable(self) -> None:
        item = fh_task_to_sponsor_item({
            "signature": "sig-1",
            "task": "subscribe channel",
            "links": ["https://t.me/sponsor1"],
            "name": "Sponsor",
        })
        self.assertEqual(item, {
            "name": "Sponsor",
            "url": "https://t.me/sponsor1",
            "ref": "sig-1",
        })

    def test_give_boost_is_marked_trust_only(self) -> None:
        # get_chat_member can confirm membership, not whether a boost was
        # given -- must never be independently "verified" via live lookup.
        item = fh_task_to_sponsor_item({
            "signature": "sig-2",
            "task": "give boost",
            "links": ["https://t.me/boost/sponsor1"],
            "name": "Sponsor",
        })
        self.assertEqual(item["kind"], "trust")

    def test_follow_link_is_marked_trust_only(self) -> None:
        item = fh_task_to_sponsor_item({
            "signature": "sig-3",
            "task": "follow link",
            "links": ["https://example.com/promo"],
            "name": "Promo",
        })
        self.assertEqual(item["kind"], "trust")

    def test_start_bot_is_marked_trust_only(self) -> None:
        item = fh_task_to_sponsor_item({
            "signature": "sig-4",
            "task": "start bot",
            "links": ["https://t.me/SomeSponsorBot"],
            "name": "Bot",
        })
        self.assertEqual(item["kind"], "trust")

    def test_missing_links_returns_none(self) -> None:
        self.assertIsNone(fh_task_to_sponsor_item({
            "signature": "sig-5",
            "task": "subscribe channel",
            "links": [],
            "name": "No link",
        }))

    def test_missing_signature_returns_none(self) -> None:
        self.assertIsNone(fh_task_to_sponsor_item({
            "task": "subscribe channel",
            "links": ["https://t.me/sponsor1"],
            "name": "No sig",
        }))

    def test_missing_name_defaults(self) -> None:
        item = fh_task_to_sponsor_item({
            "signature": "sig-6",
            "task": "subscribe channel",
            "links": ["https://t.me/sponsor1"],
            "name": None,
        })
        self.assertEqual(item["name"], "Спонсор")


if __name__ == "__main__":
    unittest.main()
