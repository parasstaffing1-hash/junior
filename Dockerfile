FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY frontend ./frontend
COPY migrations ./migrations
COPY alembic.ini ./alembic.ini

RUN mkdir -p /app/storage

EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && exec python -m uvicorn app.main:app --host 0.0.0.0 --port 8000"]
