"""
Команды для управления администрацией
"""
import discord
from discord import app_commands
from discord.ext import commands
import logging
from typing import Optional

from config.config import ADMIN_ROLE_CATEGORIES, ROLE_MAPPINGS
from src.utils.embeds import create_admin_list_embed, create_error_embed, create_success_embed
from src.utils.admin_list_manager import AdminListManager
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
        for role in category_roles:
            role_members = [member for member in guild.members if role in member.roles]
            # Сохраняем роль даже если нет участников (чтобы показать "(нет пользователей)")
            admin_categories[category_name][role.name] = role_members
    
    return admin_categories


class AdminCommands(commands.Cog):
    """Команды для управления администрацией"""
    
    def __init__(self, bot: commands.Bot, db: Optional[Database] = None):
        self.bot = bot
        self.db = db
        self.admin_list_manager = AdminListManager(bot, db) if db else None
    
    @app_commands.command(name="showadmin", description="Отображает текущий состав админов по ролям")
    @app_commands.describe(category="Категория администрации (необязательно)")
    async def showadmin(
        self,
        interaction: discord.Interaction,
        category: Optional[str] = None
    ):
        """Команда /showadmin - отображает состав администрации"""
        try:
            if not interaction.guild:
                await interaction.response.send_message(
                    embed=create_error_embed("Ошибка", "Эта команда доступна только на сервере"),
                    ephemeral=True
                )
                return
            
            # Получаем администраторов по категориям
            admin_categories = await get_admin_members_by_category(interaction.guild)
            
            # Если указана категория, фильтруем
            if category:
                category_lower = category.lower()
                filtered_categories = {
                    name: members
                    for name, members in admin_categories.items()
                    if category_lower in name.lower()
                }
                if not filtered_categories:
                    await interaction.response.send_message(
                        embed=create_error_embed("Ошибка", f"Категория '{category}' не найдена"),
                        ephemeral=True
                    )
                    return
                admin_categories = filtered_categories
            
            embed = create_admin_list_embed(admin_categories, interaction.guild)
            await interaction.response.send_message(embed=embed)
            
        except Exception as e:
            logger.error(f"Ошибка в команде showadmin: {e}", exc_info=True)
            await interaction.response.send_message(
                embed=create_error_embed("Ошибка", f"Произошла ошибка: {str(e)}"),
                ephemeral=True
            )
    
    @app_commands.command(name="adminlist", description="Управление сообщением состава администрации")
    @app_commands.describe(action="Действие: publish (опубликовать) или restore (восстановить)")
    async def adminlist(
        self,
        interaction: discord.Interaction,
        action: str
    ):
        """Команда /adminlist - управление сообщением состава администрации"""
        try:
            if not interaction.guild:
                await interaction.response.send_message(
                    embed=create_error_embed("Ошибка", "Эта команда доступна только на сервере"),
                    ephemeral=True
                )
                return
            
            if not self.admin_list_manager:
                await interaction.response.send_message(
                    embed=create_error_embed("Ошибка", "База данных не подключена"),
                    ephemeral=True
                )
                return
            
            action_lower = action.lower()
            
            if action_lower == "publish":
                await interaction.response.defer(ephemeral=True)
                
                # Публикуем сообщение
                message = await self.admin_list_manager.publish(interaction.guild)
                
                if message:
                    await interaction.followup.send(
                        embed=create_success_embed(
                            "Успешно",
                            f"Сообщение состава администрации опубликовано в канале <#{message.channel.id}>"
                        ),
                        ephemeral=True
                    )
                else:
                    await interaction.followup.send(
                        embed=create_error_embed(
                            "Ошибка",
                            "Не удалось опубликовать сообщение. Проверьте настройки канала (ADMIN_LIST_CHANNEL) в конфиге."
                        ),
                        ephemeral=True
                    )
            
            elif action_lower == "restore":
                await interaction.response.defer(ephemeral=True)
                
                # Восстанавливаем сообщение
                message = await self.admin_list_manager.restore(interaction.guild)
                
                if message:
                    await interaction.followup.send(
                        embed=create_success_embed(
                            "Успешно",
                            f"Сообщение состава администрации восстановлено в канале <#{message.channel.id}>"
                        ),
                        ephemeral=True
                    )
                else:
                    await interaction.followup.send(
                        embed=create_error_embed(
                            "Ошибка",
                            "Не удалось восстановить сообщение. Возможно, оно не было опубликовано ранее."
                        ),
                        ephemeral=True
                    )
            
            else:
                await interaction.response.send_message(
                    embed=create_error_embed(
                        "Ошибка",
                        "Неизвестное действие. Используйте: `publish` или `restore`"
                    ),
                    ephemeral=True
                )
            
        except Exception as e:
            logger.error(f"Ошибка в команде adminlist: {e}", exc_info=True)
            try:
                await interaction.response.send_message(
                    embed=create_error_embed("Ошибка", f"Произошла ошибка: {str(e)}"),
                    ephemeral=True
                )
            except:
                await interaction.followup.send(
                    embed=create_error_embed("Ошибка", f"Произошла ошибка: {str(e)}"),
                    ephemeral=True
                )


async def setup(bot: commands.Bot, db: Optional[Database] = None):
    """Добавление команд в бота"""
    await bot.add_cog(AdminCommands(bot, db))

