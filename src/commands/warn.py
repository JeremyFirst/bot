"""
Команды для выговоров
"""
import discord
from discord import app_commands
from discord.ext import commands
import logging
import asyncio
from typing import Optional

from config.config import CHANNELS, PUNISHMENT_LIMITS, PURCHASE_LINK, ROLE_MAPPINGS
from src.utils.embeds import create_warning_embed, create_error_embed
from src.database.models import Database
from src.rcon.rcon_manager import RCONManager
from src.utils.parsers import parse_pinfo_response, parse_removegroup_response
from src.utils.embeds import create_privilege_removed_embed

logger = logging.getLogger(__name__)


def get_user_category(user: discord.Member) -> int:
    """
    Определение категории пользователя (0 = Наборная, 1 = Донатная)
    По умолчанию возвращает 0 (Наборная)
    """
    # Можно добавить логику определения по ролям
    # Пока возвращаем 0 (Наборная)
    return 0


class WarnCommands(commands.Cog):
    """Команды для выговоров"""
    
    def __init__(self, bot: commands.Bot, db: Database, rcon_manager: RCONManager):
        self.bot = bot
        self.db = db
        self.rcon_manager = rcon_manager
    
    @app_commands.command(name="warn", description="Выдать выговор пользователю")
    @app_commands.describe(
        user="Пользователь для выговора",
        reason="Причина выговора"
    )
    async def warn(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        reason: Optional[str] = None
    ):
        """Команда /warn - выдача выговора"""
        try:
            if not interaction.guild:
                await interaction.response.send_message(
                    embed=create_error_embed("Ошибка", "Эта команда доступна только на сервере"),
                    ephemeral=True
                )
                return
            
            await interaction.response.defer()
            
            # Определяем категорию пользователя
            category = get_user_category(user)
            category_name = "Наборная" if category == 0 else "Донатная"
            limit = PUNISHMENT_LIMITS['recruitment'] if category == 0 else PUNISHMENT_LIMITS['donat']
            
            # Создаем выговор в БД
            await self.db.create_warning(user.id, interaction.user.id, reason or "Не указана", category)
            
            # Получаем количество выговоров
            warnings_count = await self.db.get_warnings_count(user.id, category)
            
            # Получаем executor как Member
            executor = interaction.user
            if not isinstance(executor, discord.Member) and interaction.guild:
                executor = interaction.guild.get_member(executor.id) or executor
            
            # Создаем Embed
            executor_member = executor if isinstance(executor, discord.Member) else user
            embed = create_warning_embed(
                user,
                executor_member,
                reason or "Не указана",
                warnings_count,
                limit,
                category_name
            )
            
            # Отправляем в канал предупреждений
            channel_id = CHANNELS.get('WARNINGS_CHANNEL')
            if channel_id and interaction.guild:
                channel = interaction.guild.get_channel(channel_id)
                if channel and isinstance(channel, discord.TextChannel):
                    await channel.send(f"{user.mention}", embed=embed)
            
            # Отправляем в ЛС пользователю
            try:
                dm_embed = embed.copy()
                if channel_id and channel:
                    message_link = f"https://discord.com/channels/{interaction.guild.id}/{channel_id}"
                    dm_embed.add_field(
                        name="Ссылка на сообщение",
                        value=f"[Перейти к сообщению]({message_link})",
                        inline=False
                    )
                await user.send(embed=dm_embed)
            except discord.Forbidden:
                logger.warning(f"Не удалось отправить ЛС пользователю {user.id}")
            
            await interaction.followup.send(embed=embed)
            
            # Проверяем лимит выговоров
            if warnings_count >= limit:
                logger.info(f"Достигнут лимит выговоров для пользователя {user.id}, инициируем снятие прав")
                await self._remove_privileges_for_warnings(user, interaction.guild, reason or "Достигнут лимит выговоров")
            
        except Exception as e:
            logger.error(f"Ошибка в команде warn: {e}", exc_info=True)
            await interaction.followup.send(
                embed=create_error_embed("Ошибка", f"Произошла ошибка: {str(e)}"),
                ephemeral=True
            )
    
    async def _remove_privileges_for_warnings(
        self,
        user: discord.Member,
        guild: discord.Guild,
        reason: str
    ):
        """Снятие прав при достижении лимита выговоров"""
        try:
            # Получаем привилегию пользователя
            privilege = await self.db.get_privilege_by_discord(user.id)
            
            if not privilege:
                logger.warning(f"У пользователя {user.id} нет активных привилегий для снятия")
                return
            
            group_name = privilege['group_name']
            steamid = privilege['steamid']
            
            # Выполняем RCON команду removegroup
            logger.info(f"Выполнение команды removegroup для SteamID: {steamid}, группа: {group_name}")
            response = await self.rcon_manager.send_command(f"removegroup {steamid} {group_name}")
            
            if not response:
                logger.error(f"Не удалось получить ответ на removegroup для {steamid}")
                await self._log_to_channel(
                    guild,
                    f"Ошибка при снятии прав у {user.mention}: не получен ответ от RCON"
                )
                return
            
            # Парсим ответ
            success = parse_removegroup_response(response)
            
            if not success:
                logger.warning(f"Команда removegroup не подтвердила успешность: {response[:200]}")
                await self._log_to_channel(
                    guild,
                    f"⚠️ Не удалось подтвердить снятие прав у {user.mention}. Ответ: {response[:200]}"
                )
                return
            
            # Проверяем через pinfo, что группа действительно удалена
            await asyncio.sleep(2)  # Небольшая задержка
            pinfo_response = await self.rcon_manager.send_command(f"pinfo {steamid}")
            
            if pinfo_response:
                pinfo_data = parse_pinfo_response(pinfo_response)
                # Проверяем, что удаленной группы нет в списке
                group_exists = any(
                    g['name'].lower() == group_name.lower()
                    for g in pinfo_data['groups']
                )
                
                if group_exists:
                    logger.warning(f"Группа {group_name} все еще присутствует у пользователя {steamid}")
                    await self._log_to_channel(
                        guild,
                        f"Группа {group_name} все еще присутствует у {user.mention} после removegroup"
                    )
                    return
            
            # Снимаем Discord роль
            role_id = ROLE_MAPPINGS.get(group_name.lower())
            if not role_id:
                role_id = await self.db.get_role_mapping(group_name.lower())
            
            if role_id:
                try:
                    role = guild.get_role(role_id)
                    if role:
                        await user.remove_roles(role, reason=f"Снятие прав из-за лимита выговоров")
                        logger.info(f"Роль {role.name} снята у пользователя {user.id}")
                except Exception as e:
                    logger.error(f"Ошибка снятия роли: {e}", exc_info=True)
            
            # Удаляем привилегии и выговоры из БД
            await self.db.delete_privileges_by_discord(user.id)
            await self.db.delete_warnings_by_discord(user.id)
            
            # Создаем Embed для уведомления
            purchase_link = PURCHASE_LINK or "Не указана"
            embed = create_privilege_removed_embed(user, reason, purchase_link)
            
            # Отправляем в ЛС
            try:
                await user.send(embed=embed)
            except discord.Forbidden:
                logger.warning(f"Не удалось отправить ЛС пользователю {user.id}")
            
            # Логируем в канал
            await self._log_to_channel(
                guild,
                f"Привилегии сняты у {user.mention} (SteamID: {steamid}, группа: {group_name})\n"
                f"Причина: {reason}"
            )
            
        except Exception as e:
            logger.error(f"Ошибка при снятии прав из-за выговоров: {e}", exc_info=True)
            await self._log_to_channel(
                guild,
                f"Ошибка при снятии прав у {user.mention}: {str(e)}"
            )
    
    async def _log_to_channel(self, guild: discord.Guild, message: str):
        """Логирование в канал"""
        try:
            channel_id = CHANNELS.get('ADMIN_LOGS')
            if channel_id:
                channel = guild.get_channel(channel_id)
                if channel and isinstance(channel, discord.TextChannel):
                    await channel.send(message)
        except Exception as e:
            logger.error(f"Ошибка логирования в канал: {e}")


async def setup(bot: commands.Bot, db: Database, rcon_manager: RCONManager):
    """Добавление команд в бота"""
    import asyncio
    await bot.add_cog(WarnCommands(bot, db, rcon_manager))

