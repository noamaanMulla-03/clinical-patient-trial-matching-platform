FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/workspace

WORKDIR /workspace

COPY requirements.txt requirements-dev.txt /tmp/
# The worker runs local semantic embeddings on CPU. Installing torch from its
# CPU-only index avoids carrying CUDA libraries in the research-demo image.
RUN python -m pip install --no-cache-dir \
    --index-url https://download.pytorch.org/whl/cpu \
    'torch==2.7.1'
RUN python -m pip install --no-cache-dir --requirement /tmp/requirements.txt

COPY alembic.ini ./
COPY migrations ./migrations
COPY app ./app

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
