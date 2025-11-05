"""
Модели базы данных
Поддержка MariaDB/MySQL
"""
from datetime import datetime
from typing import Optional, List
import aiomysql
import logging
from urllib.parse import urlparse, unquote

logger = logging.getLogger(__name__)


class Database:
    """Класс для работы с базой данных (MariaDB/MySQL)"""
    
    def __init__(self, db_url: str):
        self.db_url = db_url
        self.pool: Optional[aiomysql.Pool] = None
        
        # Парсим URL для подключения
        parsed = urlparse(db_url)
        
        # Декодируем пароль (на случай URL-кодирования)
        password = unquote(parsed.password or '')
        
        self.config = {
            'host': parsed.hostname or 'localhost',
            'port': parsed.port or 3306,
            'user': parsed.username or 'root',
            'password': password,
            'db': parsed.path.lstrip('/') if parsed.path else 'rustbot',
            'charset': 'utf8mb4',
            'autocommit': False
        }
        
        logger.debug(f"Парсинг DB_URL: host={self.config['host']}, user={self.config['user']}, db={self.config['db']}, port={self.config['port']}")
    
    async def connect(self):
        """Подключение к базе данных"""
        try:
            logger.info(f"Попытка подключения к БД: {self.config['host']}:{self.config['port']}, пользователь: {self.config['user']}, БД: {self.config['db']}")
            self.pool = await aiomysql.create_pool(**self.config, minsize=1, maxsize=10)
            logger.info("✓ Подключение к базе данных установлено")
        except aiomysql.OperationalError as e:
            error_code, error_msg = e.args
            logger.error(f"Ошибка подключения к БД (код {error_code}): {error_msg}")
            if error_code == 1045:
                logger.error("=" * 60)
                logger.error("ОШИБКА АВТОРИЗАЦИИ:")
                logger.error("Проверьте:")
                logger.error("1. Правильность пароля в DB_URL")
                logger.error("2. Существует ли пользователь в MySQL/MariaDB")
                logger.error("3. Имеет ли пользователь права доступа с IP контейнера")
                logger.error("")
                logger.error("Для Pterodactyl Docker контейнеров:")
                logger.error("Создайте пользователя с доступом с любого хоста:")
                logger.error("  CREATE USER 'пользователь'@'%' IDENTIFIED BY 'пароль';")
                logger.error("  GRANT ALL PRIVILEGES ON база_данных.* TO 'пользователь'@'%';")
                logger.error("  FLUSH PRIVILEGES;")
                logger.error("=" * 60)
            raise
        except Exception as e:
            logger.error(f"Ошибка подключения к БД: {e}")
            raise
    
    async def close(self):
        """Закрытие соединения с базой данных"""
        if self.pool:
            try:
                self.pool.close()
                await self.pool.wait_closed()
                logger.info("Соединение с БД закрыто")
            except RuntimeError as e:
                if "Event loop is closed" in str(e):
                    # Event loop уже закрыт, просто закрываем пул синхронно
                    logger.debug("Event loop закрыт, пропускаем wait_closed")
                else:
                    raise
    
    # Users
    async def get_user_by_discord(self, discord_id: int) -> Optional[dict]:
        """Получить пользователя по Discord ID"""
        if not self.pool:
            raise RuntimeError("База данных не подключена. Вызовите connect() сначала.")
        async with self.pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cursor:
                await cursor.execute(
                    "SELECT * FROM users WHERE discord_id = %s",
                    (discord_id,)
                )
                row = await cursor.fetchone()
                return row
    
    async def get_user_by_steamid(self, steamid: str) -> Optional[dict]:
        """Получить пользователя по SteamID"""
        if not self.pool:
            raise RuntimeError("База данных не подключена. Вызовите connect() сначала.")
        async with self.pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cursor:
                await cursor.execute(
                    "SELECT * FROM users WHERE steamid = %s",
                    (steamid,)
                )
                row = await cursor.fetchone()
                return row
    
    async def create_user(self, discord_id: int, steamid: str):
        """Создать пользователя"""
        if not self.pool:
            raise RuntimeError("База данных не подключена. Вызовите connect() сначала.")
        async with self.pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(
                    "INSERT INTO users (discord_id, steamid) VALUES (%s, %s) "
                    "ON DUPLICATE KEY UPDATE steamid = %s",
                    (discord_id, steamid, steamid)
                )
                await conn.commit()
    
    # Privileges
    async def create_privilege(
        self,
        discord_id: int,
        steamid: str,
        group_name: str,
        expires_at: Optional[datetime],
        expires_at_utc: Optional[datetime],
        permanent: bool = False
    ):
        """Создать привилегию"""
        if not self.pool:
            raise RuntimeError("База данных не подключена. Вызовите connect() сначала.")
        async with self.pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(
                    "INSERT INTO privileges (discord_id, steamid, group_name, expires_at, expires_at_utc, permanent) "
                    "VALUES (%s, %s, %s, %s, %s, %s)",
                    (discord_id, steamid, group_name, expires_at, expires_at_utc, permanent)
                )
                await conn.commit()
    
    async def get_privilege_by_discord(self, discord_id: int) -> Optional[dict]:
        """Получить активную привилегию по Discord ID"""
        if not self.pool:
            raise RuntimeError("База данных не подключена. Вызовите connect() сначала.")
        async with self.pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cursor:
                await cursor.execute(
                    "SELECT * FROM privileges WHERE discord_id = %s "
                    "AND (expires_at IS NULL OR expires_at > NOW() OR permanent = TRUE) "
                    "ORDER BY created_at DESC LIMIT 1",
                    (discord_id,)
                )
                row = await cursor.fetchone()
                return row
    
    async def get_privilege_by_steamid(self, steamid: str) -> Optional[dict]:
        """Получить активную привилегию по SteamID"""
        if not self.pool:
            raise RuntimeError("База данных не подключена. Вызовите connect() сначала.")
        async with self.pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cursor:
                await cursor.execute(
                    "SELECT * FROM privileges WHERE steamid = %s "
                    "AND (expires_at IS NULL OR expires_at > NOW() OR permanent = TRUE) "
                    "ORDER BY created_at DESC LIMIT 1",
                    (steamid,)
                )
                row = await cursor.fetchone()
                return row
    
    async def get_expired_privileges(self) -> List[dict]:
        """Получить список истекших привилегий"""
        if not self.pool:
            raise RuntimeError("База данных не подключена. Вызовите connect() сначала.")
        async with self.pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cursor:
                await cursor.execute(
                    "SELECT * FROM privileges WHERE expires_at IS NOT NULL "
                    "AND expires_at <= NOW() AND permanent = FALSE"
                )
                rows = await cursor.fetchall()
                return list(rows) if rows else []
    
    async def delete_privilege(self, privilege_id: int):
        """Удалить привилегию"""
        if not self.pool:
            raise RuntimeError("База данных не подключена. Вызовите connect() сначала.")
        async with self.pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute("DELETE FROM privileges WHERE id = %s", (privilege_id,))
                await conn.commit()
    
    async def delete_privileges_by_discord(self, discord_id: int):
        """Удалить все привилегии пользователя"""
        if not self.pool:
            raise RuntimeError("База данных не подключена. Вызовите connect() сначала.")
        async with self.pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute("DELETE FROM privileges WHERE discord_id = %s", (discord_id,))
                await conn.commit()
    
    # Warnings
    async def create_warning(self, discord_id: int, executor_id: int, reason: str, category: int = 0):
        """Создать выговор"""
        if not self.pool:
            raise RuntimeError("База данных не подключена. Вызовите connect() сначала.")
        async with self.pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(
                    "INSERT INTO warnings (discord_id, executor_id, reason, category) "
                    "VALUES (%s, %s, %s, %s)",
                    (discord_id, executor_id, reason, category)
                )
                await conn.commit()
    
    async def get_warnings_count(self, discord_id: int, category: Optional[int] = None) -> int:
        """Получить количество выговоров пользователя"""
        if not self.pool:
            raise RuntimeError("База данных не подключена. Вызовите connect() сначала.")
        async with self.pool.acquire() as conn:
            async with conn.cursor() as cursor:
                if category is not None:
                    await cursor.execute(
                        "SELECT COUNT(*) FROM warnings WHERE discord_id = %s AND category = %s",
                        (discord_id, category)
                    )
                else:
                    await cursor.execute(
                        "SELECT COUNT(*) FROM warnings WHERE discord_id = %s",
                        (discord_id,)
                    )
                result = await cursor.fetchone()
                return result[0] if result else 0
    
    async def delete_warnings_by_discord(self, discord_id: int):
        """Удалить все выговоры пользователя"""
        if not self.pool:
            raise RuntimeError("База данных не подключена. Вызовите connect() сначала.")
        async with self.pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute("DELETE FROM warnings WHERE discord_id = %s", (discord_id,))
                await conn.commit()
    
    # Role Mappings
    async def get_role_mapping(self, group_name: str) -> Optional[int]:
        """Получить Discord роль ID для группы"""
        if not self.pool:
            raise RuntimeError("База данных не подключена. Вызовите connect() сначала.")
        async with self.pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(
                    "SELECT discord_role_id FROM role_mappings WHERE group_name = %s",
                    (group_name,)
                )
                result = await cursor.fetchone()
                return result[0] if result else None
    
    async def set_role_mapping(self, group_name: str, discord_role_id: int):
        """Установить маппинг группы к роли"""
        if not self.pool:
            raise RuntimeError("База данных не подключена. Вызовите connect() сначала.")
        async with self.pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(
                    "INSERT INTO role_mappings (group_name, discord_role_id) "
                    "VALUES (%s, %s) ON DUPLICATE KEY UPDATE discord_role_id = %s",
                    (group_name, discord_role_id, discord_role_id)
                )
                await conn.commit()
