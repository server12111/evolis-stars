from aiogram.fsm.state import State, StatesGroup


class CryptoWithdrawStates(StatesGroup):
    choose_method = State()
    enter_amount = State()
    enter_recipient = State()
    confirm = State()
