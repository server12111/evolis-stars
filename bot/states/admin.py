from aiogram.fsm.state import State, StatesGroup


class AdminBroadcastStates(StatesGroup):
    waiting_message = State()
    confirm = State()


class AdminUserStates(StatesGroup):
    search = State()
    add_stars = State()
    sub_stars = State()
    add_refs = State()


class AdminPromoStates(StatesGroup):
    enter_code = State()
    enter_reward = State()
    enter_limit = State()
    enter_expiry = State()


class AdminTaskStates(StatesGroup):
    enter_title = State()
    enter_description = State()
    enter_url = State()
    enter_reward = State()
    enter_max = State()
    enter_photo = State()


class AdminSettingsStates(StatesGroup):
    enter_value = State()




class AdminMediaStates(StatesGroup):
    enter_text = State()
    enter_photo = State()
    enter_video = State()


class AdminGameStates(StatesGroup):
    enter_value = State()


class AdminLotteryStates(StatesGroup):
    enter_ticket_price = State()
    enter_ticket_limit = State()
    choose_end_type = State()
    enter_end_value = State()


class AdminOwnSponsorStates(StatesGroup):
    enter_kind = State()
    enter_target = State()
    enter_url = State()
    enter_name = State()
    enter_target_count = State()


class AdminLinkButtonStates(StatesGroup):
    enter_label = State()
    enter_url = State()
