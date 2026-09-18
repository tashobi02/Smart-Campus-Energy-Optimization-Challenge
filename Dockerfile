FROM python:3.12-slim

WORKDIR /app

# Production dependencies only (requirements-dev.txt is not copied into the
# image) — layered before the source copy so rebuilds during the round stay
# fast when only application code changes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN useradd --create-home --shell /bin/false appuser \
    && chown -R appuser:appuser /app
USER appuser

ENV PORT=8000
EXPOSE ${PORT}

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python scripts/docker_healthcheck.py

CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
