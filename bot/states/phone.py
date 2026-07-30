from aiogram.fsm.state import State, StatesGroup


class PhoneStates(StatesGroup):
    waiting_contact = State()
