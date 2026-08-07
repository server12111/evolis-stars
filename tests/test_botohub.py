import unittest
from unittest.mock import patch

from bot.services.botohub import check_botohub


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
    return patch("bot.services.botohub.aiohttp.ClientSession", return_value=_Session(response))


class CheckBotohubTests(unittest.IsolatedAsyncioTestCase):
    async def test_no_key_returns_empty_list_without_a_call(self) -> None:
        self.assertEqual(await check_botohub(123, ""), [])

    async def test_success_maps_urls_with_placeholder_names(self) -> None:
        with _patched(_Response(200, {"tasks": ["https://t.me/sponsor1", "https://t.me/sponsor2"]})):
            result = await check_botohub(123, "key")
        self.assertEqual(result, [
            {"name": "Канал", "url": "https://t.me/sponsor1"},
            {"name": "Канал", "url": "https://t.me/sponsor2"},
        ])

    async def test_completed_true_returns_empty_list(self) -> None:
        with _patched(_Response(200, {"completed": True, "tasks": ["https://t.me/x"]})):
            result = await check_botohub(123, "key")
        self.assertEqual(result, [])

    async def test_skip_true_returns_empty_list(self) -> None:
        with _patched(_Response(200, {"skip": True, "tasks": ["https://t.me/x"]})):
            result = await check_botohub(123, "key")
        self.assertEqual(result, [])

    async def test_falsy_urls_in_tasks_are_dropped(self) -> None:
        with _patched(_Response(200, {"tasks": ["https://t.me/ok", "", None]})):
            result = await check_botohub(123, "key")
        self.assertEqual(result, [{"name": "Канал", "url": "https://t.me/ok"}])

    async def test_malformed_top_level_response_returns_none(self) -> None:
        """A response body that isn't even a JSON object must fail clean
        (None -> "unavailable" upstream), not crash on data.get(...)."""
        with _patched(_Response(200, ["not", "a", "dict"])):
            result = await check_botohub(123, "key")
        self.assertIsNone(result)

    async def test_tasks_explicitly_null_returns_none_not_a_crash(self) -> None:
        """`tasks: null` (present but not a list) must not blow up the
        `for url in tasks` loop with a TypeError."""
        with _patched(_Response(200, {"tasks": None})):
            result = await check_botohub(123, "key")
        self.assertIsNone(result)

    async def test_task_entry_that_is_not_a_string_is_dropped(self) -> None:
        with _patched(_Response(200, {"tasks": ["https://t.me/ok", {"unexpected": "dict"}]})):
            result = await check_botohub(123, "key")
        self.assertEqual(result, [{"name": "Канал", "url": "https://t.me/ok"}])

    async def test_http_error_returns_none(self) -> None:
        with _patched(_Response(500, {})):
            self.assertIsNone(await check_botohub(123, "key"))

    async def test_401_returns_none(self) -> None:
        with _patched(_Response(401, {"error": "Unauthorized"})):
            self.assertIsNone(await check_botohub(123, "key"))


if __name__ == "__main__":
    unittest.main()
