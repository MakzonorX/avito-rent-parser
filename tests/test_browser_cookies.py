"""
Проверка импорта cookies Avito из браузера — на своей тестовой базе,
собранной в формате Chromium. Настоящие профили здесь не читаются.

Запуск: python tests/test_browser_cookies.py
"""
import sqlite3
import sys
import tempfile
from hashlib import pbkdf2_hmac
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from webapp.browser_cookies import (
    CHROMIUM_IV,
    CHROMIUM_SALT,
    BrowserProfile,
    _decrypt_chromium,
    _read_chromium,
    _read_firefox,
)

KEY = pbkdf2_hmac("sha1", b"peanuts", CHROMIUM_SALT, 1, 16)


def encrypt(value: str, with_domain_prefix: bool = False) -> bytes:
    """Шифрует значение так же, как это делает Chromium на Linux."""
    plain = value.encode()
    if with_domain_prefix:
        plain = b"\x11" * 32 + plain  # Chrome 127+ дописывает хэш домена
    padding = 16 - len(plain) % 16
    plain += bytes([padding]) * padding
    encryptor = Cipher(algorithms.AES(KEY), modes.CBC(CHROMIUM_IV)).encryptor()
    return b"v10" + encryptor.update(plain) + encryptor.finalize()


def make_chromium_db(path: Path, rows: list[tuple]) -> BrowserProfile:
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE cookies (host_key TEXT, name TEXT, value TEXT, encrypted_value BLOB)"
    )
    connection.executemany("INSERT INTO cookies VALUES (?,?,?,?)", rows)
    connection.commit()
    connection.close()
    return BrowserProfile(browser="Тест", profile="Default", cookies_db=path, kind="chromium")


def test_decrypt_values():
    assert _decrypt_chromium(encrypt("qJJhi32H1q-njHeDODNu"), [KEY]) == "qJJhi32H1q-njHeDODNu"
    assert _decrypt_chromium(encrypt("sessid-123", True), [KEY]) == "sessid-123", \
        "не снят 32-байтовый префикс Chrome 127+"
    assert _decrypt_chromium(b"plain-value", [KEY]) == "plain-value", "нешифрованное значение"
    assert _decrypt_chromium(b"", [KEY]) is None
    print("✓ расшифровка значений Chromium")


def test_reads_only_avito():
    with tempfile.TemporaryDirectory() as tmp:
        profile = make_chromium_db(Path(tmp) / "Cookies", [
            (".avito.ru", "ft", "", encrypt("FT-TOKEN")),
            ("www.avito.ru", "sessid", "", encrypt("SESSION-123")),
            (".avito.ru", "_avisc", "", encrypt("AVISC-XYZ")),
            (".google.com", "SID", "", encrypt("чужое")),
            (".sberbank.ru", "secret", "", encrypt("чужое")),
        ])
        cookies = _read_chromium(profile)

    assert set(cookies) == {"ft", "sessid", "_avisc"}, f"взято лишнее: {set(cookies)}"
    assert cookies["ft"] == "FT-TOKEN"
    print("✓ берутся только cookies Avito, чужие домены не трогаются")


def test_reads_locked_database():
    """Браузер может быть открыт и держать базу — читаем копию."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "Cookies"
        profile = make_chromium_db(path, [(".avito.ru", "ft", "", encrypt("FT-TOKEN"))])
        holder = sqlite3.connect(path)
        holder.execute("BEGIN EXCLUSIVE")
        try:
            cookies = _read_chromium(profile)
            assert cookies == {"ft": "FT-TOKEN"}
        finally:
            holder.rollback()
            holder.close()
    print("✓ база читается при открытом браузере")


def test_firefox_plain_cookies():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "cookies.sqlite"
        connection = sqlite3.connect(path)
        connection.execute("CREATE TABLE moz_cookies (host TEXT, name TEXT, value TEXT)")
        connection.executemany("INSERT INTO moz_cookies VALUES (?,?,?)", [
            (".avito.ru", "ft", "FT-TOKEN"),
            (".vk.com", "remixsid", "чужое"),
        ])
        connection.commit()
        connection.close()
        profile = BrowserProfile(browser="Firefox", profile="p", cookies_db=path, kind="firefox")
        cookies = _read_firefox(profile)

    assert cookies == {"ft": "FT-TOKEN"}
    print("✓ Firefox: только Avito")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
    print(f"\nВсе проверки пройдены ({len(tests)})")
