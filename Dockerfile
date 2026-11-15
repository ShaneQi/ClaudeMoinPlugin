FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8787

WORKDIR /app
COPY --from=ghcr.io/astral-sh/uv:0.13.8 /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable --no-cache \
    && useradd --system --uid 10001 --create-home app

USER app
EXPOSE 8787
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ['PORT'] + '/health', timeout=3)"

CMD ["/app/.venv/bin/moinmoin-mcp"]
