from aiogram.fsm.state import State, StatesGroup


class VcWithdrawStates(StatesGroup):
    enter_amount = State()
    confirm = State()
