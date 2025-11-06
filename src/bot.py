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
    DB_URL,
    BOT_ACTIVITY_NAME,
    BOT_ACTIVITY_TYPE,
    CONSOLE_LOGS_ENABLED,
    CONSOLE_LOGS_FILTERS
)
from src.database.models import Database
from src.rcon.rcon_manager import RCONManager
from src.commands import setup_admin, setup_privilege, setup_warn
from src.tasks.scheduler import PrivilegeScheduler
from src.utils.admin_list_manager import AdminListManager

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
admin_list_manager: Optional[AdminListManager] = None


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
                        # Список обязательных таблиц из schema.sql
                        required_tables = [
                            'users',
                            'privileges',
                            'warnings',
                            'role_mappings',
                            'admin_list_messages'
                        ]
                        
                        # Проверяем наличие каждой таблицы
                        missing_tables = []
                        for table_name in required_tables:
                            await cursor.execute(
                                "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = %s AND table_name = %s",
                                (db.config['db'], table_name)
                            )
                            result = await cursor.fetchone()
                            if not result or result[0] == 0:
                                missing_tables.append(table_name)
                        
                        if missing_tables:
                            logger.warning(f"⚠️ Найдены отсутствующие таблицы: {', '.join(missing_tables)}")
                            logger.info("Применение схемы базы данных автоматически...")
                            
                            # Читаем схему из файла
                            schema_file = Path(__file__).parent.parent / "database" / "schema.sql"
                            if schema_file.exists():
                                with open(schema_file, 'r', encoding='utf-8') as f:
                                    schema_sql = f.read()
                                
                                # Убираем комментарии (строки начинающиеся с --)
                                lines = []
                                for line in schema_sql.split('\n'):
                                    # Убираем комментарии в конце строки
                                    comment_pos = line.find('--')
                                    if comment_pos != -1:
                                        line = line[:comment_pos]
                                    lines.append(line)
                                
                                # Объединяем строки обратно
                                cleaned_sql = '\n'.join(lines)
                                
                                # Разбиваем SQL на отдельные команды по точке с запятой
                                commands = []
                                current_command = []
                                for line in cleaned_sql.split('\n'):
                                    line = line.strip()
                                    if not line:
                                        continue
                                    current_command.append(line)
                                    if line.endswith(';'):
                                        # Завершаем команду
                                        command = ' '.join(current_command).rstrip(';').strip()
                                        if command:
                                            commands.append(command)
                                        current_command = []
                                
                                # Если осталась незавершенная команда
                                if current_command:
                                    command = ' '.join(current_command).strip()
                                    if command:
                                        commands.append(command)
                                
                                # Разделяем команды на CREATE TABLE и CREATE INDEX
                                create_table_commands = [cmd for cmd in commands if cmd.upper().startswith('CREATE TABLE')]
                                create_index_commands = [cmd for cmd in commands if cmd.upper().startswith('CREATE INDEX')]
                                
                                logger.debug(f"Найдено {len(commands)} команд SQL: {len(create_table_commands)} таблиц, {len(create_index_commands)} индексов")
                                
                                # Сначала создаем таблицы
                                logger.info(f"Создание {len(create_table_commands)} таблиц...")
                                for command in create_table_commands:
                                    if command:
                                        try:
                                            await cursor.execute(command)
                                            # Извлекаем имя таблицы из команды
                                            table_name = command.split('(')[0].split()[-1] if '(' in command else "unknown"
                                            logger.info(f"✓ Таблица создана: {table_name}")
                                        except Exception as e:
                                            error_str = str(e).lower()
                                            if "already exists" in error_str or "duplicate" in error_str:
                                                table_name = command.split('(')[0].split()[-1] if '(' in command else "unknown"
                                                logger.info(f"ℹ️ Таблица уже существует: {table_name}")
                                            else:
                                                logger.error(f"❌ Ошибка при создании таблицы: {e}")
                                                logger.error(f"   Команда: {command[:100]}")
                                
                                # Затем создаем индексы
                                logger.info(f"Создание {len(create_index_commands)} индексов...")
                                for command in create_index_commands:
                                    if command:
                                        try:
                                            await cursor.execute(command)
                                            # Извлекаем имя индекса из команды
                                            index_name = command.split('ON')[0].split()[-1] if 'ON' in command else "unknown"
                                            logger.debug(f"✓ Индекс создан: {index_name}")
                                        except Exception as e:
                                            error_str = str(e).lower()
                                            if "already exists" in error_str or "duplicate" in error_str:
                                                index_name = command.split('ON')[0].split()[-1] if 'ON' in command else "unknown"
                                                logger.debug(f"ℹ️ Индекс уже существует: {index_name}")
                                            elif "doesn't exist" in error_str:
                                                # Таблица не существует - это нормально, индекс будет создан позже
                                                logger.debug(f"ℹ️ Индекс пропущен (таблица не существует): {command[:50]}...")
                                            else:
                                                logger.warning(f"⚠️ Ошибка при создании индекса: {e}")
                                                logger.warning(f"   Команда: {command[:100]}")
                                
                                await conn.commit()
                                
                                # Проверяем результат - все ли таблицы созданы
                                still_missing = []
                                for table_name in required_tables:
                                    await cursor.execute(
                                        "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = %s AND table_name = %s",
                                        (db.config['db'], table_name)
                                    )
                                    result = await cursor.fetchone()
                                    if not result or result[0] == 0:
                                        still_missing.append(table_name)
                                
                                if still_missing:
                                    logger.error(f"❌ Не удалось создать таблицы: {', '.join(still_missing)}")
                                    logger.warning("Примените схему вручную через MySQL:")
                                    logger.warning(f"  mysql -u {db.config['user']} -p {db.config['db']} < database/schema.sql")
                                else:
                                    logger.info("✅ Схема базы данных успешно применена!")
                                    logger.info("✓ Все таблицы успешно созданы")
                            else:
                                logger.error(f"❌ Файл схемы не найден: {schema_file}")
                                logger.warning("Примените схему вручную через MySQL:")
                                logger.warning(f"  mysql -u {db.config['user']} -p {db.config['db']} < database/schema.sql")
                        else:
                            logger.info("✓ Все таблицы базы данных существуют")
        except Exception as e:
            logger.warning(f"Не удалось проверить/применить схему БД: {e}")
        
        # Инициализация RCON менеджера
        logger.info("Инициализация RCON подключения...")
        rcon_manager = RCONManager()
        success = await rcon_manager.connect()
        
        # Установка статуса активности бота
        activity_type_map = {
            'watching': discord.ActivityType.watching,
            'playing': discord.ActivityType.playing,
            'streaming': discord.ActivityType.streaming,
            'listening': discord.ActivityType.listening,
            'competing': discord.ActivityType.competing
        }
        
        activity_type = activity_type_map.get(BOT_ACTIVITY_TYPE, discord.ActivityType.watching)
        
        if success:
            logger.info(f"✓ RCON подключен на порту {rcon_manager.connected_port}")
            
            # Настройка прослушивания консоли
            if CONSOLE_LOGS_ENABLED:
                async def console_log_handler(message: str):
                    """Обработчик сообщений консоли"""
                    try:
                        # Фильтрация по ключевым словам (если настроены)
                        if CONSOLE_LOGS_FILTERS:
                            message_lower = message.lower()
                            if not any(filter_word in message_lower for filter_word in CONSOLE_LOGS_FILTERS):
                                return  # Пропускаем сообщение, если не содержит ключевых слов
                        
                        # Отправка в Discord канал (если настроен)
                        channel_id = CHANNELS.get('CONSOLE_LOGS')
                        if channel_id:
                            for guild in bot.guilds:
                                channel = guild.get_channel(channel_id)
                                if isinstance(channel, discord.TextChannel):
                                    # Обрезаем длинные сообщения (Discord лимит 2000 символов)
                                    msg = message[:1950] if len(message) > 1950 else message
                                    await channel.send(f"```\n{msg}\n```")
                                    break
                        
                        # Логирование в файл
                        logger.debug(f"Консоль: {message[:100]}")
                    except Exception as e:
                        logger.error(f"Ошибка обработки лога консоли: {e}")
                
                rcon_manager.set_console_callback(console_log_handler)
                logger.info("✓ Прослушивание консоли включено")
            
            await bot.change_presence(
                activity=discord.Activity(
                    type=activity_type,
                    name=BOT_ACTIVITY_NAME
                ),
                status=discord.Status.online
            )
        else:
            logger.error("✗ Не удалось подключиться к RCON серверу")
            await bot.change_presence(
                status=discord.Status.idle,
                activity=discord.Activity(
                    type=activity_type,
                    name=BOT_ACTIVITY_NAME
                )
            )
        
        # Загрузка команд
        logger.info("Загрузка команд...")
        await setup_admin(bot, db)
        await setup_privilege(bot, rcon_manager, db)
        await setup_warn(bot, db, rcon_manager)
        logger.info("✓ Команды загружены")
        
        # Запуск планировщика
        logger.info("Запуск планировщика задач...")
        scheduler = PrivilegeScheduler(bot, db, rcon_manager)
        await scheduler.start()
        logger.info("✓ Планировщик запущен")
        
        # Инициализация менеджера состава администрации
        global admin_list_manager
        if db:
            admin_list_manager = AdminListManager(bot, db)
            logger.info("✓ Менеджер состава администрации инициализирован")
        
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
async def on_member_update(before: discord.Member, after: discord.Member):
    """Обработка изменения участника (в т.ч. ролей)"""
    global admin_list_manager
    
    # Проверяем, изменились ли роли
    if before.roles != after.roles:
        # Обновляем сообщение состава администрации
        if admin_list_manager and after.guild:
            try:
                await admin_list_manager.update(after.guild)
                logger.debug(f"Состав администрации обновлен после изменения ролей у {after.id}")
            except Exception as e:
                logger.error(f"Ошибка обновления состава администрации: {e}", exc_info=True)


@bot.event
async def on_member_join(member: discord.Member):
    """Обработка присоединения участника"""
    global admin_list_manager
    
    # Обновляем сообщение состава администрации
    if admin_list_manager and member.guild:
        try:
            await admin_list_manager.update(member.guild)
            logger.debug(f"Состав администрации обновлен после присоединения {member.id}")
        except Exception as e:
            logger.error(f"Ошибка обновления состава администрации: {e}", exc_info=True)


@bot.event
async def on_member_remove(member: discord.Member):
    """Обработка выхода участника"""
    global admin_list_manager
    
    # Обновляем сообщение состава администрации
    if admin_list_manager and member.guild:
        try:
            await admin_list_manager.update(member.guild)
            logger.debug(f"Состав администрации обновлен после выхода {member.id}")
        except Exception as e:
            logger.error(f"Ошибка обновления состава администрации: {e}", exc_info=True)


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
        # Пытаемся выполнить cleanup, но если event loop уже закрыт - игнорируем ошибку
        try:
            loop = asyncio.get_event_loop()
            if loop.is_closed():
                # Event loop уже закрыт, создаем новый
                asyncio.run(cleanup())
            else:
                # Event loop еще открыт, используем его
                loop.run_until_complete(cleanup())
        except RuntimeError:
            # Нет активного event loop, создаем новый
            try:
                asyncio.run(cleanup())
            except RuntimeError as e:
                if "Event loop is closed" in str(e):
                    logger.warning("Event loop уже закрыт, пропускаем cleanup")
                else:
                    raise


if __name__ == "__main__":
    main()

