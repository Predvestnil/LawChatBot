FROM python:3.10-slim

WORKDIR /app

# Установка зависимостей
COPY requirements.db-service.txt .
RUN pip install --no-cache-dir -r requirements_db_service.txt

# Копирование кода приложения
COPY db_service.py .
COPY .env .

# Открываем порт
EXPOSE 8004

# Запускаем приложение
CMD ["python", "db_service.py"]