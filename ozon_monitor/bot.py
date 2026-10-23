"""Owner-only Telegram commands; runs independently of the browser worker."""
import logging
import time
from datetime import datetime

log = logging.getLogger(__name__)

MENU = {'keyboard': [['📋 Мониторинг', '💰 Порог'], ['➕ Серия', '➕ Книга'],
                     ['🚫 Исключить', '📚 Исключения'], ['🔄 Проверить', 'ℹ️ Статус']],
        'resize_keyboard': True}
COMMANDS = [
    ('start', 'Меню и помощь'), ('list', 'Серии и книги'), ('price', 'Порог цены: /price 400'),
    ('addseries', 'Добавить: URL | точная серия | автор (необязательно)'),
    ('addbook', 'Добавить: URL | название (необязательно)'),
    ('exclude', 'Исключить название книги'), ('include', 'Вернуть название в поиск'),
    ('exclusions', 'Список исключений'), ('pause', 'Приостановить: /pause ID'),
    ('resume', 'Возобновить: /resume ID'), ('remove', 'Убрать из мониторинга: /remove ID'),
    ('check', 'Запросить проверку сейчас'), ('status', 'Состояние сервиса'),
    ('reports', 'Часовые отчёты: /reports on или off'), ('cancel', 'Отменить ввод'),
]
HELP = '''Управление мониторингом Ozon:
/price 400 — общий порог (цена строго ниже)
/list — список мониторинга с ID
/addseries URL | Название серии | Автор — добавить серию; автор необязателен. Серия по умолчанию только в твёрдом переплёте, сортировка по цене.
/addbook URL | Название — отслеживать конкретную книгу; название необязательно, переплёт соответствует выбранной карточке.
/exclude Название — исключить купленную книгу
/include Название — убрать исключение
/exclusions — все исключения
/pause ID, /resume ID — остановить/возобновить цель
/remove ID — отключить цель
/check — проверить сейчас
/status — состояние и результат последней проверки
/reports on или off — часовые отчёты
/cancel — отменить ввод
Настройки сохраняются после перезапуска.'''


class BotController:
    def __init__(self, store, telegram, admin_id, wake):
        self.store, self.telegram, self.admin_id, self.wake = store, telegram, int(admin_id), wake

    def reply(self, text):
        self.telegram.send_text(text, chat_id=self.admin_id, reply_markup=MENU)

    def handle(self, update):
        message = update.get('message', {})
        # Commands are accepted only in the owner's private chat, not from forwarded identities or groups.
        if message.get('from', {}).get('id') != self.admin_id or message.get('chat', {}).get('id') != self.admin_id:
            return
        text = message.get('text', '').strip()
        if not text:
            return
        buttons = {'📋 Мониторинг': '/list', '💰 Порог': '/price', '➕ Серия': '/addseries',
                   '➕ Книга': '/addbook', '🚫 Исключить': '/exclude', '📚 Исключения': '/exclusions',
                   '🔄 Проверить': '/check', 'ℹ️ Статус': '/status'}
        text = buttons.get(text, text)
        if text.startswith('/'):
            command, _, argument = text.partition(' ')
            command = command.split('@', 1)[0].lower()
            self.store.set('pending_action', '')
        else:
            command, argument = self.store.get('pending_action'), text
            if not command:
                self.reply('Выберите действие кнопкой или отправьте /help.')
                return
        try:
            response = self.execute(command, argument.strip())
        except (ValueError, TypeError) as exc:
            self.reply(str(exc) or 'Неверный формат команды. /help')
            return
        self.reply(response)

    def execute(self, command, argument):
        if command in ('/start', '/help'):
            return HELP
        prompts = {
            '/price': f"Сейчас порог {self.store.get('price_limit')} ₽. Отправьте новый порог, например 400. /cancel — отмена.",
            '/limit': f"Сейчас порог {self.store.get('price_limit')} ₽. Отправьте новый порог, например 400.",
            '/exclude': 'Отправьте полное название купленной книги. /cancel — отмена.',
            '/include': 'Отправьте название, которое нужно убрать из исключений.',
            '/addseries': 'Отправьте: ссылка на серию Ozon | точное название серии | автор (необязательно).',
            '/addbook': 'Отправьте полную ссылку на книгу Ozon. Можно добавить | название.',
        }
        if command in prompts and not argument:
            self.store.set('pending_action', command)
            return prompts[command]
        if command in ('/price', '/limit'):
            amount = self.store.set_price(argument)
            self.store.set('pending_action', '')
            self.wake.set()
            return f'Порог изменён: строго ниже {amount} ₽ с Ozon Картой. Проверка запрошена. Уже начатая обработка карточки может завершиться перед применением новых настроек.'
        if command == '/exclude':
            self.store.exclude(argument)
            self.store.set('pending_action', '')
            return f'Исключена книга: {argument}'
        if command == '/include':
            changed = self.store.include(argument)
            self.store.set('pending_action', '')
            return f'Книга возвращена в мониторинг: {argument}' if changed else 'Такого названия в исключениях нет.'
        if command == '/exclusions':
            titles = self.store.exclusions().values()
            return 'Исключённые книги:\n' + '\n'.join(titles) if titles else 'Исключений нет.'
        if command in ('/addseries', '/addbook'):
            parts = [part.strip() for part in argument.split('|')]
            if len(parts) > 3 or not parts[0]:
                raise ValueError('Формат: URL | название | автор (только для серии)')
            kind = 'series' if command == '/addseries' else 'book'
            if kind == 'book' and len(parts) > 2:
                raise ValueError('Для книги формат: URL | название')
            name = parts[1] if len(parts) > 1 else ''
            author = parts[2] if len(parts) > 2 else ''
            target = self.store.add_target(kind, parts[0], name, name if kind == 'series' else '', author)
            self.store.set('pending_action', '')
            self.wake.set()
            return f'Добавлено: ID {target.id}, {target.name}\n{target.url}\nПроверка запрошена.'
        if command == '/list':
            targets = self.store.targets()
            return '\n\n'.join(f"ID {t.id} · {'включено' if t.enabled else 'пауза'} · {'серия' if t.kind == 'series' else 'книга'}\n{t.name}\n{t.url}" for t in targets) or 'Нет целей мониторинга.'
        if command in ('/pause', '/resume', '/remove'):
            if not argument.isdecimal() or int(argument) <= 0:
                raise ValueError(f'Укажите ID из /list, например {command} 1')
            target_id = int(argument)
            self.store.enable(target_id, command == '/resume')
            if command == '/resume':
                self.wake.set()
            return f"Мониторинг ID {target_id}: {'включён' if command == '/resume' else 'отключён'}. /resume {target_id} — возобновить."
        if command == '/check':
            self.wake.set()
            return 'Проверка запрошена. Если обход уже идёт, следующая проверка начнётся после него.'
        if command == '/status':
            return (f"Порог: <{self.store.get('price_limit')} ₽\n"
                    f"Включённых целей: {len(self.store.targets(True))}\n"
                    f"Состояние: {self.store.get('worker_state', 'запуск')}\n"
                    f"Следующая плановая проверка: {self.store.get('next_check', 'ещё не назначена')}\n"
                    f"Последний результат:\n{self.store.get('last_summary', 'проверок ещё не было')}")
        if command == '/reports':
            if argument not in ('on', 'off'):
                raise ValueError('Используйте /reports on или /reports off')
            self.store.set('reports', argument)
            return 'Часовые отчёты включены.' if argument == 'on' else 'Часовые отчёты отключены; уведомления о подходящей цене остаются.'
        if command == '/cancel':
            return 'Ввод отменён.'
        return 'Неизвестная команда. /help'


class BotPoller:
    def __init__(self, controller, stop):
        self.controller, self.stop = controller, stop

    def run(self):
        store, telegram = self.controller.store, self.controller.telegram
        try:
            telegram.configure_commands([{'command': command, 'description': description} for command, description in COMMANDS])
        except RuntimeError as exc:
            log.error('Регистрация команд Telegram: %s', exc)
        while not self.stop.is_set():
            try:
                updates = telegram.updates(int(store.get('update_offset', '0')))
                for update in updates:
                    if self.stop.is_set():
                        break
                    self.controller.handle(update)
                    store.set('update_offset', int(update['update_id']) + 1)
            except Exception as exc:
                # Raw urllib exceptions can include the bot URL; never print them.
                log.error('Получение команд Telegram: %s', exc if isinstance(exc, RuntimeError) else type(exc).__name__)
                self.stop.wait(5)
