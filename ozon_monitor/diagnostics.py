"""Actionable startup errors without printing credentials or browser arguments."""


class AgeConfirmationRequired(RuntimeError):
    """Manual confirmation is needed; do not wait for input in the worker."""


def check_age_confirmation(text: str):
    import re
    if re.search(r'(?:введите|укажите)\s+(?:вашу\s+)?дату\s+рождения|подтвердите\s+(?:свой\s+)?возраст', text, re.I):
        raise AgeConfirmationRequired(
            'Ozon запрашивает подтверждение возраста. Остановите сервис и в графической сессии '
            'выполните: python main.py --login "URL_ЭТОЙ_КНИГИ". '
            'Подтвердите возраст на сайте и сохраните сессию; используйте тот же DATA_DIR и маршрут сети.')


def browser_error_message(error: Exception) -> str:
    detail = str(error).lower()
    if 'executable doesn\'t exist' in detail or 'executable does not exist' in detail:
        return ('Chromium не установлен. Выполните тем же Python, которым запускаете сервис: '
                'python -m playwright install chromium')
    if 'singleton' in detail or 'processsingleton' in detail:
        return ('Профиль Chromium уже используется. Закройте другой экземпляр сервиса '
                'или браузер, открытый через --login.')
    if 'missing x server' in detail or 'missing xserver' in detail or '$display' in detail:
        return ('Нет графического дисплея. Для фонового запуска укажите HEADLESS=true в .env; '
                '--login запускайте в графической сессии.')
    if 'operation not permitted' in detail:
        return ('Среда запрещает запуск Chromium (Operation not permitted). '
                'Запустите сервис в обычном терминале вашей ОС вне ограниченной среды Codex.')
    if 'host system is missing dependencies' in detail or 'error while loading shared libraries' in detail:
        return ('Не хватает системных библиотек Chromium. Установите их командой '
                'python -m playwright install-deps chromium.')
    return ('Не удалось запустить Chromium. Проверьте установку: python -m playwright install chromium; '
            'проверьте, что профиль data/browser не открыт другим процессом.')


def ozon_access_error(text: str, status: int = 200) -> str | None:
    import re
    incident = re.search(r'fab_[A-Za-z0-9_]+', text)
    if incident or re.search(r'похоже,?\s*нет соединения', text, re.I):
        suffix = f' Инцидент: {incident.group(0)}.' if incident else ''
        return ('Ozon показывает страницу отказа доступа «Похоже, нет соединения».' + suffix +
                ' Цены недоступны. Проверьте тот же поиск в обычном браузере вне сервиса; '
                'если ошибка повторяется, используйте «Обратиться в поддержку» на странице Ozon. '
                'Повторный ввод Telegram-токена эту ошибку не исправит.')
    if status == 403:
        return ('Ozon HTTP 403: доступ к поиску запрещён. '
                'Проверьте тот же поиск в обычном браузере вне сервиса. Цены не получены.')
    if status >= 400:
        return f'Ozon HTTP {status}'
    try:
        check_age_confirmation(text)
    except AgeConfirmationRequired as exc:
        return str(exc)
    if re.search(r'доступ ограничен|подтвердите, что вы не робот|проверка безопасности|введите код с картинки', text, re.I):
        return 'Ozon требует проверки в браузере: запустите --login и убедитесь, что доступен поиск.'
    return None
