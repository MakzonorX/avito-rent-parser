"""
Получение cookies Avito: автоматически через headless-браузер либо вручную
(вставкой строки cookies из DevTools браузера).
"""
import asyncio
import json
import os
import random
import threading
import time
from pathlib import Path

from loguru import logger

COOKIES_PATH = Path("storage/own_cookies.json")
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
)

_state = {"running": False, "result": None, "error": None}


def status() -> dict:
    info = {
        "exists": COOKIES_PATH.exists(),
        "count": 0,
        "saved_at": None,
        "age_hours": None,
        "has_ft": False,
        "running": _state["running"],
        "error": _state["error"],
        "result": _state["result"],
    }
    if not COOKIES_PATH.exists():
        return info
    try:
        data = json.loads(COOKIES_PATH.read_text(encoding="utf-8"))
        cookies = data.get("cookies") or {}
        info["count"] = len(cookies)
        info["has_ft"] = "ft" in cookies
        saved_at = data.get("saved_at")
        if saved_at:
            info["saved_at"] = saved_at
            info["age_hours"] = round((time.time() - float(saved_at)) / 3600, 1)
    except (OSError, ValueError) as err:
        logger.warning(f"Не удалось прочитать cookies: {err}")
    return info


def save_cookies(cookies: dict) -> int:
    COOKIES_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {"cookies": cookies, "saved_at": time.time(), "cookie_count": len(cookies)}
    temp_path = COOKIES_PATH.with_suffix(".tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp_path.replace(COOKIES_PATH)
    logger.info(f"💾 Сохранено cookies: {len(cookies)}")
    return len(cookies)


def parse_cookie_string(raw: str) -> dict:
    """Разбирает строку вида 'a=1; b=2' (как в document.cookie / заголовке Cookie)."""
    cookies = {}
    for part in raw.replace("\n", ";").split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        name, value = part.split("=", 1)
        name, value = name.strip(), value.strip()
        if name:
            cookies[name] = value
    return cookies


def import_from_string(raw: str) -> int:
    cookies = parse_cookie_string(raw)
    if not cookies:
        raise ValueError("Не удалось разобрать строку cookies")
    return save_cookies(cookies)


async def _grab_cookies(headless: bool = True, timeout_sec: int = 60) -> dict:
    # оригинальный ensure_playwright_installed подставляет windows-путь, на Linux он не нужен
    os.environ.pop("PLAYWRIGHT_BROWSERS_PATH", None)
    from playwright.async_api import async_playwright

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            headless=headless,
            chromium_sandbox=False,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        )
        try:
            context = await browser.new_context(
                user_agent=USER_AGENT,
                viewport={"width": 1920, "height": 1080},
                locale="ru-RU",
            )
            page = await context.new_page()
            await page.add_init_script(
                "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
                "window.chrome={runtime:{}};"
                "Object.defineProperty(navigator,'languages',{get:()=>['ru-RU','ru']});"
            )
            url = f"https://www.avito.ru/{random.randint(1111111111, 9999999999)}"
            await page.goto(url, timeout=60_000, wait_until="domcontentloaded")

            deadline = time.time() + timeout_sec
            while time.time() < deadline:
                title = await page.title()
                raw = await page.evaluate("() => document.cookie")
                cookies = parse_cookie_string(raw)
                logger.info(f"Страница: {title!r}, cookies: {len(cookies)}")
                if "проблема с ip" in title.lower():
                    raise RuntimeError(
                        "Avito блокирует этот IP-адрес. Нужен российский IP: "
                        "отключите VPN либо укажите российский прокси в настройках."
                    )
                if cookies.get("ft"):
                    return cookies
                await asyncio.sleep(4)

            raise RuntimeError("Не дождались cookie 'ft' от Avito")
        finally:
            await browser.close()


def refresh_async(headless: bool = True) -> bool:
    """Запускает получение cookies в фоне. Возвращает False, если уже идёт."""
    if _state["running"]:
        return False

    def _worker():
        _state.update(running=True, error=None, result=None)
        try:
            cookies = asyncio.run(_grab_cookies(headless=headless))
            count = save_cookies(cookies)
            _state["result"] = f"Получено {count} cookies"
        except Exception as err:
            _state["error"] = str(err)
            logger.error(f"Не удалось получить cookies: {err}")
        finally:
            _state["running"] = False

    threading.Thread(target=_worker, name="cookies-refresh", daemon=True).start()
    return True
