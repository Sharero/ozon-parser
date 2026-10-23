"""Ozon series monitor using the vendored Selenium/curl_cffi client."""
import argparse
import logging
import os
from pathlib import Path
import signal
import threading
import time
from dotenv import load_dotenv
from ozon_monitor.models import PRICE_LIMIT
from ozon_monitor.state import State
from ozon_monitor.exclusions import load_exclusions, title_key
from ozon_monitor.telegram import Telegram
from ozon_monitor.upstream_source import UpstreamOzon

log = logging.getLogger('monitor')


def run_cycle(source, state, telegram, excluded_titles=frozenset()):
    """Compatibility helper; the CLI service uses service.Monitor and persistent rules."""
    urls = source.discover()
    errors = notifications = excluded = checked = 0
    log.info('Найдено карточек: %s', len(urls))
    for url in urls:
        checked += 1
        try:
            book = source.book(url)
            is_excluded = title_key(book.title) in excluded_titles
            if book.card_price is not None and book.card_price > PRICE_LIMIT:
                if is_excluded:
                    excluded += 1
                else:
                    state.record(book)
                log.info('SKU %s: цена с Ozon Картой=%s ₽; %s; прекращаю обход — цена выше %s ₽',
                         book.sku, book.card_price, book.rejection_reason, PRICE_LIMIT)
                break
            if is_excluded:
                excluded += 1
                log.info('SKU %s: %r — исключена как купленная книга', book.sku, book.title)
                continue
            notify = state.needs_notification(book)
            reason = book.rejection_reason or ('уведомление' if notify else 'уже уведомляли об этой цене')
            log.info('SKU %s: цена с Ozon Картой=%s ₽; серия=%r; переплёт=%r; доступность=%s; %s',
                     book.sku, book.card_price, book.series, book.binding, book.available, reason)
            if notify:
                telegram.send(book)
                notifications += 1
            state.record(book, notified=notify)
        except Exception as exc:
            errors += 1
            # Never log Telegram request URLs, which contain the bot token.
            log.error('Карточка %s: %s', url, type(exc).__name__)
            if isinstance(exc, RuntimeError):
                log.error('%s', exc)
    log.info('Обход завершён: карточек=%s, проверено=%s, уведомлений=%s, исключено=%s, ошибок=%s', len(urls), checked, notifications, excluded, errors)
    return errors == 0


def main():
    parser = argparse.ArgumentParser(description='Мониторинг книг Ozon с управлением через Telegram')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--once', action='store_true', help='Один обход')
    mode.add_argument('--login', nargs='?', const='https://www.ozon.ru/', metavar='OZON_URL',
                      help='Ручная настройка сессии; можно указать URL книги для подтверждения возраста')
    mode.add_argument('--inspect', metavar='PRODUCT_URL', help='Сохранить диагностику блока цены одной книги без Telegram')
    mode.add_argument('--check', action='store_true', help='Проверить Telegram без отправки сообщений')
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    load_dotenv(root / '.env')
    if hasattr(time, 'tzset'):
        time.tzset()
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    token = os.getenv('TELEGRAM_BOT_TOKEN', '').strip()
    chat = os.getenv('TELEGRAM_CHAT_ID', '').strip()
    if not (args.login or args.inspect) and (not token or not chat):
        parser.error('Заполните TELEGRAM_BOT_TOKEN и TELEGRAM_CHAT_ID в .env')
    if args.check:
        try:
            Telegram(token, chat).check()
        except RuntimeError as exc:
            log.error('%s', exc)
            return 1
        log.info('Telegram: токен принят, чат доступен. Сообщения не отправлялись.')
        return 0
    directory = Path(os.getenv('DATA_DIR', 'data'))
    if not directory.is_absolute():
        directory = root / directory
    directory = directory.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    try:
        max_pages = int(os.getenv('MAX_SEARCH_PAGES', '100'))
        delay = float(os.getenv('REQUEST_DELAY_SECONDS', '3'))
        if max_pages < 1 or delay < 0:
            raise ValueError
    except ValueError:
        parser.error('MAX_SEARCH_PAGES должен быть целым >0; REQUEST_DELAY_SECONDS числом >=0')
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    import fcntl
    with (directory / 'monitor.lock').open('w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            parser.error('Сервис уже запущен с этим DATA_DIR')
        source = None
        if args.inspect or args.login:
            try:
                source = UpstreamOzon(directory, max_pages, delay)
            except ValueError as exc:
                parser.error(str(exc))
            except ImportError:
                log.error('Не установлены зависимости: .venv/bin/python -m pip install -r requirements.txt')
                return 1
        if args.inspect:
            try:
                path = source.inspect_price(args.inspect)
                log.info('Диагностика готова: %s', path)
                return 0
            except Exception as exc:
                log.error('Диагностика не выполнена: %s', exc if isinstance(exc, (RuntimeError, ValueError)) else type(exc).__name__)
                return 1
            finally:
                source.close()
        if args.login:
            try:
                source.login(args.login)
            except Exception as exc:
                log.error('Cookies не подтверждены: %s', exc if isinstance(exc, RuntimeError) else type(exc).__name__)
                return 1
            finally:
                source.close()
            return 0
        exclusions_file = Path(os.getenv('EXCLUDED_BOOKS_FILE', 'excluded_books.txt'))
        if not exclusions_file.is_absolute():
            exclusions_file = root / exclusions_file
        from ozon_monitor.store import Store
        from ozon_monitor.service import Monitor, run_service
        from ozon_monitor.rules import DEFAULT_SEARCH_URL
        store = Store(directory / 'state.sqlite3')
        try:
            store.initialize(os.getenv('OZON_SEARCH_URL', '').strip() or DEFAULT_SEARCH_URL, exclusions_file)
        except (ValueError, OSError) as exc:
            parser.error(str(exc))
        telegram = Telegram(token, chat)
        if args.once:
            return 0 if Monitor(store, telegram, directory, stop, max_pages=max_pages, delay=delay).run_all() else 1
        admin = os.getenv('TELEGRAM_ADMIN_ID', '').strip() or chat
        if not admin.isdecimal() or int(admin) <= 0:
            parser.error('Для управления укажите положительный TELEGRAM_ADMIN_ID (ID вашего личного Telegram-аккаунта)')
        log.info('Сервис запущен: проверка каждые 60 минут, управление через Telegram')
        run_service(store, telegram, directory, int(admin), stop, max_pages, delay)

    return 0


if __name__ == '__main__':
    raise SystemExit(main())
