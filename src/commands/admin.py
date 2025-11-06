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
from src.utils.admin_list_manager import AdminListManager, get_admin_members_by_category
from src.database.models import Database

logger = logging.getLogger(__name__)


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
    @app_commands.choices(action=[
        app_commands.Choice(name="publish - Опубликовать сообщение", value="publish"),
        app_commands.Choice(name="restore - Восстановить сообщение", value="restore")
    ])
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

