import unittest
from unittest.mock import patch

from bot.services.piarflow import check_sponsors


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


if __name__ == "__main__":
    unittest.main()
