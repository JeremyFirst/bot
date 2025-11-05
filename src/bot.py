"""
Главный файл Discord-бота для управления администрированием RUST-сервера
"""
import asyncio
import logging
import os
import sys
from pathlib import Path
from typing import Optional

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
log_file_path = LOG_FILE or 'logs/bot.log'
log_level_str = LOG_LEVEL or 'INFO'

log_dir = Path(log_file_path).parent
log_dir.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=getattr(logging, log_level_str.upper(), logging.INFO),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_file_path, encoding='utf-8'),
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
db: Optional[Database] = None
rcon_manager: Optional[RCONManager] = None
scheduler: Optional[PrivilegeScheduler] = None


@bot.event
async def on_ready():
    """Событие при запуске бота"""
    global db, rcon_manager, scheduler
    
    if bot.user:
        logger.info(f'{bot.user} успешно запущен!')
        logger.info(f'Бот подключен к Discord как {bot.user.name}')
    logger.info(f'Бот подключен к {len(bot.guilds)} серверам')
    
    try:
        # Инициализация базы данных
        logger.info("Инициализация базы данных...")
        db = Database(DB_URL)
        await db.connect()
        logger.info("✓ База данных подключена")
        
        # Проверка и автоматическое применение схемы БД
        try:
            if db.pool:
                async with db.pool.acquire() as conn:
                    async with conn.cursor() as cursor:
                        await cursor.execute("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = %s", (db.config['db'],))
                        result = await cursor.fetchone()
                        table_count = result[0] if result else 0
                        if table_count == 0:
                            logger.warning("⚠️ Таблицы в базе данных не найдены!")
                            logger.info("Применение схемы базы данных автоматически...")
                            
                            # Читаем схему из файла
                            schema_file = Path(__file__).parent.parent / "database" / "schema.sql"
                            if schema_file.exists():
                                with open(schema_file, 'r', encoding='utf-8') as f:
                                    schema_sql = f.read()
                                
                                # Разбиваем SQL на отдельные команды
                                commands = [
                                    cmd.strip() 
                                    for cmd in schema_sql.split(';') 
                                    if cmd.strip() and not cmd.strip().startswith('--')
                                ]
                                
                                # Разделяем команды на CREATE TABLE и CREATE INDEX
                                create_table_commands = [cmd for cmd in commands if cmd.upper().startswith('CREATE TABLE')]
                                create_index_commands = [cmd for cmd in commands if cmd.upper().startswith('CREATE INDEX')]
                                
                                # Сначала создаем таблицы
                                for command in create_table_commands:
                                    if command:
                                        try:
                                            await cursor.execute(command)
                                            logger.debug(f"✓ Таблица создана: {command[:50]}...")
                                        except Exception as e:
                                            error_str = str(e).lower()
                                            if "already exists" in error_str or "duplicate" in error_str:
                                                logger.debug(f"ℹ️ Таблица уже существует: {command[:50]}...")
                                            else:
                                                logger.warning(f"⚠️ Ошибка при создании таблицы: {e}")
                                
                                # Затем создаем индексы
                                for command in create_index_commands:
                                    if command:
                                        try:
                                            await cursor.execute(command)
                                            logger.debug(f"✓ Индекс создан: {command[:50]}...")
                                        except Exception as e:
                                            error_str = str(e).lower()
                                            if "already exists" in error_str or "duplicate" in error_str or "doesn't exist" in error_str:
                                                logger.debug(f"ℹ️ Индекс пропущен: {command[:50]}...")
                                            else:
                                                logger.warning(f"⚠️ Ошибка при создании индекса: {e}")
                                                logger.warning(f"   Команда: {command[:100]}")
                                
                                await conn.commit()
                                logger.info("✅ Схема базы данных успешно применена!")
                            else:
                                logger.error(f"❌ Файл схемы не найден: {schema_file}")
                                logger.warning("Примените схему вручную через MySQL:")
                                logger.warning(f"  mysql -u {db.config['user']} -p {db.config['db']} < database/schema.sql")
        except Exception as e:
            logger.warning(f"Не удалось проверить/применить схему БД: {e}")
        
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
                    # Проверяем, что канал - это текстовый канал (TextChannel)
                    if isinstance(channel, discord.TextChannel):
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
    if not DISCORD_TOKEN or DISCORD_TOKEN == "YOUR_DISCORD_BOT_TOKEN_HERE" or DISCORD_TOKEN == "":
        logger.error("=" * 60)
        logger.error("ОШИБКА: DISCORD_TOKEN не найден!")
        logger.error("=" * 60)
        logger.error("Создайте файл config/config.yaml на сервере и заполните его.")
        logger.error("Пример конфигурации можно найти в репозитории.")
        logger.error("")
        logger.error("Минимальная конфигурация:")
        logger.error("DISCORD_TOKEN: \"ваш_токен_дискорд_бота\"")
        logger.error("RCON_HOST: \"ip_вашего_rust_сервера\"")
        logger.error("RCON_PORT: 28016")
        logger.error("RCON_PASS: \"ваш_rcon_пароль\"")
        logger.error("DB_URL: \"mysql://пользователь:пароль@localhost:3306/rustbot\"")
        logger.error("=" * 60)
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

