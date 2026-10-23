import os
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


def search_urls(queries, configured=None):
    configured = os.getenv('OZON_SEARCH_URL', '').strip() if configured is None else configured.strip()
    if configured:
        parts = urlsplit(configured)
        if parts.scheme != 'https' or parts.hostname not in ('ozon.ru', 'www.ozon.ru') or parts.username or parts.password:
            raise ValueError('OZON_SEARCH_URL должен быть HTTPS-ссылкой на ozon.ru')
        return [configured]
    return ['https://www.ozon.ru/search/?' + urlencode({'text': query}) for query in queries]


def page_url(base, number):
    parts = urlsplit(base)
    parameters = [(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True) if key != 'page']
    if number > 1:
        parameters.append(('page', str(number)))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(parameters), ''))
