"""
Менеджер для управления сообщением состава администрации
"""
import discord
import logging
from typing import Optional

from config.config import ADMIN_ROLE_CATEGORIES, CHANNELS
from src.utils.embeds import create_admin_list_embed
from src.database.models import Database

logger = logging.getLogger(__name__)


async def get_admin_members_by_category(guild: discord.Guild) -> dict:
    """
    Получить администраторов по категориям с группировкой по ролям
    
    Args:
        guild: Discord сервер
        
    Returns:
        Словарь {category_name: {role_name: [discord.Member]}}
    """
    admin_categories = {}
    
    for category_name in ADMIN_ROLE_CATEGORIES:
        admin_categories[category_name] = {}
        
        # Получаем все роли сервера, которые могут относиться к категории
        # Ищем роли, которые содержат название категории или связаны с ней
        category_roles = []
        
        # Сначала ищем точное совпадение
        exact_role = discord.utils.get(guild.roles, name=category_name)
        if exact_role:
            category_roles.append(exact_role)
        
        # Также ищем роли, которые могут быть подкатегориями
        # (например, "Администратор I уровня" в категории "Администрация")
        # Для этого проверяем, содержит ли название роли ключевые слова категории
        category_keywords = {
            "Красная Администрация": ["создатель", "красная"],
            "Кураторы Серверов": ["куратор"],
            "Старшая Администрация": ["главный", "старшая", "зам", "заместитель"],
            "Администрация": ["администратор", "admin", "админ"],
            "Модерация": ["модератор", "moder", "модер"],
            "Донатные Привилегии": ["owner", "god", "sponsor", "донат", "донатный"]
        }
        
        keywords = category_keywords.get(category_name, [category_name.lower()])
        
        for role in guild.roles:
            if role.name == "@everyone":
                continue
            
            role_name_lower = role.name.lower()
            
            # Проверяем, относится ли роль к категории
            is_in_category = False
            
            # Точное совпадение
            if role.name == category_name:
                is_in_category = True
            # Проверка по ключевым словам
            elif any(keyword.lower() in role_name_lower for keyword in keywords):
                is_in_category = True
            
            if is_in_category and role not in category_roles:
                category_roles.append(role)
        
        # Если не нашли роли по ключевым словам, ищем все роли, которые могут относиться к категории
        # (например, если категория называется "Администрация", ищем все роли с "администратор" в названии)
        if not category_roles:
            category_name_lower = category_name.lower()
            for role in guild.roles:
                if role.name == "@everyone":
                    continue
                role_name_lower = role.name.lower()
                # Проверяем, содержит ли название роли часть названия категории или наоборот
                if (category_name_lower in role_name_lower or 
                    role_name_lower in category_name_lower or
                    any(word in role_name_lower for word in category_name_lower.split() if len(word) > 3)):
                    if role not in category_roles:
                        category_roles.append(role)
        
        # Группируем участников по ролям
        # Убираем дубликаты ролей (если роль уже была добавлена в другую категорию)
        seen_roles = set()
        for role in category_roles:
            # Проверяем, не была ли эта роль уже добавлена в другую категорию
            role_already_added = False
            for other_category_name, other_roles_dict in admin_categories.items():
                if other_category_name != category_name and role.name in other_roles_dict:
                    role_already_added = True
                    break
            
            if not role_already_added and role.name not in seen_roles:
                role_members = [member for member in guild.members if role in member.roles]
                # Сохраняем роль даже если нет участников (чтобы показать "(нет пользователей)")
                admin_categories[category_name][role.name] = role_members
                seen_roles.add(role.name)
    
    return admin_categories


class AdminListManager:
    """Менеджер для управления сообщением состава администрации"""
    
    def __init__(self, bot: discord.Client, db: Database):
        self.bot = bot
        self.db = db
    
    async def publish(self, guild: discord.Guild, channel: Optional[discord.TextChannel] = None) -> Optional[discord.Message]:
        """
        Опубликовать сообщение состава администрации
        
        Args:
            guild: Discord сервер
            channel: Канал для публикации (если не указан, берется из конфига)
            
        Returns:
            Объект сообщения или None при ошибке
        """
        try:
            # Определяем канал
            if not channel:
                channel_id = CHANNELS.get('ADMIN_LIST_CHANNEL', 0)
                if not channel_id:
                    logger.error("Не указан канал для состава администрации (ADMIN_LIST_CHANNEL)")
                    return None
                channel = guild.get_channel(channel_id)
                if not channel or not isinstance(channel, discord.TextChannel):
                    logger.error(f"Канал {channel_id} не найден или не является текстовым")
                    return None
            
            # Получаем данные администрации
            admin_categories = await get_admin_members_by_category(guild)
            
            # Создаем embed
            embed = create_admin_list_embed(admin_categories, guild)
            
            # Отправляем сообщение
            message = await channel.send(embed=embed)
            
            # Сохраняем в БД
            await self.db.set_admin_list_message(guild.id, channel.id, message.id)
            logger.info(f"Сообщение состава администрации опубликовано: {message.id} в канале {channel.id}")
            
            return message
            
        except Exception as e:
            logger.error(f"Ошибка публикации состава администрации: {e}", exc_info=True)
            return None
    
    async def update(self, guild: discord.Guild) -> bool:
        """
        Обновить существующее сообщение состава администрации
        
        Args:
            guild: Discord сервер
            
        Returns:
            True если обновление успешно, False при ошибке
        """
        try:
            # Получаем информацию о сообщении из БД
            message_info = await self.db.get_admin_list_message(guild.id)
            if not message_info:
                logger.debug(f"Сообщение состава администрации не найдено для сервера {guild.id}")
                return False
            
            channel_id = message_info['channel_id']
            message_id = message_info['message_id']
            
            # Получаем канал и сообщение
            channel = guild.get_channel(channel_id)
            if not channel or not isinstance(channel, discord.TextChannel):
                logger.warning(f"Канал {channel_id} не найден, удаляем запись из БД")
                await self.db.delete_admin_list_message(guild.id)
                return False
            
            try:
                message = await channel.fetch_message(message_id)
            except discord.NotFound:
                logger.warning(f"Сообщение {message_id} не найдено, удаляем запись из БД")
                await self.db.delete_admin_list_message(guild.id)
                return False
            except discord.Forbidden:
                logger.error(f"Нет доступа к сообщению {message_id}")
                return False
            
            # Получаем данные администрации
            admin_categories = await get_admin_members_by_category(guild)
            
            # Создаем новый embed
            embed = create_admin_list_embed(admin_categories, guild)
            
            # Обновляем сообщение
            await message.edit(embed=embed)
            logger.debug(f"Сообщение состава администрации обновлено: {message_id}")
            
            return True
            
        except Exception as e:
            logger.error(f"Ошибка обновления состава администрации: {e}", exc_info=True)
            return False
    
    async def restore(self, guild: discord.Guild) -> Optional[discord.Message]:
        """
        Восстановить сообщение состава администрации (если оно было удалено)
        
        Args:
            guild: Discord сервер
            
        Returns:
            Объект сообщения или None при ошибке
        """
        try:
            # Проверяем, есть ли запись в БД
            message_info = await self.db.get_admin_list_message(guild.id)
            if not message_info:
                logger.debug(f"Запись о сообщении не найдена для сервера {guild.id}")
                return None
            
            channel_id = message_info['channel_id']
            message_id = message_info['message_id']
            
            # Пытаемся получить сообщение
            channel = guild.get_channel(channel_id)
            if not channel or not isinstance(channel, discord.TextChannel):
                logger.warning(f"Канал {channel_id} не найден")
                return None
            
            try:
                message = await channel.fetch_message(message_id)
                # Сообщение существует, просто возвращаем его
                logger.info(f"Сообщение состава администрации найдено: {message_id}")
                return message
            except discord.NotFound:
                # Сообщение удалено, публикуем заново
                logger.info(f"Сообщение {message_id} удалено, публикуем заново")
                return await self.publish(guild, channel)
            except discord.Forbidden:
                logger.error(f"Нет доступа к каналу {channel_id}")
                return None
                
        except Exception as e:
            logger.error(f"Ошибка восстановления состава администрации: {e}", exc_info=True)
            return None

