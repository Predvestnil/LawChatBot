"""
API Gateway - точка входа для Telegram API
Обрабатывает входящие сообщения от пользователей и маршрутизирует их в соответствующие сервисы
"""

import asyncio
import logging
import json
import os
from typing import Dict, Any, Optional

from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.client.session.aiohttp import AiohttpSession

import aiohttp
from dotenv import load_dotenv

load_dotenv()

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Загрузка переменных окружения
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
AUTH_SERVICE_URL = os.getenv("AUTH_SERVICE_URL", "http://auth-service:8001")
DIALOGUE_SERVICE_URL = os.getenv("DIALOGUE_SERVICE_URL", "http://dialogue-service:8002")
ML_SERVICE_URL = os.getenv("ML_SERVICE_URL", "http://ml-service:8003")

# Инициализация бота и диспетчера
session = AiohttpSession()
bot = Bot(token=TELEGRAM_TOKEN, session=session)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# Сервисные функции для взаимодействия с другими микросервисами
async def call_service(url: str, data: Dict[str, Any]) -> Dict[str, Any]:
    """Отправляет запрос к указанному микросервису"""
    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(url, json=data) as response:
                if response.status == 200:
                    return await response.json()
                else:
                    error_text = await response.text()
                    logger.error(f"Service error: {response.status} - {error_text}")
                    return {"error": error_text}
        except Exception as e:
            logger.error(f"Failed to call service {url}: {e}")
            return {"error": str(e)}

async def check_authorization(user_id: int) -> bool:
    """Проверяет авторизацию пользователя через Auth Service"""
    result = await call_service(f"{AUTH_SERVICE_URL}/check", {"user_id": user_id})
    return result.get("authorized", False)

async def process_message(user_id: int, message_text: str, chat_id: int) -> Dict[str, Any]:
    """Обрабатывает сообщение через Dialogue Service"""
    return await call_service(
        f"{DIALOGUE_SERVICE_URL}/process", 
        {
            "user_id": user_id,
            "message_text": message_text,
            "chat_id": chat_id
        }
    )

async def get_full_answer(user_id: int, message_id: str) -> Optional[str]:
    """Получает полный ответ через Dialogue Service"""
    result = await call_service(
        f"{DIALOGUE_SERVICE_URL}/full_answer",
        {
            "user_id": user_id,
            "message_id": message_id
        }
    )
    return result.get("full_answer")

# Обработчики сообщений
@dp.message(Command("start"))
async def cmd_start(message: types.Message) -> None:
    """Обработчик команды /start"""
    # Регистрация нового пользователя
    user_data = {
        "user_id": message.from_user.id,
        "username": message.from_user.username,
        "first_name": message.from_user.first_name,
        "last_name": message.from_user.last_name,
        "is_bot": message.from_user.is_bot,
        "is_premium": message.from_user.is_premium,
    }
    
    await call_service(f"{AUTH_SERVICE_URL}/register", user_data)
    
    await message.answer(
        "Привет! Я ИИ-бот, который может ответить на ваши вопросы. "
        "Чтобы получить полный ответ, вам потребуется предоставить номер телефона."
    )

@dp.message()
async def handle_message(message: types.Message) -> None:
    """Обработчик любых текстовых сообщений"""
    user_id = message.from_user.id
    chat_id = message.chat.id
    message_text = message.text
    
    # Проверка на телефонный номер
    if message_text and message_text.strip().replace('+', '').isdigit() and len(message_text.strip()) >= 10:
        # Обработка номера телефона
        await call_service(
            f"{AUTH_SERVICE_URL}/authorize",
            {
                "user_id": user_id,
                "phone_number": message_text.strip()
            }
        )
        await message.answer("Спасибо за предоставленный номер телефона. Теперь вы имеете доступ к полным ответам!")
        return
    
    # Логирование сообщения пользователя
    await call_service(
        f"{DIALOGUE_SERVICE_URL}/log_message", 
        {
            "user_id": user_id,
            "chat_id": chat_id,
            "message_text": message_text,
            "is_bot_message": False
        }
    )
    
    # Обработка сообщения
    response = await process_message(user_id, message_text, chat_id)
    
    if "error" in response:
        await message.answer(f"Произошла ошибка: {response['error']}")
        return
    
    if response.get("truncated_answer"):
        # Отправляем обрезанный ответ
        reply_markup = types.InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    types.InlineKeyboardButton(
                        text="Получить полный ответ", 
                        callback_data=f"full_answer:{response.get('message_id')}"
                    )
                ]
            ]
        )
        
        await message.answer(
            f"{response['truncated_answer']}\n\n"
            "Чтобы получить полный ответ, поделитесь своим номером телефона.",
            reply_markup=reply_markup
        )
    else:
        # Отправляем полный ответ
        await message.answer(response["full_answer"])
    
    # Логирование ответа бота
    await call_service(
        f"{DIALOGUE_SERVICE_URL}/log_message", 
        {
            "user_id": user_id,
            "chat_id": chat_id,
            "message_text": response.get("full_answer", ""),
            "is_bot_message": True
        }
    )

@dp.callback_query(lambda c: c.data.startswith("full_answer:"))
async def handle_full_answer(callback_query: types.CallbackQuery) -> None:
    """Обработчик запроса на получение полного ответа"""
    user_id = callback_query.from_user.id
    message_id = callback_query.data.split(":")[1]
    
    # Проверяем авторизацию
    authorized = await check_authorization(user_id)
    
    if not authorized:
        await callback_query.answer("Для получения полного ответа необходимо предоставить номер телефона")
        await callback_query.message.answer(
            "Пожалуйста, отправьте свой номер телефона для получения полного ответа."
        )
        return
    
    # Получаем полный ответ
    full_answer = await get_full_answer(user_id, message_id)
    
    if full_answer:
        await callback_query.message.answer(full_answer)
        await callback_query.answer()
    else:
        await callback_query.answer("Не удалось получить полный ответ")

async def main() -> None:
    """Точка входа в приложение"""
    # Запуск бота
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())