# LocalDeploy: Ollama + the API/UI in one image, talking over localhost so the
# server's loopback-only backend guard is preserved. One container, one URL.
FROM ollama/ollama:latest

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-venv python3-pip ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Isolated virtualenv so we never fight the system Python (PEP 668).
ENV VENV=/opt/venv
RUN python3 -m venv "$VENV"
ENV PATH="$VENV/bin:$PATH"

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir --upgrade pip && pip install --no-cache-dir -r requirements.txt

COPY . .
RUN chmod +x scripts/docker-entrypoint.sh

ENV OLLAMA_BASE_URL=http://localhost:11434 \
    API_HOST=0.0.0.0 \
    API_PORT=8000 \
    ENABLE_WEB_UI=true

EXPOSE 8000 11434

# Override the base image's ollama entrypoint with our combined launcher.
ENTRYPOINT ["/app/scripts/docker-entrypoint.sh"]
