# syntax=docker/dockerfile:1.7
# Multi-stage backend image shared by `api`, `worker` and the `tests` service.
# Build context is the repository root (so the test stage can copy tests/).
ARG PYTHON_IMAGE=python:3.11-slim-bookworm@sha256:94a8fee35252d06ab75856f15f12c7bae67b10e8a6da93c7fded82b2610926cd

FROM ${PYTHON_IMAGE} AS base
ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONSAFEPATH=1
RUN apt-get update \
    && apt-get install -y --no-install-recommends fonts-dejavu-core fonts-noto-cjk libgl1 libglib2.0-0 libgomp1 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd -g 10001 app && useradd -r -u 10001 -g app -d /nonexistent -s /usr/sbin/nologin app \
    && mkdir -p /data /models /state /tmp/manga-translator \
    && chown -R app:app /data /models /state /tmp/manga-translator

FROM base AS deps
# OCR_EXTRAS=full installs PaddleOCR(onnxruntime)+manga-ocr(torch CPU); "none" keeps the image small (tests).
ARG OCR_EXTRAS=full
ARG TORCH_INDEX_URL=https://download.pytorch.org/whl/cpu
# COMPAT_SME_VM=1 applies requirements-compat-sme-vm.txt (see that file for why).
ARG COMPAT_SME_VM=0
COPY backend/requirements.txt backend/requirements-ocr.txt backend/requirements-compat-sme-vm.txt /tmp/req/
RUN pip install -r /tmp/req/requirements.txt \
    && if [ "$OCR_EXTRAS" = "full" ]; then \
         pip install --index-url "$TORCH_INDEX_URL" --extra-index-url https://pypi.org/simple "$(grep '^torch==' /tmp/req/requirements-ocr.txt)" \
         && pip install -r /tmp/req/requirements-ocr.txt ; \
       fi \
    && if [ "$COMPAT_SME_VM" = "1" ]; then \
         if [ "$OCR_EXTRAS" = "full" ]; then pip install -r /tmp/req/requirements-compat-sme-vm.txt; \
         else pip install "$(grep '^cryptography==' /tmp/req/requirements-compat-sme-vm.txt)"; fi; \
       fi \
    && rm -rf /tmp/req

FROM deps AS runtime
WORKDIR /app
COPY backend/fonts/patrickhand /usr/share/fonts/truetype/patrickhand
COPY --chown=app:app backend/app /app/app
ENV PYTHONPATH=/app \
    MT_DATA_DIR=/data \
    MT_MODELS_DIR=/models \
    MT_WORKER_HEARTBEAT_FILE=/state/worker.heartbeat \
    PADDLE_PDX_CACHE_HOME=/models/paddlex \
    HF_HOME=/models/huggingface \
    HF_HUB_DISABLE_TELEMETRY=1 \
    HOME=/tmp/manga-translator
USER app:app
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-server-header"]

FROM runtime AS test
USER root
COPY backend/requirements-test.txt /tmp/requirements-test.txt
RUN pip install -r /tmp/requirements-test.txt && rm /tmp/requirements-test.txt
COPY --chown=app:app backend/pyproject.toml /app/pyproject.toml
COPY --chown=app:app tests /app/tests
USER app:app
ENV MT_TEST_MODE=1
WORKDIR /app
CMD ["python", "-m", "pytest", "tests", "-q", "-p", "no:cacheprovider"]
