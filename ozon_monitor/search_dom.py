"""Recognize product links across Ozon search layouts, without generated classes."""
from .diagnostics import ozon_access_error

SEARCH_SNAPSHOT = r"""() => {
    const known = [...document.querySelectorAll(
        '[data-widget="searchResultsV2"], [data-widget="searchResults"], [data-widget="tileGridDesktop"]')];
    const roots = known.length ? known : [document.querySelector('main, [role="main"]') || document.body];
    const visible = el => el.getClientRects().length > 0 && getComputedStyle(el).visibility !== 'hidden';
    const links = new Set();
    for (const root of roots) for (const a of root.querySelectorAll('a[href*="/product/"]')) {
        if (!visible(a) || a.closest('header, footer, nav')) continue;
        try {
            const url = new URL(a.href, location.href);
            if (['ozon.ru', 'www.ozon.ru'].includes(url.hostname) &&
                /^\/product\/(?:[^/]*-)?\d+\/?$/.test(url.pathname)) links.add(url.href);
        } catch (_) {}
    }
    const nextLinks = [], nextLabels = [];
    for (const a of document.querySelectorAll('a[href]')) {
        if (!visible(a)) continue;
        try {
            const url = new URL(a.href, location.href);
            if (url.origin === location.origin && (url.pathname.startsWith('/search') || url.pathname === location.pathname) && url.searchParams.has('page'))
                nextLinks.push(url.href);
        } catch (_) {}
    }
    for (const el of document.querySelectorAll('a, button, [role="button"]')) {
        if (visible(el) && !el.disabled && el.getAttribute('aria-disabled') !== 'true' &&
            /^(дальше|далее|следующая)$/i.test((el.innerText || '').trim())) nextLabels.push(el.innerText);
    }
    const body = document.body.innerText || '';
    const incident = body.match(/fab_[A-Za-z0-9_]+/);
    const blocked = body.match(/похоже,?\s*нет соединения|доступ ограничен|подтвердите, что вы не робот|проверка безопасности|введите код с картинки/i);
    return {path: location.pathname, query: new URL(location.href).searchParams.get('text') || '', title: document.title.slice(0, 160), links: [...links],
        empty: links.size === 0 && /ничего не найдено|не нашли.*запрос/i.test(body),
        next_links: nextLinks, next_button: nextLabels.length > 0,
        challenge: (blocked?.[0] || '') + ' ' + (incident?.[0] || ''),
        widgets: [...new Set([...document.querySelectorAll('[data-widget]')]
            .map(el => el.getAttribute('data-widget')))].slice(0, 25)};
}"""


def validate_search(snapshot):
    error = ozon_access_error(snapshot.get('challenge', ''))
    if error:
        raise RuntimeError(error)
    path = snapshot.get('path', '')
    if not (path.startswith('/search') or (path.startswith('/category/') and snapshot.get('query')) or path.startswith('/series/')):
        raise RuntimeError('Вкладка ушла со страницы поиска: ' + snapshot.get('path', '?') +
                           '. Не нажимайте на странице во время автоматического обхода.')
    return bool(snapshot.get('links') or snapshot.get('empty'))


def describe_search(snapshot):
    return (f"путь={snapshot.get('path', '?')}, заголовок={snapshot.get('title', '')!r}, "
            f"ссылок={len(snapshot.get('links', []))}, "
            f"виджеты={','.join(snapshot.get('widgets', [])) or 'нет'}")
