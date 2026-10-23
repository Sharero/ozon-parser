"""Explicit egress proxy validation; never modifies host VPN routes."""
from urllib.parse import urlsplit


def proxy_url(value, socks=True):
    if not value:
        return ''
    parts = urlsplit(value)
    schemes = ('http', 'https', 'socks5', 'socks5h') if socks else ('http', 'https')
    if parts.scheme not in schemes or not parts.hostname or parts.port is None or not 1 <= parts.port <= 65535:
        raise ValueError('Прокси должен иметь формат http://host:port или socks5://host:port (Telegram: только HTTP/HTTPS)')
    if parts.username or parts.password or parts.query or parts.fragment or parts.path not in ('', '/'):
        raise ValueError('Для прокси нужен URL без логина, пароля, пути и параметров')
    return value.rstrip('/')
