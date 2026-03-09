FROM python:3.12-slim

WORKDIR /app

# Install dependencies
COPY pyproject.toml .
RUN pip install --no-cache-dir \
    websockets \
    asyncpg \
    rich \
    prometheus-client \
    python-dotenv \
    aiohttp

# Copy source
COPY src/ ./src/
COPY config/ ./config/

# Non-root user
RUN useradd -m -u 1000 monitor && chown -R monitor:monitor /app
USER monitor

EXPOSE 8000

CMD ["/bin/sh", "-c", "echo B | python3 -m src.main"]
