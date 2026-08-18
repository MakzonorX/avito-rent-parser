"""
Получение cookies Avito: автоматически через headless-браузер либо вручную
(вставкой строки cookies из DevTools браузера).

Важно: Avito привязывает cookie `ft` к отпечатку браузера, поэтому вместе с
cookies сохраняется и User-Agent, и профиль для curl_cffi. Если отправлять
чужие cookies с другим User-Agent, Avito отвечает 429/403.
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

# Playwright ставит Chromium 136, в curl_cffi есть ровно такой профиль —
# держим версию согласованной во всей цепочке.
CHROME_VERSION = "136"
IMPERSONATE = "chrome136"
USER_AGENT = (
    f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    f"(KHTML, like Gecko) Chrome/{CHROME_VERSION}.0.0.0 Safari/537.36"
)
CLIENT_HINTS = {
    "user-agent": USER_AGENT,
    "sec-ch-ua": f'"Chromium";v="{CHROME_VERSION}", "Google Chrome";v="{CHROME_VERSION}", "Not.A/Brand";v="99"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "accept-language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
}

_state = {"running": False, "result": None, "error": None}


def fingerprint() -> dict:
    """Профиль браузера, с которым были получены cookies."""
    return {"impersonate": IMPERSONATE, "headers": dict(CLIENT_HINTS)}


def status() -> dict:
    info = {
        "exists": COOKIES_PATH.exists(),
        "count": 0,
        "saved_at": None,
        "age_hours": None,
        "has_ft": False,
        "user_agent": None,
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
        info["user_agent"] = data.get("user_agent")
        saved_at = data.get("saved_at")
        if saved_at:
            info["saved_at"] = saved_at
            info["age_hours"] = round((time.time() - float(saved_at)) / 3600, 1)
    except (OSError, ValueError) as err:
        logger.warning(f"Не удалось прочитать cookies: {err}")
    return info


def save_cookies(cookies: dict, user_agent: str = USER_AGENT) -> int:
    COOKIES_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "cookies": cookies,
        "user_agent": user_agent,
        "fingerprint": fingerprint(),
        "saved_at": time.time(),
        "cookie_count": len(cookies),
    }
    temp_path = COOKIES_PATH.with_suffix(".tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp_path.replace(COOKIES_PATH)
    logger.info(f"💾 Сохранено cookies: {len(cookies)} (ft: {'да' if 'ft' in cookies else 'нет'})")
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


def import_from_string(raw: str, user_agent: str = "") -> int:
    cookies = parse_cookie_string(raw)
    if not cookies:
        raise ValueError("Не удалось разобрать строку cookies")
    return save_cookies(cookies, user_agent=user_agent.strip() or USER_AGENT)


async def _grab_cookies(headless: bool = True, timeout_sec: int = 90) -> dict:
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
                timezone_id="Asia/Yekaterinburg",
                extra_http_headers={"accept-language": CLIENT_HINTS["accept-language"]},
            )
            page = await context.new_page()
            await page.add_init_script(
                "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
                "window.chrome={runtime:{}};"
                "Object.defineProperty(navigator,'languages',{get:()=>['ru-RU','ru']});"
                "Object.defineProperty(navigator,'plugins',{get:()=>[1,2,3,4,5]});"
            )

            # Сначала главная — на ней выдаются базовые cookies и решается челлендж
            await page.goto("https://www.avito.ru/", timeout=60_000, wait_until="domcontentloaded")

            deadline = time.time() + timeout_sec
            visited_ad = False
            while time.time() < deadline:
                title = await page.title()
                # context.cookies() отдаёт и HttpOnly-куки, document.cookie их не видит
                jar = {c["name"]: c["value"] for c in await context.cookies()}
                logger.info(f"Страница: {title!r}, cookies: {len(jar)} ({', '.join(list(jar)[:6])})")

                if "проблема с ip" in title.lower():
                    raise RuntimeError(
                        "Avito блокирует этот IP-адрес. Нужен российский IP: "
                        "отключите VPN либо укажите российский прокси в настройках."
                    )

                if jar.get("ft"):
                    logger.info("Получена ключевая cookie ft")
                    return jar

                if not visited_ad and len(jar) > 2:
                    # заход на карточку объявления обычно и выдаёт ft
                    visited_ad = True
                    await page.goto(
                        f"https://www.avito.ru/{random.randint(1111111111, 9999999999)}",
                        timeout=60_000,
                        wait_until="domcontentloaded",
                    )
                    continue

                await asyncio.sleep(4)

            # ft не дождались — отдаём что есть, вдруг хватит
            jar = {c["name"]: c["value"] for c in await context.cookies()}
            if jar:
                logger.warning(f"Cookie ft не появилась, сохраняю что есть ({len(jar)} шт.)")
                return jar
            raise RuntimeError("Avito не выдал ни одной cookie")
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
