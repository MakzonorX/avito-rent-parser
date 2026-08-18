"""Настройки, специфичные для аренды квартир (секция [rent] в config.toml)."""
from dataclasses import dataclass, field, asdict


@dataclass
class RentConfig:
    # Комнатность: 0 = студия, -1 = комната, 1..N = кол-во комнат. Пустой список = любая
    rooms: list[int] = field(default_factory=list)
    min_area: float = 0          # м², 0 = без ограничения
    max_area: float = 0
    min_floor: int = 0           # 0 = без ограничения
    max_floor: int = 0
    exclude_first_floor: bool = False
    exclude_last_floor: bool = False
    exclude_daily: bool = True   # выкидывать посуточную аренду
    require_photo: bool = False  # только объявления с фото
    address_include: list[str] = field(default_factory=list)  # район/улица: показывать только эти
    address_exclude: list[str] = field(default_factory=list)
    # Первый запуск: запомнить текущие объявления молча, без потока уведомлений
    first_run_silent: bool = True
    # Телеграм
    send_photos: bool = True     # отправлять фото альбомом
    max_photos: int = 6          # сколько фото слать (1..10)

    def as_dict(self) -> dict:
        return asdict(self)
