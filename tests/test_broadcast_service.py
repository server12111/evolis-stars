import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from bot.services.broadcast import broadcast


def _text_message():
    return SimpleNamespace(
        photo=None, video=None, document=None, animation=None,
        text="Hello ⭐", html_text="Hello ⭐", caption=None,
        copy_to=AsyncMock(),
    )


def _photo_message():
    # Real aiogram Message objects have no `caption_html` attribute — only
    # `html_text`, which falls back to caption/caption_entities when `text`
    # is empty. A mock that defines `caption_html` would mask a call to a
    # nonexistent attribute that raises AttributeError for real messages.
    return SimpleNamespace(
        photo=[SimpleNamespace(file_id="f1"), SimpleNamespace(file_id="f2")],
        video=None, document=None, animation=None,
        text=None, html_text="caption <b>text</b>", caption="caption text",
        copy_to=AsyncMock(),
    )


def _voice_message():
    return SimpleNamespace(
        photo=None, video=None, document=None, animation=None,
        text=None, html_text=None, caption=None,
        copy_to=AsyncMock(),
    )


class BroadcastSendDispatchTests(unittest.IsolatedAsyncioTestCase):
    async def test_text_message_uses_send_message_not_copy(self) -> None:
        bot = SimpleNamespace(send_message=AsyncMock())
        message = _text_message()

        success, fail = await broadcast(bot, [1, 2], message)

        self.assertEqual((success, fail), (2, 0))
        message.copy_to.assert_not_awaited()
        self.assertEqual(bot.send_message.await_count, 2)
        bot.send_message.assert_any_await(1, "Hello ⭐", parse_mode="HTML", reply_markup=None)

    async def test_photo_message_uses_send_photo_with_last_size(self) -> None:
        bot = SimpleNamespace(send_photo=AsyncMock())
        message = _photo_message()

        await broadcast(bot, [1], message)

        message.copy_to.assert_not_awaited()
        bot.send_photo.assert_awaited_once_with(
            1, "f2", caption="caption <b>text</b>", parse_mode="HTML", reply_markup=None
        )

    async def test_unsupported_content_falls_back_to_copy_to(self) -> None:
        bot = SimpleNamespace()
        message = _voice_message()

        await broadcast(bot, [1], message)

        message.copy_to.assert_awaited_once_with(1, reply_markup=None)

    async def test_reply_markup_is_forwarded(self) -> None:
        bot = SimpleNamespace(send_message=AsyncMock())
        message = _text_message()
        kb = SimpleNamespace(inline_keyboard=[])

        await broadcast(bot, [1], message, kb)

        bot.send_message.assert_awaited_once_with(1, "Hello ⭐", parse_mode="HTML", reply_markup=kb)

    async def test_failures_are_counted_not_raised(self) -> None:
        bot = SimpleNamespace(send_message=AsyncMock(side_effect=[None, Exception("blocked")]))
        message = _text_message()

        success, fail = await broadcast(bot, [1, 2], message)
        self.assertEqual((success, fail), (1, 1))


if __name__ == "__main__":
    unittest.main()
