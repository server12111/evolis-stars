from aiogram.fsm.state import State, StatesGroup


class ChatOwnerPromoStates(StatesGroup):
    enter_code = State()


class ChatOwnerBonusStates(StatesGroup):
    enter_code = State()
    enter_reward = State()
    enter_limit = State()
    choose_mode = State()
    choose_sponsors = State()


class ChatOwnerBroadcastStates(StatesGroup):
    enter_text = State()
    enter_photos = State()
    ask_buttons = State()
    enter_button = State()
    enter_interval = State()


class PendingSponsorAddStates(StatesGroup):
    """User-scoped (not chat-scoped) — set while waiting for the owner to
    finish Telegram's native "add bot as admin" flow in a channel/chat
    they're attaching as a bonus sponsor. Read back from the my_chat_member
    update, which fires in the *sponsor's* chat, not the bonus's chat."""
    awaiting_add = State()
