FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DATA_DIR=/data HOST=0.0.0.0 PORT=8080
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
VOLUME /data
EXPOSE 8080
# One worker only: Telegram clients live in process memory.
CMD ["sh", "-c", "uvicorn app.main:app --host $HOST --port $PORT --proxy-headers --forwarded-allow-ips='*'"]
