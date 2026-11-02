# Деплой и эксплуатация

Бот — долгоживущий процесс: он держит соединение с VK Long Poll и должен
перезапускаться после сбоев. Ниже — варианты запуска на Linux-сервере.

## Требования

- Linux с systemd или Supervisor
- Python 3.10+
- Доступ в интернет к `api.vk.com`, `api.giga.chat`, `ngw.devices.sberbank.ru`
- Отдельный пользователь для сервиса (например, `zerocoder`)
- Каталог проекта, например `/opt/alina-ai-consultant`

## Подготовка сервера

```bash
sudo useradd --system --create-home --shell /usr/sbin/nologin zerocoder
sudo mkdir -p /opt/alina-ai-consultant
sudo chown zerocoder:zerocoder /opt/alina-ai-consultant
```

Склонируйте проект и установите зависимости:

```bash
sudo -u zerocoder git clone https://github.com/elstar883/alina-ai-consultant.git /opt/alina-ai-consultant
cd /opt/alina-ai-consultant
sudo -u zerocoder python3 -m venv .venv
sudo -u zerocoder .venv/bin/pip install --upgrade pip
sudo -u zerocoder .venv/bin/pip install -r requirements.txt
```

Создайте `.env` и ограничьте права:

```bash
sudo -u zerocoder cp .env.example .env
sudo -u zerocoder chmod 600 .env
sudo -u zerocoder editor .env
```

Проверьте подключения:

```bash
cd /opt/alina-ai-consultant
sudo -u zerocoder env PYTHONPATH=src .venv/bin/python -m alina_bot --check
```

## systemd

```bash
sudo cp deploy/systemd/zerocoder-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now zerocoder-bot
sudo systemctl status zerocoder-bot
sudo journalctl -u zerocoder-bot -f
```

Файл unit-а предполагает каталог `/opt/alina-ai-consultant` и пользователя
`zerocoder`. Если пути другие — отредактируйте `WorkingDirectory`, `ExecStart`,
`User` и `EnvironmentFile`.

## Supervisor

```bash
sudo apt install supervisor        # Debian/Ubuntu
sudo mkdir -p /var/log/alina-bot
sudo chown zerocoder:zerocoder /var/log/alina-bot
sudo cp deploy/supervisor/zerocoder-bot.conf /etc/supervisor/conf.d/
sudo supervisorctl reread
sudo supervisorctl update
sudo supervisorctl status zerocoder-bot
sudo supervisorctl tail -f zerocoder-bot
```

## Обновление

```bash
cd /opt/alina-ai-consultant
sudo -u zerocoder git fetch --all
sudo -u zerocoder git switch main
sudo -u zerocoder git pull --ff-only
sudo -u zerocoder .venv/bin/pip install -r requirements.txt
sudo systemctl restart zerocoder-bot
```

Перед обновлением:

```bash
sudo -u zerocoder env PYTHONPATH=src .venv/bin/python -m alina_bot --check
```

## Резервное копирование

Копировать нужно только `.env` и, при наличии, локальные изменения базы знаний.
Секреты храните в защищённом месте (например, в менеджере паролей), а не в Git.

```bash
sudo -u zerocoder tar czf /var/backups/alina-env-$(date +%F).tar.gz -C /opt/alina-ai-consultant .env
sudo chmod 600 /var/backups/alina-env-*.tar.gz
```

## Диагностика

| Симптом | Что проверить |
|---|---|
| `Ошибка конфигурации` | Заполнены ли `VK_TOKEN`, `VK_GROUP_ID`, `GIGACHAT_CREDENTIALS`; нет ли лишних пробелов |
| `CERTIFICATE_VERIFY_FAILED` | Установлен ли сертификат Минцифры; путь в `GIGACHAT_CA_BUNDLE_FILE` |
| `401` от GigaChat | Актуален ли Authorization Key и выбран ли правильный `GIGACHAT_SCOPE` |
| Бот молчит | Включены ли сообщения сообщества, Long Poll и событие `message_new`; есть ли у ключа право «Сообщения сообщества» |
| Дубли ответов | Не запущено ли несколько экземпляров с одним токеном |
| Ответы не по теме | Актуальна ли база знаний; проверьте поиск локально |

Проверка Long Poll вручную:

```bash
PYTHONPATH=src .venv/bin/python -m alina_bot --check
```

Проверка поиска по базе знаний:

```bash
PYTHONPATH=src .venv/bin/python -m alina_bot.knowledge_base "сколько стоит вайб-кодер"
```

## Ротация секретов

1. Выпустите новый ключ в VK и/или GigaChat Studio.
2. Обновите `.env`.
3. Перезапустите сервис.
4. Отзовите старый ключ.

Не коммитьте `.env`, сертификаты и логи. Они уже исключены в `.gitignore`.
