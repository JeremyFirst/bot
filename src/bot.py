"""
Главный файл Discord-бота для управления администрированием RUST-сервера
"""
import asyncio
import logging
import os
import sys
from pathlib import Path

# Добавляем корневую директорию проекта в PYTHONPATH
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import discord
from discord.ext import commands

from config.config import (
    DISCORD_TOKEN,
    LOG_FILE,
    LOG_LEVEL,
    CHANNELS,
    DB_URL
)
from src.database.models import Database
from src.rcon.rcon_manager import RCONManager
from src.commands import setup_admin, setup_privilege, setup_warn
from src.tasks.scheduler import PrivilegeScheduler

# Настройка логирования
log_dir = Path(LOG_FILE).parent
log_dir.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL.upper()),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE, encoding='utf-8'),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger(__name__)
# Уменьшаем уровень логирования для discord библиотеки
logging.getLogger('discord').setLevel(logging.INFO)
logging.getLogger('websockets').setLevel(logging.INFO)

# Создание бота с необходимыми intents
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.guilds = True

bot = commands.Bot(command_prefix='!', intents=intents)

# Глобальные объекты
db: Database = None
rcon_manager: RCONManager = None
scheduler: PrivilegeScheduler = None


@bot.event
async def on_ready():
    """Событие при запуске бота"""
    global db, rcon_manager, scheduler
    
    logger.info(f'{bot.user} успешно запущен!')
    logger.info(f'Бот подключен к Discord как {bot.user.name}')
    logger.info(f'Бот подключен к {len(bot.guilds)} серверам')
    
    try:
        # Инициализация базы данных
        logger.info("Инициализация базы данных...")
        db = Database(DB_URL)
        await db.connect()
        logger.info("✓ База данных подключена")
        
        # Инициализация RCON менеджера
        logger.info("Инициализация RCON подключения...")
        rcon_manager = RCONManager()
        success = await rcon_manager.connect()
        
        if success:
            logger.info(f"✓ RCON подключен на порту {rcon_manager.connected_port}")
            await bot.change_presence(
                activity=discord.Activity(
                    type=discord.ActivityType.watching,
                    name=f"WebRCON (порт {rcon_manager.connected_port})"
                ),
                status=discord.Status.online
            )
        else:
            logger.error("✗ Не удалось подключиться к RCON серверу")
            await bot.change_presence(
                status=discord.Status.idle,
                activity=discord.Activity(
                    type=discord.ActivityType.watching,
                    name="WebRCON (не подключен)"
                )
            )
        
        # Загрузка команд
        logger.info("Загрузка команд...")
        await setup_admin(bot)
        await setup_privilege(bot, rcon_manager, db)
        await setup_warn(bot, db, rcon_manager)
        logger.info("✓ Команды загружены")
        
        # Запуск планировщика
        logger.info("Запуск планировщика задач...")
        scheduler = PrivilegeScheduler(bot, db, rcon_manager)
        await scheduler.start()
        logger.info("✓ Планировщик запущен")
        
        # Логирование в канал (если настроен)
        if CHANNELS.get('ADMIN_LOGS'):
            try:
                channel_id = CHANNELS['ADMIN_LOGS']
                for guild in bot.guilds:
                    channel = guild.get_channel(channel_id)
                    if channel:
                        await channel.send("✅ Бот запущен и готов к работе")
                        break
            except Exception as e:
                logger.warning(f"Не удалось отправить сообщение в канал логов: {e}")
        
        logger.info("Бот полностью готов к работе!")
        
    except Exception as e:
        logger.error(f"Критическая ошибка при инициализации: {e}", exc_info=True)
        raise


@bot.event
async def on_command_error(ctx, error):
    """Обработка ошибок команд"""
    if isinstance(error, commands.CommandNotFound):
        return
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"❌ Отсутствует обязательный аргумент: {error.param.name}")
    elif isinstance(error, commands.BadArgument):
        await ctx.send(f"❌ Неверный аргумент: {error}")
    else:
        logger.error(f"Ошибка в команде {ctx.command}: {error}", exc_info=True)
        await ctx.send(f"❌ Произошла ошибка при выполнении команды: {error}")


async def cleanup():
    """Очистка ресурсов при завершении"""
    global db, rcon_manager, scheduler
    
    logger.info("Завершение работы бота...")
    
    if scheduler:
        await scheduler.stop()
    
    if rcon_manager:
        await rcon_manager.close()
    
    if db:
        await db.close()
    
    logger.info("Бот завершил работу")


def main():
    """Главная функция запуска бота"""
    if not DISCORD_TOKEN or DISCORD_TOKEN == "YOUR_DISCORD_BOT_TOKEN_HERE":
        logger.error("Пожалуйста, установите DISCORD_TOKEN в файле .env")
        logger.error("Создайте файл .env на основе .env.example и заполните ваш токен Discord бота")
        return
    
    try:
        bot.run(DISCORD_TOKEN)
    except KeyboardInterrupt:
        logger.info("Получен сигнал прерывания")
    except Exception as e:
        logger.error(f"Критическая ошибка: {e}", exc_info=True)
    finally:
        asyncio.run(cleanup())


if __name__ == "__main__":
    main()

