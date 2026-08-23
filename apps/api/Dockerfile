FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/workspace/apps/api

WORKDIR /workspace

COPY apps/api/requirements.txt apps/api/requirements-dev.txt /tmp/
RUN python -m pip install --no-cache-dir --requirement /tmp/requirements.txt

COPY alembic.ini ./
COPY migrations ./migrations
COPY apps/api ./apps/api

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
