"""
Запросы к Avito через настоящий браузер.

Когда обычный http-клиент получает 429/403, а браузер ту же страницу открывает
без вопросов, единственный надёжный путь — сходить браузером. Класс повторяет
интерфейс HttpClient (request → объект с .status_code/.text/.json()), поэтому
подставляется в парсер вместо обычного клиента без правок движка.
"""
import json
import threading
from pathlib import Path

from loguru import logger

from proxy_utils import normalize_proxy

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
)

# fetch() из контекста страницы avito.ru: запрос уходит со всеми cookies,
# правильным Origin/Referer и настоящим отпечатком браузера
_FETCH_JS = """
async (url) => {
    try {
        const response = await fetch(url, {
            method: 'GET',
            credentials: 'include',
            headers: {'Accept': 'application/json, text/plain, */*'},
        });
        return {status: response.status, body: await response.text()};
    } catch (error) {
        return {status: 0, body: String(error)};
    }
}
"""


class BrowserResponse:
    """Минимальная замена ответа requests."""

    def __init__(self, status_code: int, text: str, url: str):
        self.status_code = status_code
        self.text = text
        self.url = url
        self.cookies = {}

    def json(self):
        return json.loads(self.text)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"Браузер получил код {self.status_code} для {self.url}")


def split_proxy(proxy_string: str) -> dict | None:
    """Разбирает строку прокси в формат, который понимает Playwright."""
    proxy = normalize_proxy(proxy_string)
    if not proxy:
        return None

    scheme = "http"
    if "://" in proxy:
        scheme, proxy = proxy.split("://", 1)
        scheme = "socks5" if scheme.startswith("socks5") else scheme

    username = password = None
    if "@" in proxy:
        credentials, proxy = proxy.split("@", 1)
        username, _, password = credentials.partition(":")

    result = {"server": f"{scheme}://{proxy}"}
    if username:
        result["username"] = username
        result["password"] = password
    return result


class BrowserHttpClient:
    """Держит один браузер на цикл парсинга и ходит им по API."""

    def __init__(
        self,
        proxy_string: str = "",
        headless: bool = True,
        timeout: int = 60,
        on_cookies=None,
        profile_dir: str = "storage/browser_profile",
    ):
        self.proxy_string = proxy_string
        self.headless = headless
        self.timeout = timeout
        self.on_cookies = on_cookies
        self.profile_dir = Path(profile_dir)
        self._lock = threading.Lock()
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None

    # ---------- жизненный цикл браузера ----------

    def _ensure_browser(self) -> None:
        if self._page is not None:
            return

        import os
        os.environ.pop("PLAYWRIGHT_BROWSERS_PATH", None)
        from playwright.sync_api import sync_playwright

        logger.info("Запускаю браузер для запросов к Avito")
        self._playwright = sync_playwright().start()

        # Постоянный профиль: cookies и решённые челленджи сохраняются между
        # циклами, поэтому Avito видит вернувшегося пользователя, а не новичка
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        launch_args = {
            "user_data_dir": str(self.profile_dir),
            "headless": self.headless,
            "chromium_sandbox": False,
            "user_agent": USER_AGENT,
            "viewport": {"width": 1920, "height": 1080},
            "locale": "ru-RU",
            "timezone_id": "Asia/Yekaterinburg",
            "extra_http_headers": {"accept-language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7"},
            "args": [
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        }
        proxy = split_proxy(self.proxy_string)
        if proxy:
            launch_args["proxy"] = proxy

        self._context = self._playwright.chromium.launch_persistent_context(**launch_args)
        self._context.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
            "window.chrome={runtime:{}};"
            "Object.defineProperty(navigator,'languages',{get:()=>['ru-RU','ru']});"
            "Object.defineProperty(navigator,'plugins',{get:()=>[1,2,3,4,5]});"
        )
        self._page = self._context.pages[0] if self._context.pages else self._context.new_page()
        self._page.goto("https://www.avito.ru/", timeout=self.timeout * 1000,
                        wait_until="domcontentloaded")

        title = self._page.title()
        logger.info(f"Браузер открыл Avito: {title!r}")
        if "проблема с ip" in title.lower():
            raise RuntimeError("Avito блокирует этот IP-адрес даже в браузере")

        self._harvest_cookies()

    def _harvest_cookies(self) -> None:
        """Отдаём накопленные браузером cookies наружу — пригодятся быстрому клиенту."""
        if not self.on_cookies or not self._context:
            return
        try:
            jar = {c["name"]: c["value"] for c in self._context.cookies()}
            if jar:
                self.on_cookies(jar, USER_AGENT)
        except Exception as err:
            logger.debug(f"Не удалось забрать cookies из браузера: {err}")

    # ---------- интерфейс HttpClient ----------

    def request(self, method: str, url: str, **kwargs) -> BrowserResponse:
        with self._lock:
            self._ensure_browser()

            result = self._page.evaluate(_FETCH_JS, url)
            status = int(result.get("status") or 0)
            body = result.get("body") or ""

            if status == 0:
                raise RuntimeError(f"Браузер не смог выполнить запрос: {body[:200]}")

            if status in (403, 429, 439):
                # перезагружаем страницу — обычно после этого челлендж решается заново
                logger.warning(f"Браузер получил {status}, обновляю сессию")
                self._page.goto("https://www.avito.ru/", timeout=self.timeout * 1000,
                                wait_until="domcontentloaded")
                self._page.wait_for_timeout(3000)
                result = self._page.evaluate(_FETCH_JS, url)
                status = int(result.get("status") or 0)
                body = result.get("body") or ""

            self._harvest_cookies()

            response = BrowserResponse(status_code=status, text=body, url=url)
            response.raise_for_status()
            return response

    def close(self) -> None:
        with self._lock:
            for attr in ("_context", "_browser"):
                obj = getattr(self, attr, None)
                if obj:
                    try:
                        obj.close()
                    except Exception:
                        pass
                setattr(self, attr, None)
            if self._playwright:
                try:
                    self._playwright.stop()
                except Exception:
                    pass
                self._playwright = None
            self._page = None
