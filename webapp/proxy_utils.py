"""Нормализация строки прокси: пользователи вводят её в разных форматах."""


def normalize_proxy(raw: str) -> str:
    """
    Приводит прокси к виду, который понимают http-клиенты: user:pass@host:port.

    Принимает: user:pass@host:port, host:port@user:pass,
               host:port:user:pass, user:pass:host:port, host:port
    """
    raw = (raw or "").strip()
    if not raw:
        return ""

    if "//" in raw:
        raw = raw.split("//", 1)[1]
    raw = raw.rstrip("/")

    if "@" in raw:
        left, right = raw.split("@", 1)
        # хост — та часть, где есть точка (ip или домен)
        if "." in left and "." not in right:
            left, right = right, left
        return f"{left}@{right}"

    parts = raw.split(":")
    if len(parts) == 4:
        first, second, third, fourth = parts
        if "." in first:  # host:port:user:pass
            return f"{third}:{fourth}@{first}:{second}"
        return f"{first}:{second}@{third}:{fourth}"

    return raw  # host:port без авторизации
