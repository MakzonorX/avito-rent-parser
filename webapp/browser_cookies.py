"""
Импорт cookies Avito из установленного браузера.

Avito пускает браузер, в котором уже есть живая сессия: в инкогнито (пустой
профиль) он отдаёт «проблема с IP», в обычном окне — работает. Значит парсеру
нужны те же cookies, что лежат в вашем браузере.

Читаются только cookies с доменов Avito — ничего больше отсюда не берётся.
"""
import base64
import json
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

AVITO_DOMAINS = ("avito.ru", ".avito.ru", "www.avito.ru", "m.avito.ru")

# Chromium на Linux шифрует значения AES-128-CBC с ключом из этой соли
CHROMIUM_SALT = b"saltysalt"
CHROMIUM_IV = b" " * 16
CHROMIUM_ITERATIONS = 1
# Пароль по умолчанию, когда системная связка ключей недоступна
CHROMIUM_FALLBACK_PASSWORD = b"peanuts"

HOME = Path.home()

# Где искать профили. Chromium-браузеры хранят cookies одинаково.
CHROMIUM_BROWSERS = {
    "Google Chrome": [HOME / ".config/google-chrome"],
    "Chromium": [HOME / ".config/chromium", HOME / "snap/chromium/common/chromium"],
    "Brave": [HOME / ".config/BraveSoftware/Brave-Browser"],
    "Microsoft Edge": [HOME / ".config/microsoft-edge"],
    "Opera": [HOME / ".config/opera", HOME / ".config/opera-stable"],
    "Vivaldi": [HOME / ".config/vivaldi"],
    "Яндекс.Браузер": [HOME / ".config/yandex-browser", HOME / ".config/yandex-browser-beta"],
}
FIREFOX_DIRS = [HOME / ".mozilla/firefox", HOME / "snap/firefox/common/.mozilla/firefox"]


@dataclass
class BrowserProfile:
    browser: str
    profile: str
    cookies_db: Path
    kind: str  # chromium | firefox

    @property
    def key(self) -> str:
        return f"{self.kind}:{self.browser}:{self.profile}"

    def as_dict(self) -> dict:
        return {"key": self.key, "browser": self.browser, "profile": self.profile}


# ──────────────────────────────────────────────────────── поиск профилей

def find_profiles() -> list[BrowserProfile]:
    """Все профили браузеров, где есть база cookies."""
    profiles: list[BrowserProfile] = []

    for browser, roots in CHROMIUM_BROWSERS.items():
        for root in roots:
            if not root.is_dir():
                continue
            for profile_dir in sorted(root.iterdir()):
                if not profile_dir.is_dir():
                    continue
                for candidate in (profile_dir / "Cookies", profile_dir / "Network/Cookies"):
                    if candidate.is_file():
                        profiles.append(BrowserProfile(
                            browser=browser, profile=profile_dir.name,
                            cookies_db=candidate, kind="chromium",
                        ))
                        break

    for root in FIREFOX_DIRS:
        if not root.is_dir():
            continue
        for profile_dir in sorted(root.iterdir()):
            candidate = profile_dir / "cookies.sqlite"
            if candidate.is_file():
                profiles.append(BrowserProfile(
                    browser="Firefox", profile=profile_dir.name,
                    cookies_db=candidate, kind="firefox",
                ))

    return profiles


# ──────────────────────────────────────────────────────── расшифровка Chromium

def _chromium_keys() -> list[bytes]:
    """Ключи шифрования: из системной связки ключей и запасной 'peanuts'."""
    from hashlib import pbkdf2_hmac

    passwords = [CHROMIUM_FALLBACK_PASSWORD]
    try:
        import secretstorage

        connection = secretstorage.dbus_init()
        collection = secretstorage.get_default_collection(connection)
        if not collection.is_locked():
            for item in collection.get_all_items():
                label = (item.get_label() or "").lower()
                if "safe storage" in label:
                    passwords.insert(0, item.get_secret())
    except Exception as err:
        logger.debug(f"Связка ключей недоступна, использую запасной пароль: {err}")

    return [
        pbkdf2_hmac("sha1", password, CHROMIUM_SALT, CHROMIUM_ITERATIONS, 16)
        for password in passwords
    ]


def _decrypt_chromium(encrypted: bytes, keys: list[bytes]) -> str | None:
    if not encrypted:
        return None

    # незашифрованное значение
    if encrypted[:3] not in (b"v10", b"v11"):
        try:
            return encrypted.decode("utf-8")
        except UnicodeDecodeError:
            return None

    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    payload = encrypted[3:]
    for key in keys:
        try:
            decryptor = Cipher(algorithms.AES(key), modes.CBC(CHROMIUM_IV)).decryptor()
            plain = decryptor.update(payload) + decryptor.finalize()
            if not plain:
                continue
            plain = plain[: -plain[-1]] if 0 < plain[-1] <= 16 else plain  # снимаем padding

            for candidate in (plain, plain[32:]):  # Chrome 127+ дописывает 32 байта хэша домена
                if not candidate:
                    continue
                try:
                    value = candidate.decode("utf-8")
                except UnicodeDecodeError:
                    continue
                # пустое или мусорное значение = ключ не тот, пробуем следующий
                if value and value.isprintable():
                    return value
        except Exception:
            continue
    return None


def _read_chromium(profile: BrowserProfile) -> dict:
    keys = _chromium_keys()
    cookies = {}

    with tempfile.TemporaryDirectory() as tmp:
        # копируем базу: браузер может быть открыт и держать блокировку
        copy_path = Path(tmp) / "Cookies"
        shutil.copy2(profile.cookies_db, copy_path)
        connection = sqlite3.connect(f"file:{copy_path}?mode=ro", uri=True)
        try:
            rows = connection.execute(
                "SELECT host_key, name, value, encrypted_value FROM cookies "
                "WHERE host_key LIKE '%avito.ru'"
            ).fetchall()
        finally:
            connection.close()

    undecrypted = 0
    for host_key, name, value, encrypted_value in rows:
        resolved = value or _decrypt_chromium(encrypted_value, keys)
        if resolved:
            cookies[name] = resolved
        else:
            undecrypted += 1

    if undecrypted:
        logger.warning(f"Не удалось расшифровать {undecrypted} cookies — нужен доступ к связке ключей")
    return cookies


def _read_firefox(profile: BrowserProfile) -> dict:
    cookies = {}
    with tempfile.TemporaryDirectory() as tmp:
        copy_path = Path(tmp) / "cookies.sqlite"
        shutil.copy2(profile.cookies_db, copy_path)
        connection = sqlite3.connect(f"file:{copy_path}?mode=ro", uri=True)
        try:
            rows = connection.execute(
                "SELECT name, value FROM moz_cookies WHERE host LIKE '%avito.ru'"
            ).fetchall()
        finally:
            connection.close()
    for name, value in rows:
        if value:
            cookies[name] = value
    return cookies


# ──────────────────────────────────────────────────────── публичный интерфейс

def read_cookies(profile: BrowserProfile) -> dict:
    if profile.kind == "firefox":
        return _read_firefox(profile)
    return _read_chromium(profile)


def scan() -> list[dict]:
    """Профили браузеров с количеством найденных cookies Avito."""
    result = []
    for profile in find_profiles():
        entry = profile.as_dict()
        try:
            cookies = read_cookies(profile)
            entry["count"] = len(cookies)
            entry["has_ft"] = "ft" in cookies
            entry["error"] = None
        except Exception as err:
            entry["count"] = 0
            entry["has_ft"] = False
            entry["error"] = str(err)
        result.append(entry)
    # профили с ключевой cookie — первыми
    result.sort(key=lambda item: (item["has_ft"], item["count"]), reverse=True)
    return result


def import_from(profile_key: str = "") -> dict:
    """
    Забирает cookies Avito из браузера. Без ключа берёт лучший найденный профиль.
    """
    profiles = {profile.key: profile for profile in find_profiles()}
    if not profiles:
        raise RuntimeError(
            "Не нашёл ни одного браузера с cookies. Поддерживаются Chrome, Chromium, "
            "Brave, Edge, Opera, Vivaldi, Яндекс.Браузер и Firefox."
        )

    if profile_key:
        profile = profiles.get(profile_key)
        if not profile:
            raise RuntimeError("Такой профиль браузера не найден")
        candidates = [profile]
    else:
        # сами выбираем профиль, где есть ft и больше всего cookies
        scored = []
        for profile in profiles.values():
            try:
                cookies = read_cookies(profile)
            except Exception:
                continue
            scored.append((("ft" in cookies), len(cookies), profile, cookies))
        scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
        if not scored or not scored[0][1]:
            raise RuntimeError(
                "В браузерах нет cookies Avito. Открой avito.ru в браузере "
                "(в обычном окне, не в инкогнито) и нажми кнопку ещё раз."
            )
        best = scored[0]
        return {
            "cookies": best[3],
            "browser": best[2].browser,
            "profile": best[2].profile,
        }

    profile = candidates[0]
    cookies = read_cookies(profile)
    if not cookies:
        raise RuntimeError(
            f"В профиле {profile.browser} / {profile.profile} нет cookies Avito. "
            "Открой avito.ru в этом браузере и попробуй снова."
        )
    return {"cookies": cookies, "browser": profile.browser, "profile": profile.profile}
