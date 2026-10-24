# Archer AX53: работа без домашнего компьютера

Роутер принимает OpenVPN-подключение контейнера `router-vpn` на Ubuntu. Парсер обращается к HTTP-прокси `http://router-vpn:8888`. VPN не использует сеть хоста и не меняет маршруты Ubuntu или существующий VPN на сервере. Telegram остаётся на обычном маршруте парсера. Роутер и домашний интернет должны работать постоянно, компьютер после проверки можно выключить.

Используется Gluetun с [собственным OpenVPN-профилем](https://github.com/qdm12/gluetun-wiki/blob/main/setup/openvpn-configuration-file.md) и его [HTTP-прокси](https://github.com/qdm12/gluetun-wiki/blob/main/setup/options/http-proxy.md). Firewall Gluetun оставлен включённым: при разрыве VPN прокси не должен переключаться на обычный серверный маршрут. Порт прокси не публикуется на Ubuntu. `NET_ADMIN` выдаётся только VPN-контейнеру.

## Профиль

В OpenVPN-сервере Archer должен быть выбран доступ «Интернет и домашняя сеть». WAN-адрес роутера должен быть доступен извне. Если есть вышестоящий роутер/NAT, нужен соответствующий проброс VPN-порта; CGNAT может препятствовать подключению.

На Ubuntu в каталоге проекта:

```bash
install -d -m 700 vpn
```

Дома скопируйте экспортированный файл (подставьте настоящий путь):

```bash
scp '/путь/к/скачанному.ovpn' root@185.236.20.26:/root/ozon-parser/vpn/home.ovpn
```

На Ubuntu:

```bash
chmod 600 vpn/home.ovpn
```

Каталог `vpn/` и файлы `.ovpn` исключены из Git и контекста сборки Docker. Внутри профиля могут быть приватные ключи: не присылайте его содержимое в чат и не публикуйте.

Проверьте локально строку `remote` в профиле: она должна указывать на актуальный публичный адрес домашнего роутера. Для custom-профилей Gluetun требует IP вместо доменного имени. Если там домен, сначала разрешите его на Ubuntu, затем замените только адрес в строке `remote`, сохранив порт. При изменении домашнего публичного IP потребуется актуализировать адрес и перезапустить `router-vpn`. Автоматическое обновление endpoint в этой конфигурации не настроено.

Обычный экспорт Archer содержит встроенные `<ca>`, `<cert>`, `<key>`. Если профиль ссылается на отдельные файлы сертификатов или требует логин/пароль, эта базовая конфигурация потребует дополнения. Не отключайте проверку сертификатов при ошибках подключения.

Для наблюдаемого экспорта Archer с `cipher AES-128-CBC` в Compose указан `OPENVPN_FLAGS: "--data-ciphers-fallback AES-128-CBC"`. Это совместимость с сервером без согласования шифров, которую запрашивает ошибка `failed to negotiate cipher ... Configure --data-ciphers-fallback`. Проверка сертификатов остаётся включённой. При другом шифре в профиле резервный шифр должен соответствовать реальной настройке роутера. [Руководство OpenVPN 2.6](https://build.openvpn.net/man/openvpn-2.6/openvpn.8.html).

После изменения environment контейнер нужно пересоздать командой `docker-compose -f compose.yaml -f compose.router-vpn.yaml up -d --force-recreate router-vpn`; обычный `restart` не применяет изменённые переменные окружения. Если тайм-аут возникает на UDP, проверьте TCP на роутере и экспортируйте профиль заново; протокол берётся из `.ovpn`.

## Запуск и проверка

Перенесите обновлённый проект на Ubuntu, сохранив `.env`, `data/` и `vpn/`. Остановите прежний monitor и запустите только VPN:

```bash
docker-compose stop monitor
docker-compose -f compose.yaml -f compose.router-vpn.yaml up -d router-vpn
docker-compose -f compose.yaml -f compose.router-vpn.yaml logs --tail=80 router-vpn
docker-compose -f compose.yaml -f compose.router-vpn.yaml ps
```

Ожидается установление OpenVPN-соединения и статус `healthy`. Если есть TLS timeout или ошибка шифра, сначала разберите её по логу. Наличие контейнера в статусе `Up` само по себе ещё не подтверждает VPN. Сообщение о внешнем IP должно показывать домашний адрес.

Для проверки именно прокси из контейнера парсера, без обращения к Ozon:

```bash
docker-compose -f compose.yaml -f compose.router-vpn.yaml run --rm --no-deps --entrypoint python monitor -c 'import urllib.request; p=urllib.request.ProxyHandler({"https":"http://router-vpn:8888"}); print(urllib.request.build_opener(p).open("https://api.ipify.org", timeout=20).read().decode())'
```

Он должен показать домашний публичный IP. Затем остановите прежний SSH-туннель дома (Ctrl+C, либо `systemctl --user stop ozon-home-tunnel.service`) и выполните один обход:

```bash
docker-compose -f compose.yaml -f compose.router-vpn.yaml run --rm monitor python main.py --once
```

После успешного обхода:

```bash
docker-compose -f compose.yaml -f compose.router-vpn.yaml up -d
```

Во всех дальнейших командах этой установки используйте `compose.router-vpn.yaml`, а не `compose.home-proxy.yaml`. Автозапуск обоих контейнеров включён через `restart: unless-stopped`; Docker на Ubuntu также должен запускаться при загрузке. После успешного теста SSH-службу дома можно отключить: `systemctl --user disable --now ozon-home-tunnel.service`.

Настоящее подключение к вашему роутеру проверяется на Ubuntu; локальная проверка Compose подтверждает только структуру конфигурации.
