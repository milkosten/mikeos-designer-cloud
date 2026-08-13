FROM python:3.12-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY server/ ./server/
COPY migrations/ ./migrations/
# The prompt files under harness/prompts ARE the design brain — ship them in the image.
COPY harness/ ./harness/
EXPOSE 8000
# One page = one serialized GPU call (~60-90s). Keep a couple of workers so a slow
# generation on one request doesn't block health/meta/list.
CMD ["uvicorn", "server.http_server:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
