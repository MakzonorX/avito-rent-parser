"""
Веб-админка парсера Avito. Запуск: python run_web.py  → http://localhost:9999
"""
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from loguru import logger

from integrations.notifications.telegram_rent import RentTelegramNotifier
from integrations.notifications.utils import escape_markdown_v2
from webapp import browser_cookies, cookies_tool, diagnostics, logbuf, settings, store
from webapp.runner import runner

BASE_DIR = Path(__file__).parent

app = FastAPI(title="Avito Rent Parser", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")


@app.on_event("startup")
def on_startup() -> None:
    store.init()
    logbuf.install()
    logger.info("Веб-интерфейс запущен на http://localhost:9999")


@app.get("/")
def index(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")


# ---------------------------------------------------------------- статус

@app.get("/api/status")
def api_status():
    return {
        "runner": runner.status(),
        "stats": store.stats(),
        "cookies": cookies_tool.status(),
    }


@app.post("/api/start")
def api_start():
    started = runner.start()
    return {"ok": started, "message": "Парсер запущен" if started else "Парсер уже работает"}


@app.post("/api/stop")
def api_stop():
    stopped = runner.stop()
    return {"ok": stopped, "message": "Останавливаю..." if stopped else "Парсер не запущен"}


@app.post("/api/check-now")
def api_check_now():
    started = runner.run_once()
    return {
        "ok": started,
        "message": "Проверяю..." if started else "Парсер уже работает, разовая проверка не нужна",
    }


# ---------------------------------------------------------------- настройки

@app.get("/api/settings")
def api_get_settings():
    raw = settings.load_raw()
    return {
        "avito": raw["avito"],
        "rent": raw["rent"],
        "app": raw["app"],
        "presets": settings.PRESETS,
    }


@app.post("/api/settings")
async def api_save_settings(request: Request):
    payload = await request.json()
    try:
        settings.save(
            avito=payload.get("avito") or {},
            rent=payload.get("rent") or {},
            app=payload.get("app") or {},
        )
    except Exception as err:
        logger.error(f"Ошибка сохранения настроек: {err}")
        return JSONResponse({"ok": False, "message": str(err)}, status_code=400)
    return {"ok": True, "message": "Настройки сохранены"}


# ---------------------------------------------------------------- объявления

@app.get("/api/ads")
def api_ads(limit: int = 30, offset: int = 0, search: str = ""):
    return {"items": store.list_ads(limit=limit, offset=offset, search=search)}


@app.post("/api/ads/clear")
def api_ads_clear():
    store.clear()
    return {"ok": True, "message": "История очищена"}


# ---------------------------------------------------------------- логи

@app.get("/api/logs")
def api_logs(after: int = 0):
    return {"items": logbuf.tail(after=after), "last_seq": logbuf.last_seq()}


@app.post("/api/logs/clear")
def api_logs_clear():
    logbuf.clear()
    return {"ok": True}


# ---------------------------------------------------------------- telegram

@app.post("/api/telegram/test")
def api_telegram_test():
    avito_config, rent_config = settings.load()
    if not avito_config.tg_token or not avito_config.tg_chat_id:
        return JSONResponse(
            {"ok": False, "message": "Укажите токен бота и chat_id в настройках"},
            status_code=400,
        )

    text = escape_markdown_v2(
        "✅ Проверка связи. Парсер аренды квартир в Тюмени подключён к этому чату."
    )
    errors = []
    for chat_id in avito_config.tg_chat_id:
        notifier = RentTelegramNotifier(
            bot_token=avito_config.tg_token,
            chat_id=chat_id,
            rent_config=rent_config,
            proxy=avito_config.proxy_notifier or None,
        )
        try:
            notifier.notify(message=text)
        except Exception as err:
            errors.append(f"{chat_id}: {err}")

    if errors:
        return JSONResponse(
            {"ok": False, "message": "Не отправилось — " + "; ".join(errors)},
            status_code=400,
        )
    return {"ok": True, "message": f"Отправлено в {len(avito_config.tg_chat_id)} чат(ов)"}


# ---------------------------------------------------------------- cookies

@app.post("/api/cookies/refresh")
def api_cookies_refresh():
    started = cookies_tool.refresh_async()
    return {
        "ok": started,
        "message": "Открываю браузер и получаю cookies..." if started else "Уже выполняется",
    }


@app.post("/api/cookies/import")
async def api_cookies_import(request: Request):
    payload = await request.json()
    try:
        count = cookies_tool.import_from_string(
            payload.get("cookies", ""), user_agent=payload.get("user_agent", "")
        )
    except ValueError as err:
        return JSONResponse({"ok": False, "message": str(err)}, status_code=400)
    _enable_own_cookies()
    return {"ok": True, "message": f"Импортировано {count} cookies"}


def _enable_own_cookies() -> None:
    """Импортировали cookies — значит их надо использовать."""
    raw = settings.load_raw()
    if not raw["avito"].get("use_own_cookies"):
        settings.save(avito={"use_own_cookies": True}, rent={})
        logger.info("Включено использование сохранённых cookies")


@app.get("/api/cookies/browsers")
def api_cookies_browsers():
    """Список профилей браузеров с числом найденных cookies Avito."""
    try:
        return {"ok": True, "profiles": browser_cookies.scan()}
    except Exception as err:
        logger.error(f"Не удалось просмотреть браузеры: {err}")
        return JSONResponse({"ok": False, "message": str(err), "profiles": []}, status_code=500)


@app.post("/api/cookies/from-browser")
async def api_cookies_from_browser(request: Request):
    """
    Забирает cookies Avito прямо из браузера пользователя.
    User-Agent приходит со страницы админки — это тот же браузер,
    поэтому отпечаток совпадёт с тем, которому Avito выдал cookies.
    """
    payload = await request.json() if await request.body() else {}
    try:
        result = browser_cookies.import_from(payload.get("profile_key", ""))
    except Exception as err:
        return JSONResponse({"ok": False, "message": str(err)}, status_code=400)

    cookies = result["cookies"]
    count = cookies_tool.save_cookies(cookies, user_agent=payload.get("user_agent", ""))
    _enable_own_cookies()

    has_ft = "ft" in cookies
    message = (
        f"Взято {count} cookies из «{result['browser']} / {result['profile']}»"
        + (", ключевая ft на месте" if has_ft else ", но ключевой ft нет — открой avito.ru в браузере")
    )
    return {"ok": True, "message": message, "count": count, "has_ft": has_ft}


# ---------------------------------------------------------------- мастер настройки

@app.post("/api/check-avito")
async def api_check_avito(request: Request):
    """Проверяет, пускает ли Avito с текущего IP (или через указанный прокси)."""
    payload = await request.json() if await request.body() else {}
    proxy = payload.get("proxy_string")
    if proxy is None:
        avito_config, _ = settings.load()
        proxy = avito_config.proxy_string or ""
    return diagnostics.check_access(proxy_string=proxy)


@app.post("/api/onboarding/complete")
def api_onboarding_complete():
    settings.save(avito={}, rent={}, app={"onboarding_done": True})
    return {"ok": True}


@app.post("/api/onboarding/reset")
def api_onboarding_reset():
    settings.save(avito={}, rent={}, app={"onboarding_done": False})
    return {"ok": True}
