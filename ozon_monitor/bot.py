"""Owner-only Telegram commands; runs independently of the browser worker."""
import logging
import time
from datetime import datetime

log = logging.getLogger(__name__)

MENU = {'keyboard': [['📋 Мониторинг', '💰 Порог'], ['➕ Серия', '➕ Книга', '➕ Товар'],
                     ['🚫 Исключить', '📚 Исключения'], ['🔄 Проверить', 'ℹ️ Статус']],
        'resize_keyboard': True}
COMMANDS = [
    ('start', 'Меню и помощь'), ('list', 'Серии, книги и товары'), ('price', 'Порог цели: /price ID сумма'),
    ('defaultprice', 'Начальный порог новых целей'),
    ('addseries', 'Добавить: URL | точная серия | автор (необязательно)'),
    ('addbook', 'Добавить: URL | название (необязательно)'),
    ('addproduct', 'Добавить товар: URL | название | порог'),
    ('exclude', 'Исключить название книги'), ('include', 'Вернуть название в поиск'),
    ('exclusions', 'Список исключений'), ('pause', 'Приостановить: /pause ID'),
    ('resume', 'Возобновить: /resume ID'), ('remove', 'Убрать из мониторинга: /remove ID'),
    ('check', 'Запросить проверку сейчас'), ('status', 'Состояние сервиса'),
    ('reports', 'Отчёты о найденных предложениях: /reports on или off'), ('cancel', 'Отменить ввод'),
]
HELP = '''Управление мониторингом Ozon:
/price ID сумма — отдельный порог серии, книги или товара, например /price 2 400 (строго ниже)
/defaultprice 550 — начальный порог только для новых целей
/list — список мониторинга с ID
/addseries URL | Название серии | Автор | Порог — добавить серию; автор и порог необязательны. Подходит ссылка на серию или поиск Ozon. Только твёрдый переплёт, сортировка по цене.
/addbook URL | Название | Порог — отслеживать конкретную книгу; название и порог необязательны, переплёт соответствует карточке.
/addproduct URL | Название | Порог — любой товар; название и порог необязательны
/exclude Название — исключить купленную книгу
/include Название — убрать исключение
/exclusions — все исключения
/pause ID, /resume ID — остановить/возобновить цель
/remove ID — отключить цель
/check [ID] — проверить все цели или одну цель сейчас
/status — состояние и результат последней проверки
/reports on или off — отчёты о найденных предложениях; ежедневный отчёт в 12:00 приходит всегда
/cancel — отменить ввод
Плановые проверки в :00 и :30. Часовой пояс задаётся TZ (по умолчанию Новосибирск). Добавление и изменение настроек не сдвигают график. /check запускает внеплановую проверку. Настройки сохраняются после перезапуска.'''


class BotController:
    def __init__(self, store, telegram, admin_id, wake):
        self.store, self.telegram, self.admin_id, self.wake = store, telegram, int(admin_id), wake

    def reply(self, text):
        self.telegram.send_text(text, chat_id=self.admin_id, reply_markup=MENU)

    def request_check(self, target_id=None, immediate=False):
        self.store.request_check(target_id, immediate=immediate)
        self.wake.set()

    def get_target(self, value):
        if not value.isdecimal() or int(value) <= 0:
            raise ValueError('Укажите ID из /list')
        target = next((t for t in self.store.targets() if t.id == int(value)), None)
        if target is None:
            raise ValueError('Такого ID мониторинга нет')
        return target

    def handle(self, update):
        message = update.get('message', {})
        # Commands are accepted only in the owner's private chat, not from forwarded identities or groups.
        if message.get('from', {}).get('id') != self.admin_id or message.get('chat', {}).get('id') != self.admin_id:
            return
        text = message.get('text', '').strip()
        if not text:
            return
        buttons = {'📋 Мониторинг': '/list', '💰 Порог': '/price', '➕ Серия': '/addseries',
                   '➕ Книга': '/addbook', '➕ Товар': '/addproduct', '🚫 Исключить': '/exclude', '📚 Исключения': '/exclusions',
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
            '/price': 'Отправьте ID и порог, например 2 400. ID и текущие пороги: /list. /cancel — отмена.',
            '/limit': 'Отправьте ID и порог, например 2 400. ID и текущие пороги: /list.',
            '/defaultprice': f"Начальный порог новых целей: {self.store.get('price_limit')} ₽. Отправьте сумму. Существующие цели не изменятся.",
            '/exclude': 'Отправьте полное название купленной книги. /cancel — отмена.',
            '/include': 'Отправьте название, которое нужно убрать из исключений.',
            '/addseries': 'Отправьте: ссылка на серию или поиск Ozon | точное название серии | автор | порог. Автор и порог необязательны; для пропуска автора оставьте пустое поле между |.',
            '/addbook': 'Отправьте полную ссылку на книгу Ozon. Можно добавить | название | порог.',
            '/addproduct': 'Отправьте полную ссылку /product/… на товар Ozon. Можно добавить | название | порог.',
        }
        if command in prompts and not argument:
            self.store.set('pending_action', command)
            return prompts[command]
        if command in ('/price', '/limit'):
            fields = argument.split()
            if len(fields) != 2:
                raise ValueError('У каждой цели свой порог. Используйте /price ID сумма, например /price 2 400. ID: /list. Начальный порог новых целей: /defaultprice 400.')
            target = self.get_target(fields[0])
            amount = self.store.set_price(fields[1], target.id)
            self.store.set('pending_action', '')
            if target.price_limit == amount:
                return f'ID {target.id}: порог уже <{amount} ₽. Новая проверка не нужна.'
            if target.enabled:
                self.request_check(target.id)
            action = 'Проверка по ближайшему расписанию (:00 или :30).' if target.enabled else 'Цель на паузе; проверка начнётся после /resume.'
            return f'ID {target.id}, {target.name}: порог строго ниже {amount} ₽ с Ozon Картой. {action}'
        if command == '/defaultprice':
            amount = self.store.set_price(argument)
            self.store.set('pending_action', '')
            return f'Начальный порог новых целей: <{amount} ₽. Пороги существующих целей не изменены; проверка не запускается.'
        if command == '/exclude':
            self.store.exclude(argument)
            self.store.set('pending_action', '')
            return f'Исключена книга: {argument}'
        if command == '/include':
            changed = self.store.include(argument)
            self.store.set('pending_action', '')
            if changed:
                self.request_check()
            return f'Книга возвращена в мониторинг: {argument}. Проверка включённых целей — по ближайшему расписанию (:00 или :30).' if changed else 'Такого названия в исключениях нет.'
        if command == '/exclusions':
            titles = self.store.exclusions().values()
            return 'Исключённые книги:\n' + '\n'.join(titles) if titles else 'Исключений нет.'
        if command in ('/addseries', '/addbook', '/addproduct'):
            parts = [part.strip() for part in argument.split('|')]
            if len(parts) > 4 or not parts[0]:
                raise ValueError('Формат серии: URL | название | автор | порог. Книги: URL | название | порог.')
            kind = {'/addseries': 'series', '/addbook': 'book', '/addproduct': 'product'}[command]
            if kind != 'series' and len(parts) > 3:
                raise ValueError('Для товара/книги формат: URL | название | порог')
            name = parts[1] if len(parts) > 1 else ''
            author = parts[2] if kind == 'series' and len(parts) > 2 else ''
            limit_index = 3 if kind == 'series' else 2
            limit = parts[limit_index] if len(parts) > limit_index else None
            before = {t.id: t for t in self.store.targets()}
            target = self.store.add_target(kind, parts[0], name, name if kind == 'series' else '', author, limit=limit)
            self.store.set('pending_action', '')
            previous = before.get(target.id)
            changed = previous is None or not previous.enabled or previous.price_revision != target.price_revision
            if changed:
                self.request_check(target.id)
            action = 'Проверка по ближайшему расписанию (:00 или :30).' if changed else 'Условия не изменились; новая проверка не нужна.'
            return f'Сохранено: ID {target.id}, {target.name}\nПорог: <{target.price_limit} ₽\n{target.url}\n{action}'
        if command == '/list':
            targets = self.store.targets()
            labels = {'series': 'серия', 'book': 'книга', 'product': 'товар'}
            return '\n\n'.join(f"ID {t.id} · {'включено' if t.enabled else 'пауза'} · {labels[t.kind]}\n{t.name}\nПорог: <{t.price_limit} ₽\n{t.url}" for t in targets) or 'Нет целей мониторинга.'
        if command in ('/pause', '/resume', '/remove'):
            if not argument.isdecimal() or int(argument) <= 0:
                raise ValueError(f'Укажите ID из /list, например {command} 1')
            target_id = int(argument)
            changed = self.store.enable(target_id, command == '/resume')
            if command == '/resume' and changed:
                self.request_check(target_id)
            return f"Мониторинг ID {target_id}: {'включён' if command == '/resume' else 'отключён'}. /resume {target_id} — возобновить."
        if command == '/check':
            if argument:
                target = self.get_target(argument)
                if not target.enabled:
                    return f'ID {target.id} на паузе. Сначала /resume {target.id}.'
                self.request_check(target.id, immediate=True)
            else:
                self.request_check(immediate=True)
            return 'Проверка запрошена. Если обход уже идёт, следующая проверка начнётся после него.'
        if command == '/status':
            return (f"Пороги индивидуальные: /list. Для новых целей: <{self.store.get('price_limit')} ₽\n"
                    f"Включённых целей: {len(self.store.targets(True))}\n"
                    f"Состояние: {self.store.get('worker_state', 'запуск')}\n"
                    f"Следующая плановая проверка: {self.store.get('next_check', 'ещё не назначена')}\n"
                    f"Последний результат:\n{self.store.get('last_summary', 'проверок ещё не было')}")
        if command == '/reports':
            if argument not in ('on', 'off'):
                raise ValueError('Используйте /reports on или /reports off')
            self.store.set('reports', argument)
            return ('Отчёты о найденных предложениях включены.' if argument == 'on' else 'Отчёты о найденных предложениях отключены; уведомления о цене остаются.') + ' Ежедневный отчёт в 12:00 приходит всегда.'
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
