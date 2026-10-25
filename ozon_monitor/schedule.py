"""Local wall-clock schedule, independent of command and process start times."""
from datetime import timedelta
import logging

log = logging.getLogger(__name__)


def next_half_hour(now):
    base = now.replace(minute=0 if now.minute < 30 else 30, second=0, microsecond=0)
    return base + timedelta(minutes=30)


class DailyReporter:
    def __init__(self, store, telegram, stop, now):
        self.store, self.telegram, self.stop, self.now = store, telegram, stop, now

    def send_due(self):
        now = self.now()
        date = now.date().isoformat()
        if now.hour < 12 or self.store.get('daily_report_date') == date:
            return False
        summary = self.store.get('last_full_summary', 'Полных проверок ещё не было.')
        message = (f'Ежедневный отчёт Ozon · {now:%d.%m %H:%M}\n'
                   f'Состояние: {self.store.get("worker_state", "запуск")}\n'
                   f'Включённых целей: {len(self.store.targets(True))}\n'
                   f'Следующая проверка: {self.store.get("next_check", "ещё не назначена")}\n'
                   f'Последний завершённый полный обход:\n{summary}')
        self.telegram.send_text(message)
        self.store.set('daily_report_date', date)
        return True

    def run(self):
        while not self.stop.is_set():
            try:
                self.send_due()
            except Exception as exc:
                log.error('Ежедневный отчёт: %s', exc if isinstance(exc, RuntimeError) else type(exc).__name__)
                self.stop.wait(30)
                continue
            self.stop.wait(1)
