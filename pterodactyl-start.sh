#!/bin/bash

# Скрипт запуска для Pterodactyl
# Устанавливает зависимости и запускает бота

cd /mnt/server

# Установка зависимостей (если нужно)
if [ ! -d "venv" ]; then
    echo "Создание виртуального окружения..."
    python3 -m venv venv
fi

source venv/bin/activate

# Установка/обновление зависимостей
echo "Установка зависимостей..."
pip install --upgrade pip
pip install -r requirements.txt

# Запуск бота
echo "Запуск бота..."
python src/bot.py

