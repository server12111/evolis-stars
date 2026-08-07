import unittest
from unittest.mock import patch

from bot.services.piarflow import check_sponsors, get_sponsors


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


class PiarFlowSecurityTests(unittest.IsolatedAsyncioTestCase):
    async def _check(self, response: _Response) -> bool:
        with patch(
            "bot.services.piarflow.aiohttp.ClientSession",
            return_value=_Session(response),
        ):
            return await check_sponsors("key", 123, ["https://t.me/test"])

    async def test_http_error_never_counts_as_subscription(self) -> None:
        self.assertFalse(await self._check(_Response(401, {"error": "unauthorized"})))

    async def test_empty_status_list_never_counts_as_subscription(self) -> None:
        self.assertFalse(await self._check(_Response(200, {"sponsors": []})))

    async def test_explicit_subscribed_status_is_accepted(self) -> None:
        self.assertTrue(
            await self._check(
                _Response(
                    200,
                    {
                        "sponsors": [{
                            "link": "https://t.me/test",
                            "status": "subscribed",
                        }]
                    },
                )
            )
        )

    async def test_status_for_another_link_is_not_accepted(self) -> None:
        self.assertFalse(
            await self._check(
                _Response(
                    200,
                    {
                        "sponsors": [{
                            "link": "https://t.me/another",
                            "status": "subscribed",
                        }]
                    },
                )
            )
        )


class GetSponsorsHttpStatusTests(unittest.IsolatedAsyncioTestCase):
    """Regression: PiarFlow's docs list only 400/401/404/429/500 for
    /sponsors, but it's confirmed live to also return 403 {"status":
    "error", "message": "User is not in this bot"} for specific real
    user_ids that simply have nothing available -- the same "no tasks"
    meaning as the documented 404, not an auth/key failure. get_sponsors
    must treat it identically to 404 (empty list), not lump it into the
    generic >=400 "None" (hard failure) bucket -- returning None makes
    _show_pf_task show the dead-end "exhausted" screen instead of falling
    back to FlyerHub/linkni, which is what made PiarFlow tasks look like
    they'd vanished for those users."""

    async def _get(self, response: _Response) -> list | None:
        with patch(
            "bot.services.piarflow.aiohttp.ClientSession",
            return_value=_Session(response),
        ):
            return await get_sponsors("key", 123, -100, 10)

    async def test_403_user_not_in_bot_is_treated_as_no_sponsors(self) -> None:
        result = await self._get(
            _Response(403, {"status": "error", "message": "User is not in this bot"})
        )
        self.assertEqual(result, [])

    async def test_404_still_treated_as_no_sponsors(self) -> None:
        self.assertEqual(await self._get(_Response(404, {})), [])

    async def test_401_invalid_key_still_treated_as_hard_failure(self) -> None:
        # A genuine auth/config problem must still surface as None (the
        # "PiarFlow unavailable" screen) -- only 403's specific "no
        # sponsors for this user" meaning gets the 404 treatment.
        self.assertIsNone(await self._get(_Response(401, {"error": "unauthorized"})))


if __name__ == "__main__":
    unittest.main()
