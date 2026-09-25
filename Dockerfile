# YouTrained web app. The SQLite database lives on a mounted volume at /data.
FROM python:3.12-slim-bookworm

# WeasyPrint (PDF) needs pango; DejaVu gives the OG image and PDF a real font.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz0b libffi8 fonts-dejavu-core sqlite3 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/opt/venv

WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev --no-install-project --extra pdf
COPY src ./src
COPY README.md ./
RUN uv sync --frozen --no-dev --extra pdf

ENV PATH="/opt/venv/bin:$PATH" \
    YOUTRAINED_DB=/data/youtrained.sqlite \
    YOUTRAINED_CACHE_DIR=/data/cache \
    PORT=8080
VOLUME ["/data"]
EXPOSE 8080
CMD ["sh", "-c", "youtrained serve --host 0.0.0.0 --port ${PORT}"]
