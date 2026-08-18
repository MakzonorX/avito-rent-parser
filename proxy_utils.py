"""Нормализация строки прокси: пользователи вводят её в разных форматах."""

# socks5h — DNS резолвится на стороне прокси, для парсинга нужно именно так
SCHEME_ALIASES = {"socks5": "socks5h", "socks4": "socks4a"}


def normalize_proxy(raw: str) -> str:
    """
    Приводит прокси к виду, который понимают http-клиенты.

    Принимает: user:pass@host:port, host:port@user:pass, host:port:user:pass,
               user:pass:host:port, host:port — с любой схемой или без неё.
    Схему сохраняет (socks5://, socks5h://, http://); без схемы возвращает
    голую строку — вызывающий код сам подставит http://.
    """
    raw = (raw or "").strip()
    if not raw:
        return ""

    scheme = ""
    if "://" in raw:
        scheme, raw = raw.split("://", 1)
        scheme = scheme.strip().lower()
        scheme = SCHEME_ALIASES.get(scheme, scheme) + "://"
    raw = raw.strip().rstrip("/")

    if "@" in raw:
        left, right = raw.split("@", 1)
        # хост — та часть, где есть точка (ip или домен)
        if "." in left and "." not in right:
            left, right = right, left
        return f"{scheme}{left}@{right}"

    parts = raw.split(":")
    if len(parts) == 4:
        first, second, third, fourth = parts
        if "." in first:  # host:port:user:pass
            return f"{scheme}{third}:{fourth}@{first}:{second}"
        return f"{scheme}{first}:{second}@{third}:{fourth}"

    return f"{scheme}{raw}"  # host:port без авторизации


def proxy_url(raw: str) -> str:
    """Готовый URL прокси со схемой — http:// подставляется, если не указана другая."""
    proxy = normalize_proxy(raw)
    if not proxy:
        return ""
    return proxy if "://" in proxy else f"http://{proxy}"
