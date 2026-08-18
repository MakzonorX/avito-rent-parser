"""
Запуск веб-админки парсера аренды: python run_web.py
Интерфейс: http://localhost:9999
"""
import sys

import uvicorn
from loguru import logger

HOST = "127.0.0.1"
PORT = 9999

if __name__ == "__main__":
    host = HOST
    # --public открывает доступ из сети (для сервера)
    if "--public" in sys.argv:
        host = "0.0.0.0"

    logger.info(f"Открой в браузере: http://localhost:{PORT}")
    uvicorn.run("webapp.main:app", host=host, port=PORT, log_level="warning")
