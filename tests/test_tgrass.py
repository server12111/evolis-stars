import unittest
from unittest.mock import patch

from bot.services.tgrass import check_tgrass


class _Response:
    def __init__(self, status: int, payload) -> None:
        self.status = status
        self.payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def json(self, **_kwargs):
        return self.payload


class _Session:
    def __init__(self, response: _Response) -> None:
        self.response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    def post(self, *_args, **_kwargs):
        return self.response


def _patched(response: _Response):
    return patch("bot.services.tgrass.aiohttp.ClientSession", return_value=_Session(response))


class CheckTgrassTests(unittest.IsolatedAsyncioTestCase):
    async def test_no_code_returns_empty_list_without_a_call(self) -> None:
        self.assertEqual(await check_tgrass(123, ""), [])

    async def test_status_ok_returns_empty_list(self) -> None:
        with _patched(_Response(200, {"status": "ok", "offers": [{"link": "https://t.me/x"}]})):
            result = await check_tgrass(123, "code")
        self.assertEqual(result, [])

    async def test_status_no_offers_returns_empty_list(self) -> None:
        with _patched(_Response(200, {"status": "no_offers"})):
            result = await check_tgrass(123, "code")
        self.assertEqual(result, [])

    async def test_not_ok_returns_only_unsubscribed_offers(self) -> None:
        with _patched(_Response(200, {
            "status": "not_ok",
            "offers": [
                {"name": "Sub", "link": "https://t.me/sub", "subscribed": True},
                {"name": "Unsub", "link": "https://t.me/unsub", "subscribed": False},
            ],
        })):
            result = await check_tgrass(123, "code")
        self.assertEqual(result, [{"name": "Unsub", "url": "https://t.me/unsub"}])

    async def test_missing_subscribed_field_is_treated_as_unsubscribed(self) -> None:
        """subscribed absent/None must be read as NOT subscribed (fail
        closed) -- only an explicit True clears an offer."""
        with _patched(_Response(200, {
            "status": "not_ok",
            "offers": [{"name": "X", "link": "https://t.me/x"}],
        })):
            result = await check_tgrass(123, "code")
        self.assertEqual(result, [{"name": "X", "url": "https://t.me/x"}])

    async def test_offer_missing_link_is_dropped(self) -> None:
        with _patched(_Response(200, {
            "status": "not_ok",
            "offers": [{"name": "No link"}],
        })):
            result = await check_tgrass(123, "code")
        self.assertEqual(result, [])

    async def test_malformed_top_level_response_returns_none(self) -> None:
        with _patched(_Response(200, ["not", "a", "dict"])):
            result = await check_tgrass(123, "code")
        self.assertIsNone(result)

    async def test_offers_explicitly_null_returns_none_not_a_crash(self) -> None:
        with _patched(_Response(200, {"status": "not_ok", "offers": None})):
            result = await check_tgrass(123, "code")
        self.assertIsNone(result)

    async def test_offer_entry_that_is_not_a_dict_is_dropped(self) -> None:
        with _patched(_Response(200, {
            "status": "not_ok",
            "offers": ["not a dict", {"name": "X", "link": "https://t.me/x"}],
        })):
            result = await check_tgrass(123, "code")
        self.assertEqual(result, [{"name": "X", "url": "https://t.me/x"}])

    async def test_offers_limit_forwarded_only_when_positive(self) -> None:
        captured = {}

        class _CapturingSession(_Session):
            def post(self, *_args, **kwargs):
                captured["json"] = kwargs.get("json")
                return self.response

        with patch(
            "bot.services.tgrass.aiohttp.ClientSession",
            return_value=_CapturingSession(_Response(200, {"status": "no_offers"})),
        ):
            await check_tgrass(123, "code", max_offers=7)
        self.assertEqual(captured["json"]["offers_limit"], 7)

    async def test_offers_limit_omitted_when_zero(self) -> None:
        captured = {}

        class _CapturingSession(_Session):
            def post(self, *_args, **kwargs):
                captured["json"] = kwargs.get("json")
                return self.response

        with patch(
            "bot.services.tgrass.aiohttp.ClientSession",
            return_value=_CapturingSession(_Response(200, {"status": "no_offers"})),
        ):
            await check_tgrass(123, "code")
        self.assertNotIn("offers_limit", captured["json"])

    async def test_http_error_returns_none(self) -> None:
        with _patched(_Response(500, {})):
            self.assertIsNone(await check_tgrass(123, "code"))


if __name__ == "__main__":
    unittest.main()
