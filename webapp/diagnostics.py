"""
Диагностика доступа к Avito: какой у нас внешний IP и пускает ли нас Avito.
Используется мастером настройки и кнопкой проверки в интерфейсе.
"""
from curl_cffi import requests
from loguru import logger

from webapp.proxy_utils import proxy_url

IP_INFO_URL = "https://ipinfo.io/json"
AVITO_PROBE_URL = "https://www.avito.ru/tyumen"
BLOCKED_STATUSES = {403, 429, 439}


def _build_proxies(proxy_string: str) -> dict | None:
    url = proxy_url(proxy_string)
    if not url:
        return None
    return {"http": url, "https": url}


PROXY_REFUSED_MARKERS = (
    "connect tunnel failed",
    "proxy connect aborted",
    "socks",
    "received http code 403 from proxy",
)


def _proxy_refused(error: str) -> bool:
    """Прокси отказался вести нас к цели (а не сама цель нас заблокировала)."""
    error = (error or "").lower()
    return any(marker in error for marker in PROXY_REFUSED_MARKERS)


def external_ip(proxy_string: str = "") -> dict:
    """Определяет внешний IP — именно его увидит Avito."""
    try:
        response = requests.get(
            IP_INFO_URL, proxies=_build_proxies(proxy_string), timeout=15, impersonate="chrome"
        )
        data = response.json()
        return {
            "ip": data.get("ip"),
            "country": data.get("country"),
            "city": data.get("city"),
            "org": data.get("org"),
        }
    except Exception as err:
        logger.warning(f"Не удалось определить внешний IP: {err}")
        return {"ip": None, "country": None, "city": None, "org": None, "error": str(err)}


def probe_avito(proxy_string: str = "") -> dict:
    """Один запрос к Avito — смотрим, пускают или блокируют."""
    try:
        response = requests.get(
            AVITO_PROBE_URL,
            proxies=_build_proxies(proxy_string),
            timeout=20,
            impersonate="chrome",
        )
        return {"status": response.status_code, "error": None}
    except Exception as err:
        return {"status": None, "error": str(err)}


def check_access(proxy_string: str = "") -> dict:
    """
    Полная проверка доступа. Возвращает вердикт и понятную человеку подсказку.
    """
    ip_info = external_ip(proxy_string)
    probe = probe_avito(proxy_string)

    country = (ip_info.get("country") or "").upper()
    is_russian = country == "RU"
    status = probe.get("status")
    ok = status == 200

    if ok:
        message = "Avito отвечает, доступ есть"
        hint = ""
    elif status in BLOCKED_STATUSES:
        message = f"Avito блокирует запросы (код {status})"
        hint = (
            "IP не российский — Avito пускает только адреса из России. "
            "Отключи VPN для avito.ru или укажи российский прокси."
            if not is_russian else
            "IP российский, но запросы всё равно блокируются — обычно так бывает с "
            "серверными адресами. Попробуй получить cookies через браузер "
            "(раздел «Доступ к Avito») или взять мобильный прокси."
        )
    elif status is not None:
        message = f"Avito ответил кодом {status}"
        hint = "Неожиданный ответ, попробуй ещё раз через минуту."
    elif proxy_string and _proxy_refused(probe.get("error")):
        message = "Прокси не пропускает запросы к Avito"
        hint = (
            "Сам прокси рабочий, но отказывается соединяться именно с Avito — "
            "многие продавцы прокси держат Avito в чёрном списке. Спроси у поддержки, "
            "разрешён ли Avito на твоём тарифе, или возьми прокси, где это заявлено. "
            "На российском сервере прокси обычно не нужен вовсе."
        )
    else:
        message = "Не удалось соединиться с Avito"
        hint = f"Проверь интернет и настройки прокси. Ошибка: {probe.get('error')}"

    location = ", ".join(part for part in [ip_info.get("city"), country] if part)

    return {
        "ok": ok,
        "is_russian": is_russian,
        "status": status,
        "ip": ip_info.get("ip"),
        "location": location,
        "org": ip_info.get("org"),
        "message": message,
        "hint": hint,
    }
