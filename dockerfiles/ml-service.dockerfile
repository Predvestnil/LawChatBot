FROM pytorch/pytorch:2.0.1-cuda11.7-cudnn8-runtime

WORKDIR /app

# Установка зависимостей
COPY requirements.ml-service.txt .
RUN pip install --no-cache-dir -r requirements_ml_service.txt

# Копирование кода приложения
COPY ml_service.py .
COPY .env .

# Открываем порт
EXPOSE 8003

# Запускаем приложение
CMD ["python", "ml_service.py"]