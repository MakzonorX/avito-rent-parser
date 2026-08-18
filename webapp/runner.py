"""
Управление фоновым процессом парсера: старт, стоп, статус, разовая проверка.
"""
import sqlite3
import threading
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger

from integrations.notifications.base import Notifier
from integrations.notifications.composite import CompositeNotifier, NullNotifier
from integrations.notifications.telegram_rent import RentTelegramNotifier
from models import Item
from parser.browser_client import BrowserHttpClient
from parser_cls import AvitoParse
from rent.filter import RentAdsFilter
from webapp import cookies_tool, settings, store


class StoreNotifier(Notifier):
    """Складывает найденные объявления в базу веб-интерфейса."""

    def __init__(self, on_new=None):
        self.on_new = on_new

    def notify(self, ad: Item = None, message: str = None):
        if ad is None:
            return
        store.save_ad(ad)
        if self.on_new:
            self.on_new(ad)


class ParserRunner:
    """Один фоновый поток, крутящий циклы парсинга."""

    def __init__(self):
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self.state = "stopped"          # stopped | running | error
        self.started_at: datetime | None = None
        self.cycles = 0
        self.last_cycle_at: datetime | None = None
        self.next_cycle_at: datetime | None = None
        self.last_error: str | None = None
        self.found_in_session = 0
        self.good_requests = 0
        self.bad_requests = 0
        self.used_browser = False

    # ---------- статус ----------

    @property
    def is_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def status(self) -> dict:
        return {
            "state": "running" if self.is_running else self.state,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "cycles": self.cycles,
            "last_cycle_at": self.last_cycle_at.isoformat() if self.last_cycle_at else None,
            "next_cycle_at": self.next_cycle_at.isoformat() if self.next_cycle_at else None,
            "last_error": self.last_error,
            "found_in_session": self.found_in_session,
            "good_requests": self.good_requests,
            "bad_requests": self.bad_requests,
            "used_browser": self.used_browser,
        }

    # ---------- сборка парсера ----------

    @staticmethod
    def build_notifier(avito_config, rent_config, on_new=None) -> Notifier:
        notifiers: list[Notifier] = []
        if avito_config.tg_token and avito_config.tg_chat_id:
            for chat_id in avito_config.tg_chat_id:
                notifiers.append(
                    RentTelegramNotifier(
                        bot_token=avito_config.tg_token,
                        chat_id=chat_id,
                        rent_config=rent_config,
                        proxy=avito_config.proxy_notifier or None,
                        only_text=avito_config.tg_only_text,
                    )
                )
        else:
            logger.warning("Telegram не настроен — уведомления отправляться не будут")

        notifiers.append(StoreNotifier(on_new=on_new))
        return CompositeNotifier(notifiers) if notifiers else NullNotifier()

    def _store_browser_cookies(self, jar: dict, user_agent: str) -> None:
        """Cookies, добытые браузером, переиспользует быстрый клиент."""
        try:
            cookies_tool.save_cookies(jar, user_agent=user_agent)
            raw = settings.load_raw()
            if not raw["avito"].get("use_own_cookies"):
                settings.save(avito={"use_own_cookies": True}, rent={})
                logger.info("Включаю использование сохранённых cookies — их добыл браузер")
        except Exception as err:
            logger.debug(f"Не удалось сохранить cookies из браузера: {err}")

    def _build_parser(
        self, avito_config, rent_config, silent: bool = False, use_browser: bool = False
    ) -> AvitoParse:
        parser = AvitoParse(config=avito_config, stop_event=self._stop_event)
        if use_browser:
            logger.info("Запросы пойдут через браузер")
            parser.http = BrowserHttpClient(
                proxy_string=avito_config.proxy_string or "",
                headless=rent_config.browser_headless,
                timeout=max(30, avito_config.timeout),
                on_cookies=self._store_browser_cookies,
            )
        parser.ads_filter = RentAdsFilter(
            config=avito_config,
            rent_config=rent_config,
            is_viewed_fn=parser.is_viewed,
        )
        if silent:
            # тёплый старт: объявления попадают в базу, но в Telegram не уходят
            parser.notifier = CompositeNotifier([StoreNotifier(on_new=self._count_found)])
        else:
            parser.notifier = self.build_notifier(
                avito_config, rent_config, on_new=self._count_found
            )
        return parser

    @staticmethod
    def _is_first_run() -> bool:
        """True, если парсер ещё ни разу не видел объявлений (база пуста)."""
        try:
            with sqlite3.connect("database.db", timeout=10) as conn:
                cursor = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='viewed'"
                )
                if not cursor.fetchone():
                    return True
                return conn.execute("SELECT COUNT(*) FROM viewed").fetchone()[0] == 0
        except sqlite3.Error:
            return False

    @staticmethod
    def _check_cookies(avito_config) -> None:
        """Не даём парсеру упасть, если включены cookies, но файла нет."""
        if not avito_config.use_own_cookies:
            return
        if not Path("storage/own_cookies.json").exists():
            logger.warning(
                "Включены сохранённые cookies, но файла storage/own_cookies.json нет. "
                "Работаю без них — получи cookies в разделе «Доступ к Avito»"
            )
            avito_config.use_own_cookies = False

    def _count_found(self, ad: Item) -> None:
        self.found_in_session += 1

    @staticmethod
    def _close_http(parser) -> None:
        """Закрывает браузер, если цикл ходил через него."""
        client = getattr(parser, "http", None)
        if hasattr(client, "close"):
            try:
                client.close()
            except Exception as err:
                logger.debug(f"Ошибка при закрытии браузера: {err}")

    # ---------- жизненный цикл ----------

    def start(self) -> bool:
        with self._lock:
            if self.is_running:
                return False
            self._stop_event.clear()
            self.state = "running"
            self.started_at = datetime.now(timezone.utc)
            self.last_error = None
            self.found_in_session = 0
            self.cycles = 0
            self._thread = threading.Thread(target=self._loop, name="avito-parser", daemon=True)
            self._thread.start()
            logger.info("▶ Парсер запущен")
            return True

    def stop(self) -> bool:
        with self._lock:
            if not self.is_running:
                return False
            logger.info("⏹ Останавливаю парсер...")
            self._stop_event.set()
            self.state = "stopped"
            self.next_cycle_at = None
            return True

    def run_once(self) -> bool:
        """Разовая проверка (кнопка «Проверить сейчас»)."""
        with self._lock:
            if self.is_running:
                return False
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._single_cycle_thread, name="avito-parser-once", daemon=True
            )
            self._thread.start()
            return True

    def _single_cycle_thread(self) -> None:
        self.state = "running"
        try:
            self._run_cycle()
        finally:
            self.state = "stopped"
            self.next_cycle_at = None

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            self._run_cycle()
            if self._stop_event.is_set():
                break

            avito_config, _ = settings.load()
            pause = max(30, int(avito_config.pause_general or 300))
            self.next_cycle_at = datetime.fromtimestamp(time.time() + pause, tz=timezone.utc)
            logger.info(f"Пауза {pause} сек. до следующей проверки")
            if self._stop_event.wait(pause):
                break

        self.state = "stopped"
        self.next_cycle_at = None
        logger.info("⏹ Парсер остановлен")

    def _run_cycle(self) -> None:
        try:
            avito_config, rent_config = settings.load()
            if not avito_config.urls:
                logger.warning("Не задано ни одной ссылки для отслеживания")
                return

            # one_time_start ломает наш цикл — управление паузами на нашей стороне
            avito_config.one_time_start = False
            self._check_cookies(avito_config)

            silent = rent_config.first_run_silent and self._is_first_run()
            if silent:
                logger.info(
                    "Первый запуск: запоминаю текущие объявления без уведомлений. "
                    "В Telegram пойдут только те, что появятся дальше"
                )

            mode = (rent_config.fetch_mode or "auto").lower()
            use_browser = mode == "browser"

            parser = self._build_parser(
                avito_config, rent_config, silent=silent, use_browser=use_browser
            )
            try:
                parser.parse()
                blocked = parser.bad_request_count and not parser.good_request_count

                # обычный клиент упёрся в блокировку — повторяем браузером
                if blocked and mode == "auto" and not use_browser and not self._stop_event.is_set():
                    logger.warning(
                        "Обычные запросы блокируются, повторяю цикл через браузер"
                    )
                    self._close_http(parser)
                    parser = self._build_parser(
                        avito_config, rent_config, silent=silent, use_browser=True
                    )
                    use_browser = True
                    parser.parse()
                    blocked = parser.bad_request_count and not parser.good_request_count
            finally:
                self._close_http(parser)

            self.good_requests = parser.good_request_count
            self.bad_requests = parser.bad_request_count
            self.used_browser = use_browser
            self.cycles += 1
            self.last_cycle_at = datetime.now(timezone.utc)
            self.last_error = None

            if blocked:
                self.last_error = (
                    "Avito блокирует запросы даже через браузер. Нужен российский IP: "
                    "отключить VPN или указать российский прокси."
                    if use_browser else
                    "Все запросы к Avito блокируются. Нужен российский IP "
                    "(отключить VPN / указать прокси) или свежие cookies."
                )
                logger.error(self.last_error)

        except Exception as err:
            self.last_error = f"{type(err).__name__}: {err}"
            self.state = "error"
            logger.error(f"Ошибка в цикле парсинга: {err}")
            logger.debug(traceback.format_exc())


runner = ParserRunner()
