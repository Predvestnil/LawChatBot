"""
Database Service - сервис для работы с базой данных
Обеспечивает инициализацию и управление базой данных
"""

import os
import logging
import json
from typing import Dict, Any, List, Optional

import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import asyncpg
from dotenv import load_dotenv

load_dotenv()

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Загрузка переменных окружения
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@postgres:5432/telegram_bot")

app = FastAPI(title="Database Service")

# Создаем схему базы данных
SCHEMA_SQL = """
-- Таблица пользователей
CREATE TABLE IF NOT EXISTS users (
  user_id BIGINT PRIMARY KEY,
  username VARCHAR(32),
  first_name VARCHAR(64),
  last_name VARCHAR(64),
  is_bot BOOLEAN DEFAULT FALSE,
  is_premium BOOLEAN DEFAULT FALSE,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  last_activity TIMESTAMPTZ DEFAULT NOW(),
  is_blocked BOOLEAN DEFAULT FALSE,
  block_reason TEXT
);

-- Таблица диалогов
CREATE TABLE IF NOT EXISTS dialogs (
  message_id BIGINT PRIMARY KEY,
  user_id BIGINT REFERENCES users(user_id),
  chat_id BIGINT,
  message_text TEXT,
  is_bot_message BOOLEAN DEFAULT FALSE,
  sent_at TIMESTAMPTZ DEFAULT NOW(),
  delivered_at TIMESTAMPTZ,
  read_at TIMESTAMPTZ
);

-- Индексы для таблицы диалогов
CREATE INDEX IF NOT EXISTS idx_dialogs_user_id ON dialogs(user_id);
CREATE INDEX IF NOT EXISTS idx_dialogs_sent_at ON dialogs(sent_at);

-- Таблица состояний пользователя
CREATE TABLE IF NOT EXISTS user_states (
  user_id BIGINT PRIMARY KEY REFERENCES users(user_id),
  current_state VARCHAR(64),
  state_data JSONB,
  updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Таблица логов действий
CREATE TABLE IF NOT EXISTS action_logs (
  log_id SERIAL PRIMARY KEY,
  user_id BIGINT REFERENCES users(user_id),
  action_type VARCHAR(32),
  action_details TEXT,
  performed_at TIMESTAMPTZ DEFAULT NOW()
);
"""

# База данных
async def get_connection():
    """Устанавливает подключение к базе данных"""
    return await asyncpg.connect(DATABASE_URL)

async def init_db():
    """Инициализирует базу данных, создавая необходимые таблицы"""
    conn = await get_connection()
    try:
        # Создаем схему базы данных
        await conn.execute(SCHEMA_SQL)
        logger.info("Database schema initialized")
    finally:
        await conn.close()

# Модели данных
class ActionLog(BaseModel):
    user_id: int
    action_type: str
    action_details: Optional[str] = None

class UserState(BaseModel):
    user_id: int
    current_state: str
    state_data: Optional[Dict[str, Any]] = None

# Маршруты API
@app.post("/log_action")
async def log_action(action: ActionLog):
    """Логирует действие пользователя"""
    conn = await get_connection()
    try:
        await conn.execute("""
            INSERT INTO action_logs (user_id, action_type, action_details)
            VALUES ($1, $2, $3)
        """, action.user_id, action.action_type, action.action_details)
        return {"status": "logged"}
    finally:
        await conn.close()

@app.get("/action_logs/{user_id}")
async def get_action_logs(user_id: int, limit: int = 50):
    """Получает логи действий пользователя"""
    conn = await get_connection()
    try:
        rows = await conn.fetch("""
            SELECT log_id, action_type, action_details, performed_at
            FROM action_logs
            WHERE user_id = $1
            ORDER BY performed_at DESC
            LIMIT $2
        """, user_id, limit)
        
        logs = [
            {
                "log_id": row["log_id"],
                "action_type": row["action_type"],
                "action_details": row["action_details"],
                "performed_at": row["performed_at"].isoformat()
            }
            for row in rows
        ]
        
        return {"logs": logs}
    finally:
        await conn.close()

@app.post("/check_db_connection")
async def check_db_connection():
    """Проверяет подключение к базе данных"""
    try:
        conn = await get_connection()
        await conn.close()
        return {"status": "connected"}
    except Exception as e:
        logger.error(f"Database connection error: {e}")
        raise HTTPException(status_code=500, detail=f"Database connection error: {str(e)}")

@app.get("/tables")
async def get_tables():
    """Получает список таблиц в базе данных"""
    conn = await get_connection()
    try:
        rows = await conn.fetch("""
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'public'
            ORDER BY table_name
        """)
        
        tables = [row["table_name"] for row in rows]
        return {"tables": tables}
    finally:
        await conn.close()

@app.post("/execute_query")
async def execute_query(query: str, params: List[Any] = None):
    """Выполняет произвольный SQL-запрос (только для административных целей)"""
    # ⚠️ Внимание: этот метод может представлять угрозу безопасности,
    # если он доступен публично. Используйте только в защищенной среде
    # или с соответствующей аутентификацией и авторизацией.
    
    conn = await get_connection()
    try:
        result = await conn.fetch(query, *params) if params else await conn.fetch(query)
        return {"rows": [dict(row) for row in result]}
    except Exception as e:
        logger.error(f"Query execution error: {e}")
        raise HTTPException(status_code=500, detail=f"Query execution error: {str(e)}")
    finally:
        await conn.close()

@app.post("/backup")
async def backup_database():
    """Создает резервную копию базы данных"""
    # Эта функция требует доступа к системным командам.
    # В реальном приложении следует использовать более безопасный подход.
    try:
        backup_file = f"/backups/telegram_bot_backup_{int(time.time())}.sql"
        # Заглушка для примера
        return {"status": "backup_created", "file": backup_file}
    except Exception as e:
        logger.error(f"Backup error: {e}")
        raise HTTPException(status_code=500, detail=f"Backup error: {str(e)}")

@app.on_event("startup")
async def startup_event():
    """Выполняется при запуске сервиса"""
    await init_db()
    logger.info("Database Service started")

if __name__ == "__main__":
    import time
    uvicorn.run("db_service:app", host="0.0.0.0", port=8004, reload=True)