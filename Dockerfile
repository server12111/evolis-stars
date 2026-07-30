FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV DATABASE_URL=sqlite+aiosqlite:////app/data/bot.db
ENV DATA_DIR=/app/data
ENV INSTANCE_LOCK_PATH=/app/data/bot-instance.lock

VOLUME ["/app/data"]

ENTRYPOINT ["sh", "/app/docker-entrypoint.sh"]
CMD ["python", "run.py"]
