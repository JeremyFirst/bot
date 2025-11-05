"""
Конфигурация бота
Поддерживает загрузку из YAML файла (config.yaml) или переменных окружения (.env)
Приоритет: YAML файл > переменные окружения > значения по умолчанию
"""
import os
from pathlib import Path
from typing import List, Dict, Optional
import yaml

from dotenv import load_dotenv

# Загружаем переменные окружения на случай, если YAML не используется
load_dotenv()

# Путь к файлу конфигурации
CONFIG_FILE = Path(__file__).parent / "config.yaml"
CONFIG_DATA = {}

# Загружаем YAML конфигурацию если файл существует
if CONFIG_FILE.exists():
    try:
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            CONFIG_DATA = yaml.safe_load(f) or {}
        if CONFIG_DATA:
            print(f"✓ Загружена конфигурация из {CONFIG_FILE}")
    except Exception as e:
        print(f"⚠️ Предупреждение: Не удалось загрузить config.yaml: {e}")
        CONFIG_DATA = {}
else:
    print(f"ℹ️ Файл {CONFIG_FILE} не найден, используются переменные окружения или значения по умолчанию")


def get_config(key: str, default=None, env_key: Optional[str] = None):
    """
    Получить значение конфигурации
    Приоритет: YAML > переменные окружения > значение по умолчанию
    """
    # Пробуем YAML
    if key in CONFIG_DATA:
        value = CONFIG_DATA[key]
        if value is not None:
            return value
    
    # Пробуем переменные окружения
    env_key_to_use = env_key if env_key is not None else key
    env_value = os.getenv(env_key_to_use)
    if env_value is not None:
        return env_value
    
    # Возвращаем значение по умолчанию
    return default


# Discord
DISCORD_TOKEN = get_config('DISCORD_TOKEN', os.getenv('DISCORD_TOKEN', '')) or ''

# RCON (WebRCON)
RCON_HOST = get_config('RCON_HOST', os.getenv('RCON_PORT', 'localhost')) or 'localhost'
rcon_port_str = get_config('RCON_PORT', os.getenv('RCON_PORT', '28016'))
RCON_PORT = int(rcon_port_str) if rcon_port_str is not None else 28016
RCON_PASS = get_config('RCON_PASS', os.getenv('RCON_PASS', '')) or ''

# База данных (MariaDB/MySQL)
DB_URL = get_config('DB_URL', os.getenv('DB_URL', 'mysql://user:password@localhost:3306/rustbot')) or 'mysql://user:password@localhost:3306/rustbot'
# Удаляем префикс jdbc: если есть (для совместимости)
if DB_URL.startswith('jdbc:'):
    DB_URL = DB_URL.replace('jdbc:', '', 1)

# Категории администрации
admin_categories_env = os.getenv('ADMIN_ROLE_CATEGORIES', '')
admin_categories_yaml = CONFIG_DATA.get('ADMIN_ROLE_CATEGORIES', [])
if admin_categories_yaml:
    ADMIN_ROLE_CATEGORIES = admin_categories_yaml if isinstance(admin_categories_yaml, list) else [admin_categories_yaml]
elif admin_categories_env:
    ADMIN_ROLE_CATEGORIES = admin_categories_env.split(',')
else:
    ADMIN_ROLE_CATEGORIES = ['Старшая Администрация', 'Младшая Администрация']

# Каналы Discord
channels_yaml = CONFIG_DATA.get('CHANNELS', {})
channels_env = {}
if channels_yaml:
    channels_env = channels_yaml
else:
    channels_env = {
        'ADMIN_LOGS': int(os.getenv('CHANNEL_ADMIN_LOGS', '0')),
        'WARNINGS_CHANNEL': int(os.getenv('CHANNEL_WARNINGS', '0')),
    }

CHANNELS = {
    'ADMIN_LOGS': channels_env.get('ADMIN_LOGS', 0) if isinstance(channels_env.get('ADMIN_LOGS'), int) else int(channels_env.get('ADMIN_LOGS', 0)),
    'WARNINGS_CHANNEL': channels_env.get('WARNINGS_CHANNEL', 0) if isinstance(channels_env.get('WARNINGS_CHANNEL'), int) else int(channels_env.get('WARNINGS_CHANNEL', 0)),
    'CONSOLE_LOGS': channels_env.get('CONSOLE_LOGS', 0) if isinstance(channels_env.get('CONSOLE_LOGS'), int) else int(channels_env.get('CONSOLE_LOGS', 0)),
}

# Настройки логирования консоли
console_logs_enabled_raw = get_config('CONSOLE_LOGS_ENABLED', os.getenv('CONSOLE_LOGS_ENABLED', 'false')) or 'false'
CONSOLE_LOGS_ENABLED = str(console_logs_enabled_raw).lower() == 'true'
console_logs_filters_raw = get_config('CONSOLE_LOGS_FILTERS', os.getenv('CONSOLE_LOGS_FILTERS', '')) or ''
if console_logs_filters_raw:
    CONSOLE_LOGS_FILTERS = [f.strip().lower() for f in str(console_logs_filters_raw).split(',') if f.strip()]
else:
    CONSOLE_LOGS_FILTERS = []  # Пустой список = логировать все

# Маппинг групп к ролям Discord (group_name -> discord_role_id)
ROLE_MAPPINGS: Dict[str, int] = {}

# Загружаем из YAML
role_mappings_yaml = CONFIG_DATA.get('ROLE_MAPPINGS', {})
if role_mappings_yaml:
    ROLE_MAPPINGS = {str(k): int(v) for k, v in role_mappings_yaml.items()}
else:
    # Загружаем из переменных окружения (ROLE_MAPPINGS_adminl1=123456789)
    for key, value in os.environ.items():
        if key.startswith('ROLE_MAPPINGS_'):
            group_name = key.replace('ROLE_MAPPINGS_', '')
            try:
                ROLE_MAPPINGS[group_name] = int(value)
            except ValueError:
                pass

# Иерархия групп (от высшей к низшей)
group_hierarchy_yaml = CONFIG_DATA.get('GROUP_HIERARCHY', [])
if group_hierarchy_yaml:
    GROUP_HIERARCHY = group_hierarchy_yaml if isinstance(group_hierarchy_yaml, list) else [group_hierarchy_yaml]
else:
    GROUP_HIERARCHY = os.getenv('GROUP_HIERARCHY', 'owner,admin,adminl1,moderator,moder,helper').split(',')

# Все возможные варианты названий групп в ответах RCON
group_name_db_yaml = CONFIG_DATA.get('GROUP_NAME_DATABASE', [])
if group_name_db_yaml:
    GROUP_NAME_DATABASE = group_name_db_yaml if isinstance(group_name_db_yaml, list) else [group_name_db_yaml]
else:
    GROUP_NAME_DATABASE = os.getenv('GROUP_NAME_DATABASE', 'moderator,moder,adminl1,admin,owner,helper').split(',')

# Лимиты выговоров
punishment_limits_yaml = CONFIG_DATA.get('PUNISHMENT_LIMITS', {})
if punishment_limits_yaml:
    PUNISHMENT_LIMITS = {
        'recruitment': int(punishment_limits_yaml.get('recruitment', 3)),
        'donat': int(punishment_limits_yaml.get('donat', 2)),
    }
else:
    PUNISHMENT_LIMITS = {
        'recruitment': int(os.getenv('PUNISHMENT_LIMIT_RECRUITMENT', '3')),
        'donat': int(os.getenv('PUNISHMENT_LIMIT_DONAT', '2')),
    }

# Ссылка для предложения покупки привилегии
PURCHASE_LINK = get_config('PURCHASE_LINK', os.getenv('PURCHASE_LINK', 'https://example.com/buy'))

# Настройки логирования
LOG_FILE = get_config('LOG_FILE', os.getenv('LOG_FILE', 'logs/bot.log'))
LOG_LEVEL = get_config('LOG_LEVEL', os.getenv('LOG_LEVEL', 'INFO'))

# Настройки планировщика
scheduler_interval = get_config('SCHEDULER_CHECK_INTERVAL', os.getenv('SCHEDULER_CHECK_INTERVAL', '60'))
SCHEDULER_CHECK_INTERVAL = int(scheduler_interval) if scheduler_interval is not None else 60

privilege_retry_delay = get_config('PRIVILEGE_REMOVAL_RETRY_DELAY', os.getenv('PRIVILEGE_REMOVAL_RETRY_DELAY', '120'))
PRIVILEGE_REMOVAL_RETRY_DELAY = int(privilege_retry_delay) if privilege_retry_delay is not None else 120

max_retries = get_config('MAX_REMOVAL_RETRIES', os.getenv('MAX_REMOVAL_RETRIES', '3'))
MAX_REMOVAL_RETRIES = int(max_retries) if max_retries is not None else 3

# Статус активности бота
BOT_ACTIVITY_NAME = get_config('BOT_ACTIVITY_NAME', os.getenv('BOT_ACTIVITY_NAME', 'RUST сервер')) or 'RUST сервер'
bot_activity_type_raw = get_config('BOT_ACTIVITY_TYPE', os.getenv('BOT_ACTIVITY_TYPE', 'watching')) or 'watching'
BOT_ACTIVITY_TYPE = bot_activity_type_raw.lower() if isinstance(bot_activity_type_raw, str) else 'watching'
