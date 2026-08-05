from aiogram.fsm.state import State, StatesGroup


class WithdrawStates(StatesGroup):
    choose_recipient = State()
    enter_recipient_username = State()
    choose_method = State()
    confirm = State()
    enter_captcha = State()
