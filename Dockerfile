# LocalDeploy: Ollama + the API/UI in one image, talking over localhost so the
# server's loopback-only backend guard is preserved. One container, one URL.
# Pinned (not :latest) so a fresh `docker compose build` is reproducible; bump
# deliberately when picking up a newer Ollama release.
FROM ollama/ollama:0.32.1@sha256:6345fbc18bd73a1e16404be681dbc6fd291a027cab43ed541abe78c4c81051b0

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

ENV OLLAMA_NO_CLOUD=true \
    OLLAMA_BASE_URL=http://127.0.0.1:11434 \
    LOCALDEPLOY_HOME=/data/localdeploy \
    API_HOST=0.0.0.0 \
    API_PORT=8000 \
    ENABLE_WEB_UI=true

# 8000 is the API/UI (published by compose). 11434 (Ollama) is internal-only.
EXPOSE 8000

# Override the base image's ollama entrypoint with our combined launcher.
ENTRYPOINT ["/app/scripts/docker-entrypoint.sh"]
