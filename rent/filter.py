"""Фильтр объявлений об аренде: комнатность, площадь, этаж, адрес."""
from typing import List

from loguru import logger

from dto import AvitoConfig
from filters.ads_filter import AdsFilter
from models import Item
from rent.config import RentConfig
from rent.extract import extract


class RentAdsFilter(AdsFilter):
    """Базовые фильтры парсера + параметры квартиры."""

    def __init__(self, config: AvitoConfig, rent_config: RentConfig, is_viewed_fn=None):
        super().__init__(config=config, is_viewed_fn=is_viewed_fn)
        self.rent = rent_config

    def apply(self, ads: List[Item]) -> List[Item]:
        ads = super().apply(ads)
        if not ads:
            return ads

        rent_filters = [
            self._filter_daily,
            self._filter_rooms,
            self._filter_area,
            self._filter_floor,
            self._filter_address,
            self._filter_photo,
        ]
        for filter_fn in rent_filters:
            ads = filter_fn(ads)
            logger.info(f"После фильтрации {filter_fn.__name__} осталось {len(ads)}")
            if not ads:
                return ads
        return ads

    def _filter_daily(self, ads: List[Item]) -> List[Item]:
        if not self.rent.exclude_daily:
            return ads
        return [ad for ad in ads if not extract(ad).is_daily]

    def _filter_rooms(self, ads: List[Item]) -> List[Item]:
        if not self.rent.rooms:
            return ads
        allowed = set(self.rent.rooms)
        result = []
        for ad in ads:
            rooms = extract(ad).rooms
            # объявления без распознанной комнатности не выкидываем — лучше лишнее, чем пропуск
            if rooms is None or rooms in allowed:
                result.append(ad)
        return result

    def _filter_area(self, ads: List[Item]) -> List[Item]:
        if not self.rent.min_area and not self.rent.max_area:
            return ads
        result = []
        for ad in ads:
            area = extract(ad).area
            if area is None:
                result.append(ad)
                continue
            if self.rent.min_area and area < self.rent.min_area:
                continue
            if self.rent.max_area and area > self.rent.max_area:
                continue
            result.append(ad)
        return result

    def _filter_floor(self, ads: List[Item]) -> List[Item]:
        rent = self.rent
        if not any([rent.min_floor, rent.max_floor, rent.exclude_first_floor, rent.exclude_last_floor]):
            return ads
        result = []
        for ad in ads:
            info = extract(ad)
            if info.floor is None:
                result.append(ad)
                continue
            if rent.exclude_first_floor and info.is_first_floor:
                continue
            if rent.exclude_last_floor and info.is_last_floor:
                continue
            if rent.min_floor and info.floor < rent.min_floor:
                continue
            if rent.max_floor and info.floor > rent.max_floor:
                continue
            result.append(ad)
        return result

    def _filter_address(self, ads: List[Item]) -> List[Item]:
        rent = self.rent
        if not rent.address_include and not rent.address_exclude:
            return ads
        result = []
        for ad in ads:
            address = extract(ad).address.lower()
            if rent.address_exclude and any(word.lower() in address for word in rent.address_exclude if word):
                continue
            if rent.address_include and not any(word.lower() in address for word in rent.address_include if word):
                continue
            result.append(ad)
        return result

    def _filter_photo(self, ads: List[Item]) -> List[Item]:
        if not self.rent.require_photo:
            return ads
        return [ad for ad in ads if extract(ad).images]
