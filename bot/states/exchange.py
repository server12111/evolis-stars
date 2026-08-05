from aiogram.fsm.state import State, StatesGroup


class ExchangeStates(StatesGroup):
    enter_amount = State()
