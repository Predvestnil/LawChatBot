"""
Dialogue Service - сервис управления диалогами
Обрабатывает сообщения пользователей, сохраняет контекст диалога
и взаимодействует с ML Service для генерации ответов
"""

import os
import logging
import json
import uuid
import base64
from typing import Dict, Any, List, Optional
from datetime import datetime

import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import asyncpg
import aiohttp
from Crypto.Cipher import AES
from Crypto.Random import get_random_bytes
from Crypto.Util.Padding import pad, unpad
from dotenv import load_dotenv

load_dotenv()

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Загрузка переменных окружения
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@db:5432/telegram_bot")
ML_SERVICE_URL = os.getenv("ML_SERVICE_URL", "http://ml-service:8003")
AUTH_SERVICE_URL = os.getenv("AUTH_SERVICE_URL", "http://auth-service:8001")
ENCRYPTION_KEY = os.getenv("ENCRYPTION_KEY")
if not ENCRYPTION_KEY:
    # Генерация ключа шифрования 32 байта (256 бит) если не задан
    ENCRYPTION_KEY = get_random_bytes(32)
    logger.warning("ENCRYPTION_KEY not found in environment. Generated new key.")
else:
    # Преобразование ключа из base64 в байты
    ENCRYPTION_KEY = base64.b64decode(ENCRYPTION_KEY)

# Максимальная длина обрезанного ответа
MAX_TRUNCATED_LENGTH = 100

app = FastAPI(title="Dialogue Service")

# Шифрование AES-256
def encrypt_data(data: str) -> str:
    """Шифрует строку с использованием AES-256"""
    cipher = AES.new(ENCRYPTION_KEY, AES.MODE_CBC)
    ct_bytes = cipher.encrypt(pad(data.encode('utf-8'), AES.block_size))
    iv = base64.b64encode(cipher.iv).decode('utf-8')
    ct = base64.b64encode(ct_bytes).decode('utf-8')
    return json.dumps({'iv': iv, 'ciphertext': ct})

def decrypt_data(encrypted_data: str) -> str:
    """Дешифрует строку, зашифрованную с использованием AES-256"""
    b64 = json.loads(encrypted_data)
    iv = base64.b64decode(b64['iv'])
    ct = base64.b64decode(b64['ciphertext'])
    cipher = AES.new(ENCRYPTION_KEY, AES.MODE_CBC, iv)
    pt = unpad(cipher.decrypt(ct), AES.block_size)
    return pt.decode('utf-8')

# База данных
async def get_connection():
    """Устанавливает подключение к базе данных"""
    return await asyncpg.connect(DATABASE_URL)

async def init_db():
    """Инициализирует базу данных, создавая необходимые таблицы"""
    conn = await get_connection()
    try:
        # Проверяем существование таблицы dialogs
        exists = await conn.fetchval(
            "SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'dialogs')"
        )
        
        if not exists:
            await conn.execute("""
                CREATE TABLE dialogs (
                    message_id TEXT PRIMARY KEY,
                    user_id BIGINT,
                    chat_id BIGINT,
                    message_text TEXT,
                    is_bot_message BOOLEAN DEFAULT FALSE,
                    sent_at TIMESTAMPTZ DEFAULT NOW(),
                    delivered_at TIMESTAMPTZ,
                    read_at TIMESTAMPTZ,
                    full_answer TEXT
                )
            """)
            
            # Создаем индексы
            await conn.execute("""
                CREATE INDEX idx_dialogs_user_id ON dialogs(user_id);
                CREATE INDEX idx_dialogs_sent_at ON dialogs(sent_at);
            """)
            
            logger.info("Created dialogs table")
            
        # Проверяем существование таблицы user_states
        exists = await conn.fetchval(
            "SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'user_states')"
        )
        
        if not exists:
            await conn.execute("""
                CREATE TABLE user_states (
                    user_id BIGINT PRIMARY KEY,
                    current_state VARCHAR(64),
                    state_data JSONB,
                    updated_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)
            logger.info("Created user_states table")
            
    finally:
        await conn.close()

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

async def get_ml_response(user_id: int, message_text: str, context: List[Dict[str, Any]]) -> str:
    """Получает ответ от ML Service"""
    result = await call_service(
        f"{ML_SERVICE_URL}/generate",
        {
            "user_id": user_id,
            "message": message_text,
            "context": context
        }
    )
    
    if "error" in result:
        logger.error(f"Error from ML Service: {result['error']}")
        return "К сожалению, произошла ошибка при обработке вашего запроса."
    
    return result.get("response", "")

async def check_authorization(user_id: int) -> bool:
    """Проверяет авторизацию пользователя через Auth Service"""
    result = await call_service(f"{AUTH_SERVICE_URL}/check", {"user_id": user_id})
    return result.get("authorized", False)

# Модели данных
class Message(BaseModel):
    user_id: int
    message_text: str
    chat_id: int

class LogMessage(BaseModel):
    user_id: int
    chat_id: int
    message_text: str
    is_bot_message: bool = False

class FullAnswerRequest(BaseModel):
    user_id: int
    message_id: str

# Маршруты API
@app.post("/process")
async def process_message(message: Message):
    """Обрабатывает сообщение пользователя и генерирует ответ"""
    conn = await get_connection()
    try:
        # Генерируем уникальный идентификатор сообщения
        message_id = str(uuid.uuid4())
        
        # Получаем контекст диалога (последние 10 сообщений)
        context_rows = await conn.fetch("""
            SELECT message_text, is_bot_message
            FROM dialogs
            WHERE user_id = $1 AND chat_id = $2
            ORDER BY sent_at DESC
            LIMIT 10
        """, message.user_id, message.chat_id)
        
        context = [
            {
                "role": "assistant" if row["is_bot_message"] else "user",
                "content": row["message_text"]
            }
            for row in reversed(context_rows)
        ]
        
        # Добавляем текущее сообщение в контекст
        context.append({"role": "user", "content": message.message_text})
        
        # Получаем ответ от ML Service
        full_answer = await get_ml_response(message.user_id, message.message_text, context)
        
        # Проверяем авторизацию пользователя
        authorized = await check_authorization(message.user_id)
        
        # Шифруем полный ответ перед сохранением
        encrypted_answer = encrypt_data(full_answer)
        
        # Сохраняем полный ответ и сообщение пользователя
        await conn.execute("""
            INSERT INTO dialogs (message_id, user_id, chat_id, message_text, full_answer)
            VALUES ($1, $2, $3, $4, $5)
        """, message_id, message.user_id, message.chat_id, message.message_text, encrypted_answer)
        
        if authorized:
            # Возвращаем полный ответ для авторизованного пользователя
            return {
                "full_answer": full_answer,
                "truncated_answer": None,
                "message_id": message_id
            }
        else:
            # Обрезаем ответ для неавторизованного пользователя
            truncated_answer = full_answer[:MAX_TRUNCATED_LENGTH]
            if len(full_answer) > MAX_TRUNCATED_LENGTH:
                truncated_answer += "..."
                
            return {
                "full_answer": None,
                "truncated_answer": truncated_answer,
                "message_id": message_id
            }
    finally:
        await conn.close()

@app.post("/log_message")
async def log_message(message: LogMessage):
    """Логирует сообщение в базу данных"""
    conn = await get_connection()
    try:
        # Генерируем уникальный идентификатор сообщения
        message_id = str(uuid.uuid4())
        
        # Шифруем текст сообщения, если это не пустая строка
        encrypted_text = None
        if message.message_text:
            encrypted_text = encrypt_data(message.message_text)
        
        # Добавляем сообщение в базу данных
        await conn.execute("""
            INSERT INTO dialogs 
            (message_id, user_id, chat_id, message_text, is_bot_message)
            VALUES ($1, $2, $3, $4, $5)
        """, message_id, message.user_id, message.chat_id, 
            encrypted_text, message.is_bot_message)
        
        return {"status": "logged", "message_id": message_id}
    finally:
        await conn.close()

@app.post("/full_answer")
async def get_full_answer(request: FullAnswerRequest):
    """Возвращает полный ответ по идентификатору сообщения"""
    # Проверяем авторизацию
    authorized = await check_authorization(request.user_id)
    if not authorized:
        return {"error": "Not authorized"}
    
    conn = await get_connection()
    try:
        # Получаем зашифрованный ответ из базы данных
        encrypted_answer = await conn.fetchval("""
            SELECT full_answer 
            FROM dialogs 
            WHERE message_id = $1 AND user_id = $2
        """, request.message_id, request.user_id)
        
        if not encrypted_answer:
            return {"error": "Answer not found"}
        
        # Расшифровываем ответ
        full_answer = decrypt_data(encrypted_answer)
        
        # Обновляем время прочтения
        await conn.execute("""
            UPDATE dialogs 
            SET read_at = NOW() 
            WHERE message_id = $1
        """, request.message_id)
        
        return {"full_answer": full_answer}
    finally:
        await conn.close()

@app.get("/history/{user_id}")
async def get_dialog_history(user_id: int, limit: int = 20):
    """Получает историю диалога пользователя"""
    conn = await get_connection()
    try:
        # Проверяем авторизацию
        authorized = await check_authorization(user_id)
        
        # Получаем историю диалога
        rows = await conn.fetch("""
            SELECT message_id, message_text, is_bot_message, 
                  sent_at, full_answer
            FROM dialogs
            WHERE user_id = $1
            ORDER BY sent_at DESC
            LIMIT $2
        """, user_id, limit)
        
        history = []
        for row in rows:
            message_item = {
                "message_id": row["message_id"],
                "is_bot_message": row["is_bot_message"],
                "sent_at": row["sent_at"].isoformat(),
            }
            
            # Расшифровываем текст сообщения
            if row["message_text"]:
                try:
                    message_item["message_text"] = decrypt_data(row["message_text"])
                except Exception as e:
                    logger.error(f"Error decrypting message: {e}")
                    message_item["message_text"] = "[Ошибка расшифровки]"
            
            # Расшифровываем полный ответ для авторизованных пользователей
            if authorized and row["full_answer"] and row["is_bot_message"]:
                try:
                    message_item["full_answer"] = decrypt_data(row["full_answer"])
                except Exception as e:
                    logger.error(f"Error decrypting full answer: {e}")
                    message_item["full_answer"] = "[Ошибка расшифровки]"
            
            history.append(message_item)
        
        return {"history": history}
    finally:
        await conn.close()

@app.post("/update_state")
async def update_user_state(user_id: int, state: str, data: Dict[str, Any] = None):
    """Обновляет состояние пользователя"""
    conn = await get_connection()
    try:
        await conn.execute("""
            INSERT INTO user_states (user_id, current_state, state_data, updated_at)
            VALUES ($1, $2, $3, NOW())
            ON CONFLICT (user_id) 
            DO UPDATE SET 
                current_state = $2, 
                state_data = $3, 
                updated_at = NOW()
        """, user_id, state, json.dumps(data) if data else None)
        
        return {"status": "updated", "user_id": user_id, "state": state}
    finally:
        await conn.close()

@app.get("/state/{user_id}")
async def get_user_state(user_id: int):
    """Получает текущее состояние пользователя"""
    conn = await get_connection()
    try:
        row = await conn.fetchrow("""
            SELECT current_state, state_data, updated_at
            FROM user_states
            WHERE user_id = $1
        """, user_id)
        
        if not row:
            return {"state": None, "data": None}
        
        return {
            "state": row["current_state"],
            "data": json.loads(row["state_data"]) if row["state_data"] else None,
            "updated_at": row["updated_at"].isoformat()
        }
    finally:
        await conn.close()

@app.on_event("startup")
async def startup_event():
    """Выполняется при запуске сервиса"""
    await init_db()
    logger.info("Dialogue Service started")

if __name__ == "__main__":
    uvicorn.run("dialogue_service:app", host="0.0.0.0", port=8002, reload=True)