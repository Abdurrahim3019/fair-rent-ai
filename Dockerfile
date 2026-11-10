# Ədalətli Kirayə — Streamlit app
# Build without browser (default, server uses the pre-seeded DB):
#   docker build -t edaletli-kiraye .
# Build with Chromium for live scraping in the container:
#   docker build --build-arg WITH_CHROMIUM=1 -t edaletli-kiraye .

FROM python:3.11-slim

ARG WITH_CHROMIUM=0
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    STREAMLIT_SERVER_FILE_WATCHER_TYPE=none

RUN if [ "$WITH_CHROMIUM" = "1" ]; then \
        apt-get update && apt-get install -y --no-install-recommends chromium chromium-driver fonts-dejavu-core \
        && rm -rf /var/lib/apt/lists/*; \
    fi
ENV CHROME_BIN=/usr/bin/chromium \
    CHROMEDRIVER_PATH=/usr/bin/chromedriver

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app.py ./
COPY src ./src
COPY scripts ./scripts
COPY .streamlit ./.streamlit

RUN useradd --create-home --uid 1000 app && mkdir -p /app/data && chown -R app:app /app
USER app

EXPOSE 8501
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8501/_stcore/health', timeout=4).status == 200 else 1)"

CMD ["streamlit", "run", "app.py", "--server.port=8501", "--server.address=0.0.0.0", "--server.headless=true"]
