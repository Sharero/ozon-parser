import json
import time
import os
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen, build_opener, ProxyHandler
from .models import Book
from .network import proxy_url


class Telegram:
    def __init__(self, token: str, chat_id: str):
        self.token, self.chat_id = token, chat_id
        proxy = proxy_url(os.getenv('TELEGRAM_PROXY_URL', '').strip(), socks=False)
        self.opener = build_opener(ProxyHandler({'https': proxy, 'http': proxy})) if proxy else None

    def check(self):
        """Read-only credential and chat access checks; does not send messages."""
        self._request('getMe', {})
        self._request('getChat', {'chat_id': self.chat_id})

    def send(self, book: Book):
        details = ' · '.join(part for part in (book.author, book.series) if part)
        text = '\n'.join(part for part in (book.title[:600], details[:600], book.binding,
                                         f'{book.card_price} ₽ с Ozon Картой', book.url) if part)
        return self.send_text(text)

    def send_text(self, text, chat_id=None, reply_markup=None):
        result = None
        while text:
            chunk = text[:3500]
            if len(text) > 3500 and '\n' in chunk:
                chunk = chunk[:chunk.rfind('\n') + 1]
            payload = {'chat_id': chat_id if chat_id is not None else self.chat_id,
                       'text': chunk, 'link_preview_options': {'is_disabled': True}}
            if reply_markup:
                payload['reply_markup'] = reply_markup
            result = self._request('sendMessage', payload)
            text = text[len(chunk):]
        return result

    def updates(self, offset):
        return self._request('getUpdates', {'offset': offset, 'timeout': 15, 'allowed_updates': ['message']}, attempts=1, timeout=25) or []

    def configure_commands(self, commands):
        info = self._request('getWebhookInfo', {})
        if info and info.get('url'):
            raise RuntimeError('У бота активен webhook. Отключите прежний webhook перед запуском polling-сервиса.')
        self._request('setMyCommands', {'commands': commands})

    def _request(self, method: str, payload: dict, attempts=3, timeout=30):
        data = json.dumps(payload).encode()
        for attempt in range(attempts):
            retry_after = 2 ** attempt
            try:
                request = Request(f'https://api.telegram.org/bot{self.token}/{method}',
                                  data=data, headers={'Content-Type': 'application/json'})
                with (self.opener.open(request, timeout=timeout) if self.opener else urlopen(request, timeout=timeout)) as response:
                    result = json.load(response)
                if not result.get('ok'):
                    raise RuntimeError('Telegram отклонил сообщение')
                return result.get('result')
            except HTTPError as exc:
                if exc.code != 429 and exc.code < 500:
                    messages = {
                        400: 'Telegram: неверный chat ID или чат недоступен. Отправьте боту /start и проверьте TELEGRAM_CHAT_ID.',
                        401: 'Telegram: токен отклонён. Проверьте TELEGRAM_BOT_TOKEN из @BotFather.',
                        403: 'Telegram: доступ запрещён. Разблокируйте бота, отправьте /start; для канала проверьте права бота.',
                        404: 'Telegram: неверный токен или адрес метода.',
                        409: 'Telegram: getUpdates уже используется другим процессом или активен webhook.',
                    }
                    exc.close()
                    raise RuntimeError(messages.get(exc.code, f'Telegram HTTP {exc.code}')) from None
                if exc.code == 429:
                    try:
                        retry_after = min(120, int(json.load(exc)['parameters']['retry_after']))
                    except (ValueError, KeyError, TypeError):
                        pass
                exc.close()
            except (URLError, TimeoutError):
                if attempt == attempts - 1:
                    raise RuntimeError('Telegram недоступен по сети: проверьте DNS, подключение и HTTPS-доступ к api.telegram.org') from None
            if attempt < attempts - 1:
                time.sleep(retry_after)
        raise RuntimeError('Telegram не ответил успешно после повторных попыток')
