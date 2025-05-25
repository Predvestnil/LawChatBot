"""
Auth Service - сервис авторизации пользователей
Обрабатывает регистрацию пользователей и проверку авторизации
"""

import os
import logging
import json
import base64
from typing import Dict, Any, List, Optional
from datetime import datetime

import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import asyncpg
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
ENCRYPTION_KEY = os.getenv("ENCRYPTION_KEY")
if not ENCRYPTION_KEY:
    # Генерация ключа шифрования 32 байта (256 бит) если не задан
    ENCRYPTION_KEY = get_random_bytes(32)
    logger.warning("ENCRYPTION_KEY not found in environment. Generated new key.")
else:
    # Преобразование ключа из base64 в байты
    ENCRYPTION_KEY = base64.b64decode(ENCRYPTION_KEY)

app = FastAPI(title="Auth Service")

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
        # Проверяем существование таблицы users
        exists = await conn.fetchval(
            "SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'users')"
        )
        
        if not exists:
            await conn.execute("""
                CREATE TABLE users (
                    user_id BIGINT PRIMARY KEY,
                    username VARCHAR(32),
                    first_name VARCHAR(64),
                    last_name VARCHAR(64),
                    is_bot BOOLEAN DEFAULT FALSE,
                    is_premium BOOLEAN DEFAULT FALSE,
                    created_at TIMESTAMPTZ DEFAULT NOW(),
                    last_activity TIMESTAMPTZ DEFAULT NOW(),
                    is_blocked BOOLEAN DEFAULT FALSE,
                    block_reason TEXT,
                    phone_number TEXT
                )
            """)
            logger.info("Created users table")
    finally:
        await conn.close()

# Модели данных
class UserBase(BaseModel):
    user_id: int
    username: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    is_bot: bool = False
    is_premium: bool = False

class PhoneNumber(BaseModel):
    user_id: int
    phone_number: str

class UserId(BaseModel):
    user_id: int

# Маршруты API
@app.post("/register")
async def register_user(user: UserBase):
    """Регистрирует нового пользователя в системе"""
    conn = await get_connection()
    try:
        # Проверяем, существует ли пользователь
        exists = await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM users WHERE user_id = $1)",
            user.user_id
        )
        
        if exists:
            # Обновляем информацию о пользователе
            await conn.execute("""
                UPDATE users 
                SET username = $1, first_name = $2, last_name = $3, 
                    is_bot = $4, is_premium = $5, last_activity = NOW()
                WHERE user_id = $6
            """, user.username, user.first_name, user.last_name, 
                user.is_bot, user.is_premium, user.user_id)
            return {"status": "updated", "user_id": user.user_id}
        else:
            # Создаем нового пользователя
            await conn.execute("""
                INSERT INTO users (user_id, username, first_name, last_name, is_bot, is_premium)
                VALUES ($1, $2, $3, $4, $5, $6)
            """, user.user_id, user.username, user.first_name, user.last_name,
                user.is_bot, user.is_premium)
            return {"status": "registered", "user_id": user.user_id}
    finally:
        await conn.close()

@app.post("/authorize")
async def authorize_user(auth_data: PhoneNumber):
    """Авторизует пользователя по номеру телефона"""
    conn = await get_connection()
    try:
        # Шифруем номер телефона перед сохранением
        encrypted_phone = encrypt_data(auth_data.phone_number)
        
        # Обновляем номер телефона пользователя
        await conn.execute("""
            UPDATE users 
            SET phone_number = $1, last_activity = NOW()
            WHERE user_id = $2
        """, encrypted_phone, auth_data.user_id)
        
        return {"status": "authorized", "user_id": auth_data.user_id}
    finally:
        await conn.close()

@app.post("/check")
async def check_authorization(user: UserId):
    """Проверяет, авторизован ли пользователь"""
    conn = await get_connection()
    try:
        # Проверяем наличие номера телефона
        phone_number = await conn.fetchval(
            "SELECT phone_number FROM users WHERE user_id = $1",
            user.user_id
        )
        
        # Обновляем время последней активности
        await conn.execute(
            "UPDATE users SET last_activity = NOW() WHERE user_id = $1",
            user.user_id
        )
        
        return {"authorized": phone_number is not None, "user_id": user.user_id}
    finally:
        await conn.close()

@app.post("/block")
async def block_user(user: UserId, reason: str = ""):
    """Блокирует пользователя"""
    conn = await get_connection()
    try:
        await conn.execute("""
            UPDATE users 
            SET is_blocked = TRUE, block_reason = $1
            WHERE user_id = $2
        """, reason, user.user_id)
        
        return {"status": "blocked", "user_id": user.user_id}
    finally:
        await conn.close()

@app.post("/unblock")
async def unblock_user(user: UserId):
    """Разблокирует пользователя"""
    conn = await get_connection()
    try:
        await conn.execute("""
            UPDATE users 
            SET is_blocked = FALSE, block_reason = NULL
            WHERE user_id = $1
        """, user.user_id)
        
        return {"status": "unblocked", "user_id": user.user_id}
    finally:
        await conn.close()

@app.get("/user/{user_id}")
async def get_user(user_id: int):
    """Получает информацию о пользователе"""
    conn = await get_connection()
    try:
        user = await conn.fetchrow(
            "SELECT * FROM users WHERE user_id = $1",
            user_id
        )
        
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        
        # Расшифровываем номер телефона, если он есть
        phone_number = None
        if user["phone_number"]:
            try:
                phone_number = decrypt_data(user["phone_number"])
            except Exception as e:
                logger.error(f"Error decrypting phone number: {e}")
        
        return {
            "user_id": user["user_id"],
            "username": user["username"],
            "first_name": user["first_name"],
            "last_name": user["last_name"],
            "is_bot": user["is_bot"],
            "is_premium": user["is_premium"],
            "created_at": user["created_at"],
            "last_activity": user["last_activity"],
            "is_blocked": user["is_blocked"],
            "block_reason": user["block_reason"],
            "has_phone": phone_number is not None
        }
    finally:
        await conn.close()

@app.on_event("startup")
async def startup_event():
    """Выполняется при запуске сервиса"""
    await init_db()
    logger.info("Auth Service started")

if __name__ == "__main__":
    uvicorn.run("auth_service:app", host="0.0.0.0", port=8001, reload=True)