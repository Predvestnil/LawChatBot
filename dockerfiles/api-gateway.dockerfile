FROM python:3.10-slim

WORKDIR /app

# Установка зависимостей
COPY requirements.api-gateway.txt .
RUN pip install --no-cache-dir -r requirements_api_gateway.txt

# Копирование кода приложения
COPY api_gateway.py .
COPY .env .

# Открываем порт
EXPOSE 8000

# Запускаем приложение
CMD ["python", "api_gateway.py"]