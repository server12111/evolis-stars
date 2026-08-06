import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers.admin.logs import cb_logs_full, cb_logs_menu, cb_logs_tail, tail_log_lines, settings


def admin_user() -> SimpleNamespace:
    return SimpleNamespace(user_id=1, is_admin=True)


def callback() -> SimpleNamespace:
    return SimpleNamespace(
        answer=AsyncMock(),
        message=SimpleNamespace(
            edit_text=AsyncMock(), answer=AsyncMock(), answer_document=AsyncMock(),
        ),
    )


class TailLogLinesTests(unittest.TestCase):
    def test_missing_file_returns_placeholder(self) -> None:
        self.assertEqual(tail_log_lines("Z:\\does\\not\\exist.log"), "Файл лога пока не создан.")

    def test_returns_last_n_lines_only(self) -> None:
        import tempfile, os
        fd, path = tempfile.mkstemp(suffix=".log")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                for i in range(10):
                    f.write(f"line {i}\n")
            result = tail_log_lines(path, n=3)
        finally:
            os.remove(path)
        self.assertEqual(result, "line 7\nline 8\nline 9\n")

    def test_empty_file_returns_placeholder(self) -> None:
        import tempfile, os
        fd, path = tempfile.mkstemp(suffix=".log")
        os.close(fd)
        try:
            result = tail_log_lines(path)
        finally:
            os.remove(path)
        self.assertEqual(result, "Файл лога пока пуст.")

    def test_fewer_lines_than_requested_returns_all(self) -> None:
        import tempfile, os
        fd, path = tempfile.mkstemp(suffix=".log")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write("only line\n")
            result = tail_log_lines(path, n=300)
        finally:
            os.remove(path)
        self.assertEqual(result, "only line\n")


class LogsHandlerTests(unittest.IsolatedAsyncioTestCase):
    async def test_menu_shown_to_admin(self) -> None:
        cb = callback()
        await cb_logs_menu(cb, admin_user())
        cb.message.edit_text.assert_awaited_once()
        cb.answer.assert_awaited_once()

    async def test_menu_ignored_for_non_admin(self) -> None:
        non_admin = SimpleNamespace(user_id=2, is_admin=False)
        cb = callback()
        await cb_logs_menu(cb, non_admin)
        cb.message.edit_text.assert_not_awaited()
        cb.answer.assert_not_awaited()

    async def test_tail_sends_a_document(self) -> None:
        cb = callback()
        with patch.object(settings, "log_file_path", "Z:\\does\\not\\exist.log"):
            await cb_logs_tail(cb, admin_user())
        cb.message.answer_document.assert_awaited_once()

    async def test_full_missing_file_shows_alert_not_document(self) -> None:
        cb = callback()
        with patch.object(settings, "log_file_path", "Z:\\does\\not\\exist.log"):
            await cb_logs_full(cb, admin_user())
        cb.message.answer_document.assert_not_awaited()
        cb.answer.assert_awaited_once()
        self.assertIn("не создан", cb.answer.await_args.args[0])

    async def test_full_sends_the_actual_file_when_present(self) -> None:
        import tempfile, os
        fd, path = tempfile.mkstemp(suffix=".log")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write("some log content\n")
            cb = callback()
            with patch.object(settings, "log_file_path", path):
                await cb_logs_full(cb, admin_user())
            cb.message.answer_document.assert_awaited_once()
        finally:
            os.remove(path)

    async def test_non_admin_cannot_download_logs(self) -> None:
        non_admin = SimpleNamespace(user_id=2, is_admin=False)
        cb = callback()
        await cb_logs_tail(cb, non_admin)
        await cb_logs_full(cb, non_admin)
        cb.message.answer_document.assert_not_called()


if __name__ == "__main__":
    unittest.main()
