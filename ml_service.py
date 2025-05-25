"""
ML Service - сервис для работы с ИИ-моделью
Обрабатывает запросы на генерацию ответов и управляет контекстом диалога
"""

import os
import logging
import json
from typing import Dict, Any, List, Optional
import time

import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import torch
from transformers import AutoModelForCausalLM, AutoTokenizer
from dotenv import load_dotenv

load_dotenv()

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Загрузка переменных окружения
MODEL_NAME = os.getenv("MODEL_NAME", "meta-llama/Llama-2-7b-chat-hf")
DEVICE = "cuda" if torch.cuda.is_available() else "cpu"

app = FastAPI(title="ML Service")

# Глобальные переменные для модели и токенизатора
model = None
tokenizer = None

# Модели данных
class GenerateRequest(BaseModel):
    user_id: int
    message: str
    context: List[Dict[str, str]] = []

class GenerateResponse(BaseModel):
    response: str
    tokens_used: int
    generation_time: float

# Функции для работы с моделью
def format_context(context: List[Dict[str, str]]) -> str:
    """Форматирует контекст диалога для передачи в модель"""
    formatted = ""
    for item in context:
        role = item["role"]
        content = item["content"]
        if role == "user":
            formatted += f"<|user|>\n{content}\n"
        elif role == "assistant":
            formatted += f"<|assistant|>\n{content}\n"
    return formatted + "<|assistant|>\n"

def initialize_model():
    """Инициализирует модель и токенизатор"""
    global model, tokenizer
    
    try:
        logger.info(f"Loading model: {MODEL_NAME}")
        tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)
        model = AutoModelForCausalLM.from_pretrained(
            MODEL_NAME,
            torch_dtype=torch.float16 if DEVICE == "cuda" else torch.float32,
            device_map="auto" if DEVICE == "cuda" else None,
            low_cpu_mem_usage=True
        )
        logger.info(f"Model loaded successfully on {DEVICE}")
    except Exception as e:
        logger.error(f"Failed to load model: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to load model: {str(e)}")

def generate_response(prompt: str, max_length: int = 1024) -> tuple:
    """Генерирует ответ на основе промпта"""
    start_time = time.time()
    
    try:
        inputs = tokenizer(prompt, return_tensors="pt")
        input_ids = inputs.input_ids.to(DEVICE)
        
        # Генерация ответа
        with torch.no_grad():
            generated_ids = model.generate(
                input_ids,
                max_length=max_length,
                num_return_sequences=1,
                do_sample=True,
                temperature=0.7,
                top_p=0.9,
                pad_token_id=tokenizer.eos_token_id
            )
        
        # Декодирование ответа
        generated_text = tokenizer.decode(generated_ids[0], skip_special_tokens=True)
        
        # Извлечение только сгенерированного ответа (удаляем промпт)
        response = generated_text[len(tokenizer.decode(input_ids[0], skip_special_tokens=True)):]
        
        tokens_used = len(generated_ids[0])
        generation_time = time.time() - start_time
        
        return response, tokens_used, generation_time
    except Exception as e:
        logger.error(f"Error generating response: {e}")
        return "Извините, произошла ошибка при генерации ответа.", 0, time.time() - start_time

# Маршруты API
@app.post("/generate", response_model=GenerateResponse)
async def generate(request: GenerateRequest):
    """Генерирует ответ на сообщение пользователя"""
    # Проверяем, загружена ли модель
    if model is None or tokenizer is None:
        initialize_model()
    
    # Форматируем контекст и сообщение пользователя
    prompt = format_context(request.context)
    
    # Генерируем ответ
    response, tokens_used, generation_time = generate_response(prompt)
    
    logger.info(f"Generated response for user {request.user_id} in {generation_time:.2f}s, used {tokens_used} tokens")
    
    return {
        "response": response.strip(),
        "tokens_used": tokens_used,
        "generation_time": generation_time
    }

@app.get("/health")
async def health_check():
    """Проверяет состояние сервиса"""
    # Проверяем, загружена ли модель
    try:
        if model is None or tokenizer is None:
            return {
                "status": "initializing",
                "model": MODEL_NAME,
                "device": DEVICE
            }
        else:
            return {
                "status": "ready",
                "model": MODEL_NAME,
                "device": DEVICE
            }
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        return {
            "status": "error",
            "model": MODEL_NAME,
            "device": DEVICE,
            "error": str(e)
        }

@app.on_event("startup")
async def startup_event():
    """Выполняется при запуске сервиса"""
    try:
        # Инициализируем модель при запуске сервиса
        initialize_model()
        logger.info("ML Service started successfully")
    except Exception as e:
        logger.error(f"ML Service startup failed: {e}")

if __name__ == "__main__":
    uvicorn.run("ml_service:app", host="0.0.0.0", port=8003, reload=True)