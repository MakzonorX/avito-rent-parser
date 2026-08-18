"""
Чтение и запись config.toml: секция [avito] (настройки парсера) и [rent]
(параметры квартиры). Формат совместим с оригинальным парсером.
"""
import tomllib
from dataclasses import fields
from pathlib import Path
from typing import Any

import tomli_w
from loguru import logger

from dto import AvitoConfig
from rent.config import RentConfig
from proxy_utils import normalize_proxy

CONFIG_PATH = Path("config.toml")

# Тюмень, длительная аренда квартир, сортировка по дате (s=104 — «по дате» на Avito).
# Сортировка по дате обязательна: новые объявления должны быть на первой странице.
DEFAULT_URL = "https://www.avito.ru/tyumen/kvartiry/sdam/na_dlitelnyy_srok-ASgBAgICAkSSA8gQ8AeQUg?cd=1&s=104"

PRESETS = {
    "Тюмень — квартиры, длительная аренда (по дате)": DEFAULT_URL,
}

DEFAULT_AVITO = {
    "urls": [DEFAULT_URL],
    "count": 1,
    "tg_token": "",
    "tg_chat_id": [],
    "vk_token": "",
    "vk_user_id": [],
    "keys_word_white_list": [],
    "keys_word_black_list": [],
    "seller_black_list": [],
    "max_price": 60000,
    "min_price": 0,
    "geo": "",
    "proxy_string": "",
    "proxy_change_url": "",
    "pause_general": 300,
    "pause_between_links": 5,
    "max_age": 86400,
    "max_count_of_retry": 5,
    "ignore_reserv": True,
    "ignore_promotion": False,
    "one_time_start": False,
    "one_file_for_link": False,
    "parse_views": False,
    "save_xlsx": False,
    "use_webdriver": False,
    "use_bypass_api": False,
    "cookies_api_key": "",
    "use_own_cookies": False,
    "parse_phone": False,
    "proxy_notifier": "",
    "tg_only_text": False,
    "retry_delay": 5,
    "timeout": 20,
    "block_threshold": 3,
}

DEFAULT_APP = {
    "onboarding_done": False,
}

_AVITO_FIELDS = {f.name: f for f in fields(AvitoConfig)}
_RENT_FIELDS = {f.name: f for f in fields(RentConfig)}


def _coerce(value: Any, target_type: str, default: Any) -> Any:
    """Приводит значение из формы/файла к типу поля конфига."""
    try:
        if "bool" in target_type:
            if isinstance(value, bool):
                return value
            return str(value).strip().lower() in {"1", "true", "on", "yes", "да"}
        if "list[int]" in target_type.replace(" ", ""):
            if isinstance(value, str):
                value = [v for v in value.replace(",", "\n").split("\n") if v.strip()]
            return [int(str(v).strip()) for v in (value or []) if str(v).strip()]
        if "List[str]" in target_type or "list[str]" in target_type:
            if isinstance(value, str):
                value = [v for v in value.split("\n")]
            return [str(v).strip() for v in (value or []) if str(v).strip()]
        if "float" in target_type:
            return float(str(value).replace(",", ".")) if str(value).strip() else 0.0
        if "int" in target_type:
            return int(float(str(value))) if str(value).strip() else 0
        if "Path" in target_type:
            return str(value)
        return str(value) if value is not None else ""
    except (TypeError, ValueError):
        return default


def load_raw() -> dict:
    if not CONFIG_PATH.exists():
        return {"avito": dict(DEFAULT_AVITO), "rent": RentConfig().as_dict(), "app": dict(DEFAULT_APP)}
    try:
        with CONFIG_PATH.open("rb") as f:
            data = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError) as err:
        logger.error(f"Не удалось прочитать config.toml: {err}. Использую значения по умолчанию")
        return {"avito": dict(DEFAULT_AVITO), "rent": RentConfig().as_dict(), "app": dict(DEFAULT_APP)}

    avito = {**DEFAULT_AVITO, **(data.get("avito") or {})}
    rent = {**RentConfig().as_dict(), **(data.get("rent") or {})}
    app = {**DEFAULT_APP, **(data.get("app") or {})}
    return {"avito": avito, "rent": rent, "app": app}


def load() -> tuple[AvitoConfig, RentConfig]:
    raw = load_raw()
    avito_kwargs = {k: v for k, v in raw["avito"].items() if k in _AVITO_FIELDS}
    rent_kwargs = {k: v for k, v in raw["rent"].items() if k in _RENT_FIELDS}
    return AvitoConfig(**avito_kwargs), RentConfig(**rent_kwargs)


def save(avito: dict, rent: dict, app: dict | None = None) -> None:
    """Пишет config.toml атомарно, сохраняя типы полей."""
    current = load_raw()

    avito_out = dict(current["avito"])
    for key, value in avito.items():
        if key not in _AVITO_FIELDS:
            continue
        field = _AVITO_FIELDS[key]
        value = _coerce(value, str(field.type), current["avito"].get(key))
        if key in ("proxy_string", "proxy_notifier"):
            value = normalize_proxy(value)
        avito_out[key] = value

    rent_out = dict(current["rent"])
    for key, value in rent.items():
        if key not in _RENT_FIELDS:
            continue
        field = _RENT_FIELDS[key]
        rent_out[key] = _coerce(value, str(field.type), current["rent"].get(key))

    # output_dir хранится как строка, tomli_w не умеет Path
    avito_out.pop("debug_mode", None)
    avito_out.pop("purchase_cooldown", None)
    avito_out.pop("output_dir", None)

    app_out = dict(current["app"])
    for key, value in (app or {}).items():
        if key in DEFAULT_APP:
            app_out[key] = bool(value) if isinstance(DEFAULT_APP[key], bool) else value

    payload = {"avito": avito_out, "rent": rent_out, "app": app_out}
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    temp_path = CONFIG_PATH.with_suffix(".toml.tmp")
    with temp_path.open("wb") as f:
        tomli_w.dump(payload, f)
    temp_path.replace(CONFIG_PATH)
    logger.info("Настройки сохранены в config.toml")
