from aiogram.fsm.state import State, StatesGroup


class ChatOwnerPromoStates(StatesGroup):
    enter_code = State()


class ChatOwnerBonusStates(StatesGroup):
    enter_code = State()
    enter_reward = State()
    enter_limit = State()
    choose_mode = State()
    enter_conditions = State()
