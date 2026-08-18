FROM python:3.12-slim

# Библиотеки, нужные Chromium (используется для получения cookies Avito)
RUN apt-get update && apt-get install -y --no-install-recommends \
        libatk-bridge2.0-0 \
        libatk1.0-0 \
        libatspi2.0-0 \
        libcairo2 \
        libdbus-1-3 \
        libdrm2 \
        libgbm1 \
        libglib2.0-0 \
        libnspr4 \
        libnss3 \
        libpango-1.0-0 \
        libxcomposite1 \
        libxdamage1 \
        libxfixes3 \
        libxrandr2 \
        libxkbcommon0 \
        libasound2 \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir --upgrade pip && pip install --no-cache-dir -r requirements.txt
RUN python -m playwright install chromium-headless-shell

COPY . /app

# Всё, что приложение записывает (config.toml, cookies, базы, логи), лежит в /app/data.
# Рабочая директория = /app/data, поэтому относительные пути пишутся именно туда.
ENV PYTHONPATH=/app \
    PYTHONUNBUFFERED=1
RUN mkdir -p /app/data
WORKDIR /app/data

EXPOSE 9999
CMD ["python", "/app/run_web.py", "--public"]
