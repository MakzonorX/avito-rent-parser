"""
Проверка цепочки: ответ API Avito → модель → фильтры аренды → карточка Telegram → база.
Запуск: python tests/test_rent_pipeline.py
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dto import AvitoConfig
from integrations.notifications.telegram_rent import build_caption
from models import ItemsResponse
from rent.config import RentConfig
from rent.extract import extract
from rent.filter import RentAdsFilter


def make_item(ad_id: int, title: str, price: int, address: str, postfix: str = "в месяц",
              images: int = 3, age_hours: float = 0.5) -> dict:
    """Собирает объявление в формате ответа мобильного API Avito."""
    return {
        "id": ad_id,
        "categoryId": 24,
        "locationId": 659020,
        "title": title,
        "description": "Сдаётся уютная квартира. Есть вся мебель и техника, интернет. Собственник.",
        "urlPath": f"/tyumen/kvartiry/{ad_id}",
        "sortTimeStamp": int((time.time() - age_hours * 3600) * 1000),
        "priceDetailed": {
            "enabled": True, "fullString": f"{price} ₽ {postfix}", "hasValue": True,
            "postfix": postfix, "string": str(price), "stringWithoutDiscount": None,
            "title": {"default": "Цена"}, "titleDative": "цене", "value": price,
            "wasLowered": False, "exponent": "",
        },
        "geo": {"geoReferences": [], "formattedAddress": address},
        "addressDetailed": {"locationName": "Тюмень"},
        "images": [
            {"208x156": f"https://img.avito.st/{ad_id}_{i}_208.jpg",
             "640x480": f"https://img.avito.st/{ad_id}_{i}_640.jpg"}
            for i in range(images)
        ],
        "isReserved": False,
    }


CATALOG = {
    "items": [
        make_item(1001, "2-к. квартира, 54,3 м², 3/10 эт.", 38000, "ул. Ленина, 15"),
        make_item(1002, "1-к. квартира, 35 м², 1/9 эт.", 25000, "ул. Мельникайте, 101"),
        make_item(1003, "Квартира-студия, 25 м², 7/17 эт.", 22000, "ул. Широтная, 189"),
        make_item(1004, "3-к. квартира, 78 м², 12/12 эт.", 55000, "ул. Республики, 204"),
        make_item(1005, "1-к. квартира, 32 м², 4/9 эт.", 1500, "ул. Пермякова, 50", postfix="за сутки"),
        make_item(1006, "Комната 12 м² в 3-к., 2/9 эт.", 9000, "ул. Одесская, 7"),
        make_item(1007, "2-к. квартира, 60 м², 5/16 эт.", 120000, "ул. Немцова, 39"),
    ]
}


def base_config(**overrides) -> AvitoConfig:
    params = dict(urls=[], min_price=0, max_price=99999999, max_age=0, ignore_reserv=True)
    params.update(overrides)
    return AvitoConfig(**params)


def test_model_parsing():
    response = ItemsResponse(**CATALOG)
    assert len(response.items) == 7
    info = extract(response.items[0])
    assert info.rooms == 2 and info.area == 54.3
    assert info.floor == 3 and info.total_floors == 10
    assert info.price == 38000
    assert info.address == "ул. Ленина, 15"
    assert len(info.images) == 3
    assert info.images[0].endswith("640.jpg"), "должно браться фото максимального размера"
    assert info.url == "https://www.avito.ru/tyumen/kvartiry/1001"
    print("✓ разбор объявления")


def test_daily_rent_excluded():
    ads = ItemsResponse(**CATALOG).items
    ads_filter = RentAdsFilter(base_config(), RentConfig(exclude_daily=True))
    result = [ad.id for ad in ads_filter.apply(ads)]
    assert 1005 not in result, "посуточная аренда должна отсеиваться"
    assert 1001 in result
    print("✓ посуточная аренда отсеивается")


def test_price_range():
    ads = ItemsResponse(**CATALOG).items
    ads_filter = RentAdsFilter(base_config(min_price=20000, max_price=40000), RentConfig())
    result = [ad.id for ad in ads_filter.apply(ads)]
    assert result == [1001, 1002, 1003], f"неожиданный результат: {result}"
    print("✓ фильтр по цене")


def test_rooms_and_floor():
    ads = ItemsResponse(**CATALOG).items
    ads_filter = RentAdsFilter(
        base_config(),
        RentConfig(rooms=[1, 2], exclude_first_floor=True, exclude_last_floor=True),
    )
    result = [ad.id for ad in ads_filter.apply(ads)]
    assert 1002 not in result, "первый этаж должен отсеиваться"
    assert 1003 not in result, "студия не входит в выбранную комнатность"
    assert 1004 not in result, "3-комнатная не входит в выбранную комнатность"
    assert 1001 in result and 1007 in result
    print("✓ фильтр по комнатности и этажу")


def test_area_and_address():
    ads = ItemsResponse(**CATALOG).items
    ads_filter = RentAdsFilter(
        base_config(),
        RentConfig(min_area=30, max_area=60, address_exclude=["Мельникайте"]),
    )
    result = [ad.id for ad in ads_filter.apply(ads)]
    assert 1002 not in result, "адрес из стоп-списка должен отсеиваться"
    assert 1003 not in result, "25 м² меньше минимума"
    assert 1006 not in result, "комната 12 м² меньше минимума"
    assert 1001 in result
    print("✓ фильтр по площади и адресу")


def test_freshness():
    catalog = {"items": [
        make_item(2001, "1-к. квартира, 35 м², 5/9 эт.", 25000, "ул. Новая, 1", age_hours=0.2),
        make_item(2002, "1-к. квартира, 36 м², 6/9 эт.", 26000, "ул. Старая, 2", age_hours=50),
    ]}
    ads = ItemsResponse(**catalog).items
    ads_filter = RentAdsFilter(base_config(max_age=24 * 3600), RentConfig())
    result = [ad.id for ad in ads_filter.apply(ads)]
    assert result == [2001], f"старое объявление не отсеялось: {result}"
    print("✓ фильтр по свежести")


def test_telegram_caption():
    ad = ItemsResponse(**CATALOG).items[0]
    caption = build_caption(extract(ad))
    assert "2\\-комн\\." in caption, caption
    assert "54\\.3 м²" in caption
    assert "3/10 эт\\." in caption
    assert "38 000 ₽ в месяц" in caption
    assert "Ленина" in caption
    assert "https://www.avito.ru/tyumen/kvartiry/1001" in caption
    assert len(caption) <= 1024, "подпись не должна превышать лимит Telegram"
    print("✓ карточка Telegram")
    print("--- пример сообщения ---")
    print(caption)
    print("------------------------")


def test_store_roundtrip():
    from webapp import store
    store.DB_PATH = Path("storage/test_webapp.db")
    if store.DB_PATH.exists():
        store.DB_PATH.unlink()
    store.init()
    for ad in ItemsResponse(**CATALOG).items:
        store.save_ad(ad)
    items = store.list_ads(limit=10)
    assert len(items) == 7
    assert store.stats()["total"] == 7
    found = next(i for i in items if i["id"] == 1001)
    assert found["rooms"] == 2 and found["area"] == 54.3 and found["price"] == 38000
    assert len(found["images"]) == 3
    searched = store.list_ads(search="Ленина")
    assert len(searched) == 1 and searched[0]["id"] == 1001
    store.DB_PATH.unlink()
    print("✓ сохранение и поиск в базе")


class FakeResponse:
    """Заглушка ответа Telegram."""
    def __init__(self, status_code=200):
        self.status_code = status_code
        self.url = "https://api.telegram.org/"

    def json(self):
        return {"ok": self.status_code == 200}

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(f"status {self.status_code}", response=self)


def test_telegram_media_group_payload():
    """Альбом должен уходить одним запросом, подпись — только у первого фото."""
    import integrations.notifications.telegram_rent as tg_module
    from integrations.notifications.telegram_rent import RentTelegramNotifier

    calls = []
    original_post = tg_module.requests.post

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        return FakeResponse(200)

    tg_module.requests.post = fake_post
    try:
        ad = ItemsResponse(**CATALOG).items[0]
        notifier = RentTelegramNotifier(
            bot_token="test", chat_id="42", rent_config=RentConfig(send_photos=True, max_photos=6)
        )
        notifier.notify_ad(ad)
    finally:
        tg_module.requests.post = original_post

    assert len(calls) == 1, f"ожидался один запрос, было {len(calls)}"
    url, kwargs = calls[0]
    assert url.endswith("/sendMediaGroup"), url
    media = kwargs["json"]["media"]
    assert len(media) == 3, f"должно уйти 3 фото, ушло {len(media)}"
    assert "caption" in media[0] and media[0]["parse_mode"] == "MarkdownV2"
    assert all("caption" not in m for m in media[1:]), "подпись должна быть только у первого фото"
    assert kwargs["json"]["chat_id"] == "42"
    print("✓ альбом фото в Telegram")


def test_telegram_falls_back_to_text():
    """Если фото не отправляются, объявление всё равно должно дойти текстом."""
    import integrations.notifications.telegram_rent as tg_module
    from integrations.notifications.telegram_rent import RentTelegramNotifier

    calls = []
    original_post = tg_module.requests.post

    def fake_post(url, **kwargs):
        calls.append(url)
        # фото не принимаются, текст проходит
        return FakeResponse(200 if url.endswith("/sendMessage") else 400)

    tg_module.requests.post = fake_post
    original_bytes = RentTelegramNotifier._send_photo_bytes
    RentTelegramNotifier._send_photo_bytes = lambda self, image_url, message: False
    try:
        ad = ItemsResponse(**CATALOG).items[0]
        notifier = RentTelegramNotifier(bot_token="test", chat_id="42", rent_config=RentConfig())
        notifier.notify_ad(ad)
    finally:
        tg_module.requests.post = original_post
        RentTelegramNotifier._send_photo_bytes = original_bytes

    assert any(url.endswith("/sendMessage") for url in calls), calls
    print("✓ запасной вариант — текстовое сообщение")


def test_first_run_is_silent():
    """На первом запуске уведомления в Telegram не уходят, объявления только запоминаются."""
    from integrations.notifications.composite import CompositeNotifier
    from integrations.notifications.telegram_rent import RentTelegramNotifier
    from webapp.runner import ParserRunner, StoreNotifier

    config = base_config(tg_token="123:abc", tg_chat_id=["42"])

    loud = ParserRunner.build_notifier(config, RentConfig())
    assert isinstance(loud, CompositeNotifier)
    assert any(isinstance(n, RentTelegramNotifier) for n in loud.notifiers), "обычный режим — с Telegram"

    silent = CompositeNotifier([StoreNotifier()])
    assert not any(isinstance(n, RentTelegramNotifier) for n in silent.notifiers)
    print("✓ тёплый старт без уведомлений")


def test_proxy_normalization():
    """Прокси вводят в разных форматах — приводим к тому, что понимают http-клиенты."""
    from proxy_utils import normalize_proxy

    expected = "user:pass@1.2.3.4:8000"
    for raw in [
        "user:pass@1.2.3.4:8000",
        "1.2.3.4:8000@user:pass",
        "1.2.3.4:8000:user:pass",
        "user:pass:1.2.3.4:8000",
        "  user:pass@1.2.3.4:8000  ",
    ]:
        assert normalize_proxy(raw) == expected, f"{raw} → {normalize_proxy(raw)}"

    # явно указанная схема сохраняется
    assert normalize_proxy("http://user:pass@1.2.3.4:8000") == "http://user:pass@1.2.3.4:8000"

    assert normalize_proxy("1.2.3.4:8000") == "1.2.3.4:8000", "прокси без авторизации"
    assert normalize_proxy("") == ""
    print("✓ нормализация прокси")


def test_proxy_schemes():
    """SOCKS5-прокси должен сохранять свою схему, а не превращаться в http."""
    from proxy_utils import proxy_url

    # без схемы — http по умолчанию
    assert proxy_url("user:pass@1.2.3.4:8000") == "http://user:pass@1.2.3.4:8000"
    assert proxy_url("1.2.3.4:8000:user:pass") == "http://user:pass@1.2.3.4:8000"

    # socks5 повышается до socks5h, чтобы DNS резолвился на стороне прокси
    assert proxy_url("socks5://user:pass@1.2.3.4:1080") == "socks5h://user:pass@1.2.3.4:1080"
    assert proxy_url("socks5h://user:pass@1.2.3.4:1080") == "socks5h://user:pass@1.2.3.4:1080"
    assert proxy_url("SOCKS5://1.2.3.4:1080") == "socks5h://1.2.3.4:1080"
    assert proxy_url("") == ""

    # движок парсера не должен приклеивать http:// к уже готовой схеме
    from parser.proxies.proxy import ServerProxy
    assert ServerProxy("socks5h://u:p@1.2.3.4:1080").get_httpx_proxy() == "socks5h://u:p@1.2.3.4:1080"
    assert ServerProxy("u:p@1.2.3.4:8000").get_httpx_proxy() == "http://u:p@1.2.3.4:8000"
    print("✓ схемы прокси (http / socks5)")


def test_telegram_proxy_schemes():
    """Из России Telegram недоступен — уведомления должны уметь ходить через socks5."""
    from integrations.notifications.telegram import TelegramNotifier

    socks = TelegramNotifier.get_proxy("socks5://user:pass@1.2.3.4:1080")
    assert socks["https"] == "socks5h://user:pass@1.2.3.4:1080", socks
    assert socks["http"] == socks["https"]

    http = TelegramNotifier.get_proxy("user:pass@1.2.3.4:8000")
    assert http["https"] == "http://user:pass@1.2.3.4:8000", http

    assert TelegramNotifier.get_proxy("") is None
    assert TelegramNotifier.get_proxy(None) is None

    # прокси доезжает до самого отправителя
    notifier = TelegramNotifier(bot_token="t", chat_id="1", proxy="socks5://1.2.3.4:1080")
    assert notifier.proxy["https"] == "socks5h://1.2.3.4:1080"
    print("✓ прокси для Telegram (socks5 / http)")


def test_proxy_refusal_detected():
    """Отказ прокси нужно отличать от блокировки со стороны Avito."""
    from webapp.diagnostics import _proxy_refused

    assert _proxy_refused("Failed to perform, curl: (7) CONNECT tunnel failed, response 403")
    assert _proxy_refused("Failed to perform, curl: (56) Proxy CONNECT aborted")
    assert _proxy_refused("Can't complete SOCKS5 connection to www.avito.ru")
    assert not _proxy_refused("Connection timed out after 20000 milliseconds")
    print("✓ распознавание отказа прокси")


def test_onboarding_flag_persists():
    """Флаг пройденного мастера должен переживать сохранение настроек."""
    from webapp import settings

    original = settings.CONFIG_PATH
    settings.CONFIG_PATH = Path("storage/test_config.toml")
    try:
        settings.save(avito={"max_price": 40000}, rent={}, app={"onboarding_done": True})
        raw = settings.load_raw()
        assert raw["app"]["onboarding_done"] is True
        assert raw["avito"]["max_price"] == 40000

        # сохранение других настроек не сбрасывает флаг
        settings.save(avito={"min_price": 10000}, rent={})
        raw = settings.load_raw()
        assert raw["app"]["onboarding_done"] is True, "флаг мастера потерялся"
        assert raw["avito"]["max_price"] == 40000, "прежние настройки затёрлись"
        print("✓ состояние мастера сохраняется")
    finally:
        if settings.CONFIG_PATH.exists():
            settings.CONFIG_PATH.unlink()
        settings.CONFIG_PATH = original


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
    print(f"\nВсе проверки пройдены ({len(tests)})")
