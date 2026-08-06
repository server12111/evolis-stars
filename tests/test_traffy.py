import unittest
from unittest.mock import patch

from bot.services.traffy import check_traffy_tasks, get_traffy_tasks


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
    return patch("bot.services.traffy.aiohttp.ClientSession", return_value=_Session(response))


class GetTraffyTasksTests(unittest.IsolatedAsyncioTestCase):
    async def test_no_key_returns_empty_list_without_a_call(self) -> None:
        self.assertEqual(await get_traffy_tasks("", 123), [])

    async def test_success_decorates_url_name_and_ref(self) -> None:
        with _patched(_Response(200, {
            "ok": True,
            "tasks": [{
                "assignment_id": "uuid-1",
                "title": "Sponsor Channel",
                "target_link": "https://t.me/sponsor1",
            }],
        })):
            result = await get_traffy_tasks("key", 123)
        self.assertEqual(result, [{
            "name": "Sponsor Channel",
            "url": "https://t.me/sponsor1",
            "ref": "uuid-1",
        }])

    async def test_task_missing_assignment_id_is_dropped(self) -> None:
        with _patched(_Response(200, {
            "ok": True,
            "tasks": [{"title": "No id", "target_link": "https://t.me/x"}],
        })):
            result = await get_traffy_tasks("key", 123)
        self.assertEqual(result, [])

    async def test_empty_tasks_is_a_valid_empty_result(self) -> None:
        with _patched(_Response(200, {"ok": True, "tasks": []})):
            result = await get_traffy_tasks("key", 123)
        self.assertEqual(result, [])

    async def test_http_401_is_treated_as_unavailable_not_empty(self) -> None:
        # Bad key / not-yet-moderated bot -- must NOT be read as "no sponsors".
        with _patched(_Response(401, {"ok": False})):
            result = await get_traffy_tasks("key", 123)
        self.assertIsNone(result)

    async def test_ok_false_is_treated_as_unavailable(self) -> None:
        with _patched(_Response(200, {"ok": False, "error": "bad_telegram_id"})):
            result = await get_traffy_tasks("key", 123)
        self.assertIsNone(result)

    async def test_limit_is_clamped_between_one_and_ten(self) -> None:
        captured = {}

        class _CapturingSession(_Session):
            def post(self, *_args, **kwargs):
                captured["json"] = kwargs.get("json")
                return self.response

        with patch(
            "bot.services.traffy.aiohttp.ClientSession",
            return_value=_CapturingSession(_Response(200, {"ok": True, "tasks": []})),
        ):
            await get_traffy_tasks("key", 123, limit=99)
        self.assertEqual(captured["json"]["limit"], 10)


class CheckTraffyTasksTests(unittest.IsolatedAsyncioTestCase):
    async def test_no_key_returns_empty_dict(self) -> None:
        self.assertEqual(await check_traffy_tasks("", 123, ["a"]), {})

    async def test_no_assignment_ids_returns_empty_dict_without_a_call(self) -> None:
        self.assertEqual(await check_traffy_tasks("key", 123, []), {})

    async def test_completed_status_maps_to_true(self) -> None:
        with _patched(_Response(200, {
            "ok": True,
            "results": [
                {"assignment_id": "a", "status": "completed"},
                {"assignment_id": "b", "status": "pending"},
                {"assignment_id": "c", "status": "rejected"},
            ],
        })):
            statuses = await check_traffy_tasks("key", 123, ["a", "b", "c"])
        self.assertEqual(statuses, {"a": True, "b": False, "c": False})

    async def test_http_error_returns_none(self) -> None:
        with _patched(_Response(500, {})):
            self.assertIsNone(await check_traffy_tasks("key", 123, ["a"]))

    async def test_ok_false_returns_none(self) -> None:
        with _patched(_Response(200, {"ok": False})):
            self.assertIsNone(await check_traffy_tasks("key", 123, ["a"]))


if __name__ == "__main__":
    unittest.main()
