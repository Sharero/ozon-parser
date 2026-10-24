"""Single browser worker plus independently polled Telegram commands."""
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
import logging
import threading
import time
from .exclusions import title_key
from .bot import BotController, BotPoller
from .diagnostics import AgeConfirmationRequired

log = logging.getLogger(__name__)


@dataclass
class Result:
    name: str
    limit: Decimal = Decimal('550')
    checked: int = 0
    notified: int = 0
    excluded: int = 0
    errors: int = 0
    minimum: Decimal | None = None
    cutoff: bool = False
    detail: str = ''

    def summary(self):
        minimum = f'; минимальная прочитанная цена Ozon Банка {self.minimum} ₽' if self.minimum is not None else '; цена не подтверждена'
        end = '; остановка выше порога' if self.cutoff else ''
        error = f'\nОшибка: {self.detail[:350]}' if self.detail else ''
        return (f'{self.name} · порог <{self.limit} ₽: проверено {self.checked}, уведомлений {self.notified}, '
                f'исключено {self.excluded}, ошибок {self.errors}{minimum}{end}{error}')


def source_factory(directory, target, max_pages, delay):
    from .upstream_source import UpstreamOzon
    return UpstreamOzon(directory, max_pages, delay, search_url=target.url, kind=target.kind, strict_metadata=False)


class Monitor:
    def __init__(self, store, telegram, directory, stop, factory=source_factory, max_pages=100, delay=3):
        self.store, self.telegram, self.directory, self.stop = store, telegram, directory, stop
        self.factory, self.max_pages, self.delay = factory, max_pages, delay

    def age_alert(self, target, url):
        message = (f'Ozon: требуется подтверждение возраста\nМониторинг #{target.id}: {target.name}\n'
                   f'{url}\nПроверка этой цели прервана, бот продолжает работать. '
                   'Остановите сервис и откройте эту ссылку командой main.py --login '
                   'в графической сессии с тем же DATA_DIR. Подтвердите возраст на сайте. '
                   'Следующая попытка — по расписанию или /check.')
        try:
            self.telegram.send_text(message)
        except RuntimeError as exc:
            log.error('Не удалось отправить предупреждение о возрасте: %s', exc)

    def target_cycle(self, original):
        result = Result(original.name, limit=original.price_limit)
        source = None
        try:
            current, _, _, _ = self.store.snapshot(original.id)
            if current is None or not current.enabled:
                result.detail = 'Мониторинг отключён через Telegram'
                return result
            original = current
            result.limit = current.price_limit
            source = self.factory(self.directory, original, self.max_pages, self.delay)
            urls = source.discover()
            log.info('Мониторинг #%s %s: найдено карточек %s', original.id, original.name, len(urls))
            for url in urls:
                if self.stop.is_set():
                    result.detail = 'Остановка сервиса'
                    break
                target, _, _, _ = self.store.snapshot(original.id)
                if target is None or not target.enabled:
                    result.detail = 'Мониторинг отключён через Telegram'
                    break
                result.checked += 1
                try:
                    book = source.book(url)
                    # Re-read settings after loading the card, so commands apply during an active run.
                    target, limit, revision, excluded = self.store.snapshot(original.id)
                    result.limit = limit
                    if target is None or not target.enabled:
                        result.detail = 'Мониторинг отключён через Telegram'
                        break
                    purchased = title_key(book.title, book.author) in excluded
                    eligible, reason, unknown = target.evaluate(book, limit)
                    if not purchased and book.card_price is not None and (eligible or reason.startswith('Цена вне порога')):
                        result.minimum = min(result.minimum, book.card_price) if result.minimum is not None else book.card_price
                    if target.sorted_by_price and book.card_price is not None and book.card_price > limit:
                        if purchased:
                            result.excluded += 1
                        elif not unknown:
                            self.store.record(target.id, book, False, revision)
                        result.cutoff = True
                        log.info('Мониторинг #%s: %s ₽ выше %s ₽ — прекращаю эту серию', target.id, book.card_price, limit)
                        break
                    if purchased:
                        result.excluded += 1
                        log.info('SKU %s: исключена купленная книга %s', book.sku, book.title)
                        continue
                    if unknown:
                        raise RuntimeError(reason)
                    notify = self.store.needs_notification(target.id, book, eligible, revision)
                    log.info('#%s SKU %s: %s ₽, порог <%s ₽, %s', target.id, book.sku, book.card_price, limit, reason)
                    if notify:
                        self.telegram.send(book)
                        result.notified += 1
                    self.store.record(target.id, book, eligible, revision, notified=notify)
                except AgeConfirmationRequired as exc:
                    result.errors += 1
                    result.detail = str(exc)
                    self.age_alert(original, url)
                    break
                except Exception as exc:
                    result.errors += 1
                    result.detail = str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__
                    log.error('Мониторинг #%s, карточка: %s', original.id, result.detail)
        except AgeConfirmationRequired as exc:
            result.errors += 1
            result.detail = str(exc)
            self.age_alert(original, original.url)
        except Exception as exc:
            result.errors += 1
            result.detail = str(exc) if isinstance(exc, (RuntimeError, ImportError)) else type(exc).__name__
            log.error('Мониторинг #%s: %s', original.id, result.detail)
        finally:
            if source is not None:
                try:
                    source.close()
                except Exception:
                    log.warning('Ошибка закрытия браузера мониторинга #%s', original.id)
        return result

    def run_all(self, target_ids=None):
        self.store.set('worker_state', 'проверка цен')
        self.store.set('last_started', datetime.now().isoformat(timespec='seconds'))
        targets = self.store.targets(enabled_only=True)
        if target_ids is not None:
            targets = [target for target in targets if target.id in target_ids]
        results = []
        for target in targets:
            if self.stop.is_set():
                break
            self.store.set('worker_state', f'проверка #{target.id}: {target.name}')
            results.append(self.target_cycle(target))
        summary = 'Проверка Ozon · ' + datetime.now().strftime('%d.%m %H:%M') + '\n'
        summary += '\n\n'.join(result.summary() for result in results) if results else 'Нет включённых целей мониторинга.'
        self.store.set('last_summary', summary)
        self.store.set('worker_state', 'ожидание')
        log.info('%s', summary)
        if self.store.get('reports') == 'on' and not self.stop.is_set():
            try:
                self.telegram.send_text(summary)
            except RuntimeError as exc:
                log.error('Часовой отчёт Telegram: %s', exc)
        return all(result.errors == 0 for result in results)


def run_service(store, telegram, directory, admin_id, stop, max_pages=100, delay=3, interval=3600):
    wake = threading.Event()
    controller = BotController(store, telegram, admin_id, wake)
    poller = threading.Thread(target=BotPoller(controller, stop).run, name='telegram-commands', daemon=True)
    poller.start()
    monitor = Monitor(store, telegram, directory, stop, max_pages=max_pages, delay=delay)
    due = time.monotonic()
    try:
        while not stop.is_set():
            remaining = due - time.monotonic()
            if remaining > 0 and not store.has_checks():
                stop.wait(min(1, remaining))
                continue
            wake.clear()
            started = time.monotonic()
            scheduled = remaining <= 0
            requested = store.take_checks()
            if scheduled:
                due = started + interval
                store.set('next_check', (datetime.now() + timedelta(seconds=interval)).strftime('%d.%m.%Y %H:%M:%S'))
            try:
                if scheduled or 0 in requested:
                    monitor.run_all()
                elif requested:
                    monitor.run_all(target_ids=requested)
            except Exception as exc:
                log.error('Цикл мониторинга: %s', exc if isinstance(exc, RuntimeError) else type(exc).__name__)
                store.set('worker_state', 'ошибка цикла, следующая попытка по расписанию')
    finally:
        stop.set()
        store.set('worker_state', 'сервис остановлен')
        poller.join(timeout=30)
