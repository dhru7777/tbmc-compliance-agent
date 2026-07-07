# Build from repo root (Railway root railway.toml). Context = monorepo root.
FROM python:3.12-slim

WORKDIR /app

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ .
# Riverstone trial package lives outside backend/ — required at /agent-skill/mock documents
COPY agent-skill/mock\ documents /agent-skill/mock\ documents

ARG GIT_COMMIT=dev
RUN echo "${GIT_COMMIT}" > DEPLOY_SHA.txt

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}"]
