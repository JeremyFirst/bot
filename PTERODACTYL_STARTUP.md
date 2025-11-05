# Startup Command для Pterodactyl

## ✅ Правильная команда (с PYTHONPATH):

```bash
cd /home/container; if [[ ! -f src/bot.py ]]; then echo "Клонирование репозитория..."; rm -rf * .[^.]* 2>/dev/null || true; git clone https://github.com/JeremyFirst/bot.git .; fi; if [[ -d .git ]] && [[ "${AUTO_UPDATE}" == "1" ]]; then git pull; fi; pip3 install -r requirements.txt; cd /home/container && PYTHONPATH=/home/container python3 src/bot.py
```

## Альтернатива (запуск из корневой директории):

```bash
cd /home/container; if [[ ! -f src/bot.py ]]; then echo "Клонирование репозитория..."; rm -rf * .[^.]* 2>/dev/null || true; git clone https://github.com/JeremyFirst/bot.git .; fi; if [[ -d .git ]] && [[ "${AUTO_UPDATE}" == "1" ]]; then git pull; fi; pip3 install -r requirements.txt; cd /home/container && python3 -m src.bot
```

## Простая команда (если репозиторий уже настроен):

```bash
cd /home/container && PYTHONPATH=/home/container python3 src/bot.py
```

## Где изменить:

1. В Pterodactyl откройте настройки вашего сервера
2. Найдите раздел **Startup**
3. Вставьте одну из команд выше в поле **Startup Command**
4. Сохраните и перезапустите сервер

## Важно:

- Файл теперь находится в `src/bot.py`, а не `bot.py`
- Убедитесь, что `config/config.yaml` создан на сервере
- Проверьте, что база данных создана и схема применена

