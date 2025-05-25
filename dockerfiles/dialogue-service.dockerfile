FROM python:3.10-slim

WORKDIR /app

# Установка зависимостей
COPY requirements.dialogue-service.txt .
RUN pip install --no-cache-dir -r requirements_dialogue_service.txt

# Копирование кода приложения
COPY dialogue_service.py .
COPY .env .

# Открываем порт
EXPOSE 8002

# Запускаем приложение
CMD ["python", "dialogue_service.py"]