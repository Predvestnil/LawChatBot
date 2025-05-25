FROM python:3.10-slim

WORKDIR /app

# Установка зависимостей
COPY requirements.auth-service.txt .
RUN pip install --no-cache-dir -r requirements_auth_service.txt

# Копирование кода приложения
COPY auth_service.py .
COPY .env .

# Открываем порт
EXPOSE 8001

# Запускаем приложение
CMD ["python", "auth_service.py"]