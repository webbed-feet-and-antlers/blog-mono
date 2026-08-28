# Study app — one image, one origin: uvicorn serves the API and the built
# SPA (frontend/dist). Build from the study-app/ directory:
#   docker build -t study-app --build-arg VITE_CLERK_PUBLISHABLE_KEY=pk_… .

# --- Stage 1: build the SPA --------------------------------------------------
FROM node:22-slim AS frontend

# The publishable key is public by design and baked into the bundle.
ARG VITE_CLERK_PUBLISHABLE_KEY
ENV VITE_CLERK_PUBLISHABLE_KEY=$VITE_CLERK_PUBLISHABLE_KEY

WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# --- Stage 2: the app --------------------------------------------------------
FROM python:3.13-slim

# System deps: LibreOffice headless (office→PDF conversion in parsers.py),
# ffmpeg (audio chunking in transcription.py), curl (Fly health checks).
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libreoffice ffmpeg curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Python deps first (cached layer).
WORKDIR /app/study-app/backend
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev

# App code, then the built SPA where app/main.py expects it
# (frontend_dist_dir = backend/../frontend/dist).
COPY backend/app ./app
COPY --from=frontend /build/dist /app/study-app/frontend/dist

# Persistent state lives on the Fly volume mounted at /data (mkdir'd here
# so the image also runs standalone without a volume).
RUN mkdir -p /data
ENV PYTHONUNBUFFERED=1 \
    DB_PATH=/data/study_app.db \
    STORAGE_DIR=/data/storage

EXPOSE 8000
CMD [".venv/bin/uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
