"""
Telegram-уведомления об аренде квартир: карточка с параметрами + альбом фото.
"""
from datetime import datetime, timezone

import requests
from loguru import logger

from integrations.notifications.telegram import TelegramNotifier
from integrations.notifications.transport import send_with_retries
from integrations.notifications.utils import escape_markdown_v2
from models import Item
from rent.config import RentConfig
from rent.extract import RentInfo, extract

CAPTION_LIMIT = 1024
DESCRIPTION_LIMIT = 350


def human_time_ago(published_at: datetime | None) -> str:
    if not published_at:
        return ""
    delta = datetime.now(timezone.utc) - published_at
    seconds = int(delta.total_seconds())
    if seconds < 0:
        return "только что"
    if seconds < 60:
        return "только что"
    if seconds < 3600:
        minutes = seconds // 60
        return f"{minutes} мин. назад"
    if seconds < 86400:
        hours = seconds // 3600
        return f"{hours} ч. назад"
    days = seconds // 86400
    return f"{days} дн. назад"


def format_price(info: RentInfo) -> str:
    if info.price is None:
        return "Цена не указана"
    price = f"{info.price:,}".replace(",", " ")
    postfix = info.price_postfix or "в месяц"
    return f"{price} ₽ {postfix}".strip()


def build_caption(info: RentInfo, include_description: bool = True) -> str:
    """Карточка объявления в MarkdownV2."""
    params = [p for p in (info.rooms_label, f"{info.area:g} м²" if info.area else "", info.floor_label) if p]
    header = escape_markdown_v2(", ".join(params)) if params else escape_markdown_v2(info.title)

    lines = [f"🏠 *{header}*", f"💰 *{escape_markdown_v2(format_price(info))}*"]

    if info.address:
        lines.append(f"📍 {escape_markdown_v2(info.address)}")

    published = human_time_ago(info.published_at)
    if published:
        lines.append(f"🕒 {escape_markdown_v2(published)}")

    if include_description and info.description:
        description = info.description.replace("\n", " ").strip()
        if len(description) > DESCRIPTION_LIMIT:
            description = description[:DESCRIPTION_LIMIT].rsplit(" ", 1)[0] + "…"
        lines.append("")
        lines.append(escape_markdown_v2(description))

    lines.append("")
    lines.append(f"[Открыть на Avito]({info.url})")

    caption = "\n".join(lines)
    if len(caption) > CAPTION_LIMIT and include_description:
        return build_caption(info, include_description=False)
    return caption[:CAPTION_LIMIT]


class RentTelegramNotifier(TelegramNotifier):
    """Отправляет объявление об аренде альбомом фото с подписью."""

    def __init__(
        self,
        bot_token: str,
        chat_id: str,
        rent_config: RentConfig | None = None,
        proxy: str = None,
        only_text: bool = False,
    ):
        super().__init__(bot_token=bot_token, chat_id=chat_id, proxy=proxy, only_text=only_text)
        self.rent = rent_config or RentConfig()

    def _api(self, method: str) -> str:
        return f"https://api.telegram.org/bot{self.bot_token}/{method}"

    def notify_ad(self, ad: Item):
        info = extract(ad, max_photos=max(1, min(self.rent.max_photos, 10)))
        caption = build_caption(info)
        photos = info.images if self.rent.send_photos and not self.only_text else []

        if len(photos) > 1:
            if self._send_media_group(photos=photos, caption=caption):
                return
            photos = photos[:1]

        if photos:
            if self._send_single_photo(photo=photos[0], caption=caption):
                return

        self._send_text(text=caption)

    def _send_media_group(self, photos: list[str], caption: str) -> bool:
        media = [
            {
                "type": "photo",
                "media": url,
                **({"caption": caption, "parse_mode": "MarkdownV2"} if index == 0 else {}),
            }
            for index, url in enumerate(photos[:10])
        ]

        def _send():
            return requests.post(
                self._api("sendMediaGroup"),
                json={"chat_id": self.chat_id, "media": media},
                proxies=self.proxy,
                timeout=30,
            )

        try:
            send_with_retries(_send)
            return True
        except requests.RequestException as err:
            logger.warning(f"[notify] не удалось отправить альбом: {err}")
            return False

    def _send_single_photo(self, photo: str, caption: str) -> bool:
        def _send():
            return requests.post(
                self._api("sendPhoto"),
                json={
                    "chat_id": self.chat_id,
                    "photo": photo,
                    "caption": caption,
                    "parse_mode": "MarkdownV2",
                },
                proxies=self.proxy,
                timeout=20,
            )

        try:
            send_with_retries(_send)
            return True
        except requests.RequestException as err:
            logger.warning(f"[notify] не удалось отправить фото, пробую байтами: {err}")

        try:
            return self._send_photo_bytes(image_url=photo, message=caption)
        except requests.RequestException as err:
            logger.warning(f"[notify] загрузка фото байтами не удалась: {err}")
            return False

    def _send_text(self, text: str) -> None:
        def _send():
            return requests.post(
                self._api("sendMessage"),
                json={
                    "chat_id": self.chat_id,
                    "text": text,
                    "parse_mode": "MarkdownV2",
                    "disable_web_page_preview": False,
                },
                proxies=self.proxy,
                timeout=20,
            )

        try:
            send_with_retries(_send)
        except requests.RequestException as err:
            logger.error(f"[notify] не удалось отправить сообщение: {err}")
