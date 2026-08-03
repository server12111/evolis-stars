from aiogram import F, Router

router = Router()
router.message.filter(F.chat.type.in_({"group", "supergroup"}))
router.callback_query.filter(F.message.as_("m").chat.type.in_({"group", "supergroup"}))
