"""
Команды для управления администрацией
"""
import discord
from discord import app_commands
from discord.ext import commands
import logging
from typing import Optional

from config.config import ADMIN_ROLE_CATEGORIES, ROLE_MAPPINGS
from src.utils.embeds import create_admin_list_embed, create_error_embed

logger = logging.getLogger(__name__)


async def get_admin_members_by_category(guild: discord.Guild) -> dict:
    """
    Получить администраторов по категориям
    
    Args:
        guild: Discord сервер
        
    Returns:
        Словарь {category_name: [discord.Member]}
    """
    admin_categories = {}
    
    for category_name in ADMIN_ROLE_CATEGORIES:
        admin_categories[category_name] = []
        
        # Получаем все роли сервера
        for role in guild.roles:
            if role.name == category_name:
                # Получаем всех участников с этой ролью
                for member in guild.members:
                    if role in member.roles:
                        admin_categories[category_name].append(member)
                break
    
    return admin_categories


class AdminCommands(commands.Cog):
    """Команды для управления администрацией"""
    
    def __init__(self, bot: commands.Bot):
        self.bot = bot
    
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
            
            # Убираем дубликаты (если пользователь в нескольких категориях)
            seen_members = set()
            for cat_name in admin_categories:
                admin_categories[cat_name] = [
                    member for member in admin_categories[cat_name]
                    if member.id not in seen_members and not seen_members.add(member.id)
                ]
            
            embed = create_admin_list_embed(admin_categories, interaction.guild)
            await interaction.response.send_message(embed=embed)
            
        except Exception as e:
            logger.error(f"Ошибка в команде showadmin: {e}", exc_info=True)
            await interaction.response.send_message(
                embed=create_error_embed("Ошибка", f"Произошла ошибка: {str(e)}"),
                ephemeral=True
            )


async def setup(bot: commands.Bot):
    """Добавление команд в бота"""
    await bot.add_cog(AdminCommands(bot))

