"""
Менеджер для управления сообщением состава администрации
"""
import discord
import logging
from typing import Optional

from config.config import ADMIN_ROLE_CATEGORIES, CHANNELS, CATEGORY_ROLE_MAPPINGS, GROUP_HIERARCHY, ROLE_MAPPINGS
from src.utils.embeds import create_admin_list_embed
from src.database.models import Database

logger = logging.getLogger(__name__)


def get_role_group_name(role_id: int) -> Optional[str]:
    """
    Получить название группы по ID роли Discord через ROLE_MAPPINGS
    
    Args:
        role_id: ID роли Discord
        
    Returns:
        Название группы или None
    """
    for group_name, mapped_role_id in ROLE_MAPPINGS.items():
        if mapped_role_id == role_id:
            return group_name
    return None


def get_role_priority(role_id: int) -> int:
    """
    Получить приоритет роли по GROUP_HIERARCHY (чем меньше индекс, тем выше приоритет)
    
    Args:
        role_id: ID роли Discord
        
    Returns:
        Индекс в GROUP_HIERARCHY или очень большое число, если роль не найдена
    """
    group_name = get_role_group_name(role_id)
    if not group_name:
        return 999999  # Очень низкий приоритет
    
    try:
        return GROUP_HIERARCHY.index(group_name.lower())
    except ValueError:
        return 999999  # Роль не найдена в иерархии


async def get_admin_members_by_category(guild: discord.Guild) -> dict:
    """
    Получить администраторов по категориям с группировкой по ролям
    Работает строго по конфигу CATEGORY_ROLE_MAPPINGS
    Пользователь отображается только в категории с самой высшей ролью по GROUP_HIERARCHY
    
    Args:
        guild: Discord сервер
        
    Returns:
        Словарь {category_name: {role_name: [discord.Member]}}
    """
    admin_categories = {}
    
    # Сначала собираем всех пользователей и их роли
    # user_id -> {role_name: category_name, role_priority: int}
    user_best_roles = {}  # user_id -> (role_name, category_name, priority)
    
    # Создаем обратный маппинг: role_id -> (role_name, category_name)
    role_to_category = {}
    
    for category_name in ADMIN_ROLE_CATEGORIES:
        admin_categories[category_name] = {}
        
        # Получаем список ролей для этой категории из конфига
        role_names = CATEGORY_ROLE_MAPPINGS.get(category_name, [])
        
        if not role_names:
            continue
        
        # Ищем роли по точному названию из конфига
        for role_name in role_names:
            role = discord.utils.get(guild.roles, name=role_name)
            if role:
                role_to_category[role.id] = (role_name, category_name)
    
    # Проходим по всем участникам и определяем их самую высшую роль
    for member in guild.members:
        member_best_role = None
        member_best_priority = 999999
        member_best_category = None
        member_best_role_name = None
        
        # Проверяем все роли участника
        for role in member.roles:
            if role.id in role_to_category:
                role_name, category_name = role_to_category[role.id]
                priority = get_role_priority(role.id)
                
                # Если эта роль выше по приоритету, сохраняем её
                if priority < member_best_priority:
                    member_best_priority = priority
                    member_best_role = role
                    member_best_category = category_name
                    member_best_role_name = role_name
        
        # Если нашли роль для участника, сохраняем
        if member_best_category and member_best_role_name:
            user_best_roles[member.id] = (member_best_role_name, member_best_category)
    
    # Теперь распределяем пользователей по категориям
    for category_name in ADMIN_ROLE_CATEGORIES:
        admin_categories[category_name] = {}
        
        # Получаем список ролей для этой категории из конфига
        role_names = CATEGORY_ROLE_MAPPINGS.get(category_name, [])
        
        if not role_names:
            continue
        
        # Для каждой роли в категории собираем участников
        for role_name in role_names:
            role_members = []
            
            # Добавляем участников, у которых эта роль - самая высшая
            for user_id, (best_role_name, best_category) in user_best_roles.items():
                if best_role_name == role_name and best_category == category_name:
                    member = guild.get_member(user_id)
                    if member:
                        role_members.append(member)
            
            # Сохраняем роль даже если нет участников (чтобы показать "(нет пользователей)")
            admin_categories[category_name][role_name] = role_members
    
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
                found_channel = guild.get_channel(channel_id)
                if not found_channel or not isinstance(found_channel, discord.TextChannel):
                    logger.error(f"Канал {channel_id} не найден или не является текстовым")
                    return None
                channel = found_channel
            
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
            found_channel = guild.get_channel(channel_id)
            if not found_channel or not isinstance(found_channel, discord.TextChannel):
                logger.warning(f"Канал {channel_id} не найден, удаляем запись из БД")
                await self.db.delete_admin_list_message(guild.id)
                return False
            channel = found_channel
            
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
            found_channel = guild.get_channel(channel_id)
            if not found_channel or not isinstance(found_channel, discord.TextChannel):
                logger.warning(f"Канал {channel_id} не найден")
                return None
            channel = found_channel
            
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

