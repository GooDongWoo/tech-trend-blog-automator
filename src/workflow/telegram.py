"""Configured Telegram briefing transport; credentials never enter run records."""
from config import settings


def configured_recipient():
    chat = str(settings.telegram_chat_id)
    user = str(settings.telegram_reviewer_user_id or (chat if chat.isdigit() else ''))
    if not chat or not user:
        raise ValueError('Telegram reviewer chat/user is not configured')
    return chat, user


def briefing_message_kwargs(payload):
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    return dict(chat_id=payload['chat_id'], text=payload['text'],
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(**button) for button in row] for row in payload['rows']]),
        disable_web_page_preview=payload['disable_web_page_preview'])


async def send_briefing(payload):
    if not settings.telegram_bot_token or settings.telegram_bot_token == 'your_telegram_bot_token_here':
        raise ValueError('Telegram delivery unavailable')
    from telegram import Bot
    async with Bot(token=settings.telegram_bot_token) as bot:
        await bot.send_message(**briefing_message_kwargs(payload))
