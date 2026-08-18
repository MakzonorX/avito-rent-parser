"""
Проверяет все способы достучаться до Avito и показывает, какой работает.

    .venv/bin/python scripts/check_avito.py

Порядок проверки: внешний IP → обычный запрос → cookies из браузера →
обычный запрос с этими cookies → запрос целиком через браузер.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from loguru import logger

logger.remove()
logger.add(sys.stderr, level="WARNING", format="<dim>{message}</dim>")

API_URL = (
    "https://www.avito.ru/web/1/js/items?categoryId=24&localPriority=0"
    "&locationId=659020&params%5B201%5D=1060&params%5B504%5D=5256"
    "&presentationType=serp&sort=date&page=1"
)

OK, FAIL, WARN = "\033[92m✓\033[0m", "\033[91m✗\033[0m", "\033[93m!\033[0m"


def head(text):
    print(f"\n\033[1m{text}\033[0m")


def items_count(payload: dict) -> int:
    for candidate in (payload.get("catalog"), payload.get("result"), payload):
        if isinstance(candidate, dict) and isinstance(candidate.get("items"), list):
            return len(candidate["items"])
    return 0


def main():
    proxy = ""
    if len(sys.argv) > 1:
        proxy = sys.argv[1]
        print(f"Через прокси: {proxy}")

    # ── 1. Кто мы снаружи ────────────────────────────────────────────────────
    head("1. Внешний IP")
    from webapp.diagnostics import external_ip

    info = external_ip(proxy)
    country = (info.get("country") or "?").upper()
    print(f"   {info.get('ip')} · {info.get('city')}, {country} · {info.get('org')}")
    if country != "RU":
        print(f"   {WARN} IP не российский — Avito почти наверняка откажет. Отключи VPN.")

    # ── 2. Обычный запрос ────────────────────────────────────────────────────
    head("2. Обычный запрос (curl_cffi)")
    from curl_cffi import requests as cffi
    from proxy_utils import proxy_url

    proxies = {"http": proxy_url(proxy), "https": proxy_url(proxy)} if proxy else None
    plain_ok = False
    try:
        response = cffi.get(API_URL, proxies=proxies, timeout=25, impersonate="chrome136")
        print(f"   код {response.status_code}")
        if response.status_code == 200:
            plain_ok = True
            print(f"   {OK} работает, объявлений: {items_count(response.json())}")
        else:
            print(f"   {FAIL} заблокирован: {response.text[:120]}")
    except Exception as err:
        print(f"   {FAIL} {type(err).__name__}: {err}")

    # ── 3. Cookies через браузер ─────────────────────────────────────────────
    head("3. Cookies из браузера")
    from webapp import cookies_tool

    jar = {}
    try:
        import asyncio

        jar = asyncio.run(cookies_tool._grab_cookies(headless=True, timeout_sec=70))
        cookies_tool.save_cookies(jar)
        print(f"   {OK} получено {len(jar)} cookies, ft: {'да' if 'ft' in jar else 'нет'}")
        print(f"   {', '.join(list(jar)[:10])}")
    except Exception as err:
        print(f"   {FAIL} {err}")

    # ── 4. Обычный запрос с этими cookies ────────────────────────────────────
    head("4. Обычный запрос + cookies браузера")
    cookies_ok = False
    if jar:
        try:
            session = cffi.Session(impersonate=cookies_tool.IMPERSONATE)
            session.headers.update(cookies_tool.CLIENT_HINTS)
            session.headers["referer"] = "https://www.avito.ru/"
            session.cookies.update(jar)
            if proxies:
                session.proxies = proxies
            response = session.get(API_URL, timeout=25)
            print(f"   код {response.status_code}")
            if response.status_code == 200:
                cookies_ok = True
                print(f"   {OK} работает, объявлений: {items_count(response.json())}")
            else:
                print(f"   {FAIL} {response.text[:120]}")
        except Exception as err:
            print(f"   {FAIL} {type(err).__name__}: {err}")
    else:
        print("   пропускаю — cookies не получены")

    # ── 5. Запрос целиком через браузер ──────────────────────────────────────
    head("5. Запрос через браузер")
    browser_ok = False
    from parser.browser_client import BrowserHttpClient

    client = BrowserHttpClient(proxy_string=proxy, timeout=60)
    try:
        response = client.request("GET", API_URL)
        print(f"   код {response.status_code}")
        if response.status_code == 200:
            browser_ok = True
            print(f"   {OK} работает, объявлений: {items_count(response.json())}")
    except Exception as err:
        print(f"   {FAIL} {type(err).__name__}: {err}")
    finally:
        client.close()

    # ── Итог ─────────────────────────────────────────────────────────────────
    head("Итог")
    print(f"   обычный запрос            {OK if plain_ok else FAIL}")
    print(f"   обычный запрос + cookies  {OK if cookies_ok else FAIL}")
    print(f"   через браузер             {OK if browser_ok else FAIL}")
    print()
    if plain_ok or cookies_ok:
        print(f"   {OK} Ставь режим «Только обычные запросы» или «Авто» — будет быстро.")
    elif browser_ok:
        print(f"   {OK} Ставь режим «Всегда через браузер» — обычные запросы не проходят.")
    else:
        print(f"   {FAIL} Ни один способ не работает. Нужен российский IP: отключи VPN")
        print("     или укажи российский прокси (он не должен блокировать avito.ru).")
    print()


if __name__ == "__main__":
    main()
