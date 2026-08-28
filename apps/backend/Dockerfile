FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/workspace \
    HF_HOME=/opt/huggingface

WORKDIR /workspace

COPY requirements.txt requirements-dev.txt /tmp/
# The worker runs local semantic embeddings on CPU. Installing torch from its
# CPU-only index avoids carrying CUDA libraries in the research-demo image.
RUN python -m pip install --no-cache-dir \
    --index-url https://download.pytorch.org/whl/cpu \
    'torch==2.7.1'
RUN python -m pip install --no-cache-dir --requirement /tmp/requirements.txt
# The runtime deliberately uses local_files_only=True so patient-derived query
# text never triggers a model download. Provision the pinned public model here
# so a clean Compose deployment can either run semantic search or report a real
# embedding-job failure, never silently depend on a developer cache.
RUN python -c "from huggingface_hub import snapshot_download; snapshot_download('NeuML/pubmedbert-base-embeddings', revision='b79526d6ef3645e0df4530322e266f24c829f5ef')"

COPY alembic.ini ./
COPY migrations ./migrations
COPY src ./src

EXPOSE 8000

CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
