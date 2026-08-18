"""
Извлечение параметров квартиры из объявления Avito.

Avito отдаёт параметры аренды в заголовке объявления
("2-к. квартира, 54,3 м², 3/10 эт."), поэтому основной источник — title,
а адрес и цена берутся из структурированных полей.
"""
import re
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Optional

from models import Item

STUDIO = 0
ROOM = -1  # комната/койко-место, не отдельная квартира

_ROOMS_RE = re.compile(r"(\d+)\s*-?\s*к(?:омн)?\.?", re.IGNORECASE)
_STUDIO_RE = re.compile(r"студи", re.IGNORECASE)
_ROOM_RE = re.compile(r"^\s*(комната|койко)", re.IGNORECASE)
_AREA_RE = re.compile(r"([\d]+(?:[.,]\d+)?)\s*м²")
_FLOOR_RE = re.compile(r"(\d+)\s*/\s*(\d+)\s*эт", re.IGNORECASE)
_DAILY_RE = re.compile(r"посуточн|сутк|на ночь|почасов", re.IGNORECASE)


@dataclass
class RentInfo:
    """Разобранные параметры объявления об аренде."""
    rooms: Optional[int] = None          # 0 = студия, -1 = комната, None = не определено
    area: Optional[float] = None         # м²
    floor: Optional[int] = None
    total_floors: Optional[int] = None
    price: Optional[int] = None
    price_postfix: str = ""              # "в месяц" / "за сутки"
    address: str = ""
    title: str = ""
    description: str = ""
    url: str = ""
    published_at: Optional[datetime] = None
    images: list = None
    is_daily: bool = False

    def __post_init__(self):
        if self.images is None:
            self.images = []

    @property
    def rooms_label(self) -> str:
        if self.rooms == STUDIO:
            return "Студия"
        if self.rooms == ROOM:
            return "Комната"
        if self.rooms is None:
            return "—"
        return f"{self.rooms}-комн."

    @property
    def floor_label(self) -> str:
        if self.floor and self.total_floors:
            return f"{self.floor}/{self.total_floors} эт."
        if self.floor:
            return f"{self.floor} эт."
        return ""

    @property
    def is_first_floor(self) -> bool:
        return self.floor == 1

    @property
    def is_last_floor(self) -> bool:
        return bool(self.floor and self.total_floors and self.floor == self.total_floors)

    def as_dict(self) -> dict:
        data = asdict(self)
        data["published_at"] = self.published_at.isoformat() if self.published_at else None
        data["rooms_label"] = self.rooms_label
        data["floor_label"] = self.floor_label
        return data


def parse_rooms(title: str) -> Optional[int]:
    if not title:
        return None
    if _ROOM_RE.search(title):
        return ROOM
    if _STUDIO_RE.search(title):
        return STUDIO
    match = _ROOMS_RE.search(title)
    if match:
        try:
            return int(match.group(1))
        except ValueError:
            return None
    return None


def parse_area(title: str) -> Optional[float]:
    if not title:
        return None
    match = _AREA_RE.search(title)
    if not match:
        return None
    try:
        return float(match.group(1).replace(",", "."))
    except ValueError:
        return None


def parse_floor(title: str) -> tuple[Optional[int], Optional[int]]:
    if not title:
        return None, None
    match = _FLOOR_RE.search(title)
    if not match:
        return None, None
    try:
        return int(match.group(1)), int(match.group(2))
    except ValueError:
        return None, None


def get_images(ad: Item, limit: int = 10) -> list[str]:
    """Возвращает ссылки на фото в максимальном доступном разрешении."""
    images = getattr(ad, "images", None) or []
    urls = []

    def largest(img) -> Optional[str]:
        try:
            key = max(
                img.root.keys(),
                key=lambda k: int(k.split("x")[0]) * int(k.split("x")[1]),
            )
            return str(img.root[key])
        except (ValueError, AttributeError, TypeError):
            return None

    for img in images[:limit]:
        url = largest(img)
        if url:
            urls.append(url)
    return urls


def _get_price(ad: Item) -> tuple[Optional[int], str]:
    price = getattr(ad, "priceDetailed", None)
    if price is None:
        return None, ""
    if isinstance(price, dict):
        value, postfix = price.get("value"), price.get("postfix", "")
    else:
        value, postfix = getattr(price, "value", None), getattr(price, "postfix", "") or ""
    try:
        value = int(value) if value is not None else None
    except (TypeError, ValueError):
        value = None
    return value, str(postfix).replace("\xa0", " ").strip()


def _get_address(ad: Item) -> str:
    geo = getattr(ad, "geo", None)
    if geo is not None:
        address = geo.get("formattedAddress") if isinstance(geo, dict) else getattr(geo, "formattedAddress", "")
        if address:
            return str(address).replace("\xa0", " ").strip()
    detailed = getattr(ad, "addressDetailed", None)
    if detailed is not None:
        name = detailed.get("locationName") if isinstance(detailed, dict) else getattr(detailed, "locationName", "")
        if name:
            return str(name).replace("\xa0", " ").strip()
    return ""


def extract(ad: Item, max_photos: int = 10) -> RentInfo:
    """Собирает RentInfo из объявления."""
    title = (getattr(ad, "title", "") or "").replace("\xa0", " ").strip()
    description = (getattr(ad, "description", "") or "").replace("\xa0", " ").strip()
    price, postfix = _get_price(ad)
    floor, total_floors = parse_floor(title)

    published_at = None
    timestamp = getattr(ad, "sortTimeStamp", None)
    if timestamp:
        try:
            published_at = datetime.fromtimestamp(timestamp / 1000, tz=timezone.utc)
        except (ValueError, OSError, TypeError):
            published_at = None

    url_path = getattr(ad, "urlPath", "") or ""
    url = f"https://www.avito.ru{url_path}" if url_path.startswith("/") else url_path

    return RentInfo(
        rooms=parse_rooms(title),
        area=parse_area(title),
        floor=floor,
        total_floors=total_floors,
        price=price,
        price_postfix=postfix,
        address=_get_address(ad),
        title=title,
        description=description,
        url=url or f"https://avito.ru/{getattr(ad, 'id', '')}",
        published_at=published_at,
        images=get_images(ad, limit=max_photos),
        is_daily=bool(_DAILY_RE.search(f"{title} {postfix}")),
    )
