"""
Команды для управления привилегиями
"""
import discord
from discord import app_commands
from discord.ext import commands
import logging
import re
from typing import Optional

from config.config import ROLE_MAPPINGS, CHANNELS, PURCHASE_LINK
from src.rcon.rcon_manager import RCONManager
from src.utils.parsers import parse_pinfo_response, select_highest_group
from src.utils.time_utils import utc_to_msk
from src.utils.embeds import (
    create_privilege_added_embed,
    create_no_privileges_embed,
    create_error_embed
)
from src.database.models import Database

logger = logging.getLogger(__name__)


def is_valid_steamid64(steamid: str) -> bool:
    """Проверка валидности SteamID64"""
    # SteamID64 должен быть числом из 17 цифр
    return bool(re.match(r'^\d{17}$', steamid))


class PrivilegeCommands(commands.Cog):
    """Команды для управления привилегиями"""
    
    def __init__(self, bot: commands.Bot, rcon_manager: RCONManager, db: Database):
        self.bot = bot
        self.rcon_manager = rcon_manager
        self.db = db
    
    @app_commands.command(name="addprivilege", description="Выдаёт/регистрирует привилегию пользователю")
    @app_commands.describe(
        steamid="SteamID64 пользователя",
        user="Discord пользователь (если не указан, используется исполнитель команды)"
    )
    async def addprivilege(
        self,
        interaction: discord.Interaction,
        steamid: str,
        user: Optional[discord.Member] = None
    ):
        """Команда /addprivilege - выдача привилегий"""
        try:
            if not interaction.guild:
                await interaction.response.send_message("Эта команда доступна только на сервере", ephemeral=True)
                return
            
            # Определяем Discord пользователя
            if user:
                discord_user = user
            elif isinstance(interaction.user, discord.Member):
                discord_user = interaction.user
            else:
                # Если interaction.user не Member, получаем его из гильдии
                discord_user = interaction.guild.get_member(interaction.user.id)
                if not discord_user:
                    await interaction.response.send_message("Не удалось найти пользователя на сервере", ephemeral=True)
                    return
            
            if not interaction.guild:
                await interaction.response.send_message(
                    embed=create_error_embed("Ошибка", "Эта команда доступна только на сервере"),
                    ephemeral=True
                )
                return
            
            # Валидация SteamID
            if not is_valid_steamid64(steamid):
                await interaction.response.send_message(
                    embed=create_error_embed("Ошибка", "Неверный формат SteamID64. Должно быть 17 цифр."),
                    ephemeral=True
                )
                return
            
            await interaction.response.defer()
            
            # Отправляем RCON команду pinfo
            logger.info(f"Отправка команды pinfo для SteamID: {steamid}")
            response = await self.rcon_manager.send_command(f"pinfo {steamid}")
            
            if not response:
                await interaction.followup.send(
                    embed=create_error_embed("Ошибка", "Не удалось получить ответ от RCON сервера"),
                    ephemeral=True
                )
                return
            
            # Парсим ответ
            pinfo_data = parse_pinfo_response(response)
            
            if not pinfo_data['has_privileges']:
                embed = create_no_privileges_embed(steamid, pinfo_data['raw_response'])
                await interaction.followup.send(embed=embed)
                
                # Логируем в канал
                await self._log_to_channel(
                    interaction.guild,
                    f"❌ Попытка выдачи привилегии пользователю {discord_user.mention} (SteamID: {steamid}) - нет привилегий на сервере"
                )
                return
            
            # Выбираем самую высокую группу
            highest_group = select_highest_group(pinfo_data['groups'])
            
            if not highest_group:
                await interaction.followup.send(
                    embed=create_error_embed(
                        "Ошибка",
                        "Не удалось определить группу из ответа RCON. Проверьте конфигурацию GROUP_NAME_DATABASE."
                    ),
                    ephemeral=True
                )
                return
            
            group_name = highest_group['name']
            expires_at_utc = highest_group.get('expires_at_utc')
            permanent = highest_group.get('permanent', False)
            
            # Конвертируем время UTC → MSK
            expires_at_msk = None
            if expires_at_utc and not permanent:
                expires_at_msk = utc_to_msk(expires_at_utc)
            
            # Сохраняем в БД
            try:
                # Создаем/обновляем пользователя
                await self.db.create_user(discord_user.id, steamid)
                
                # Создаем привилегию
                await self.db.create_privilege(
                    discord_user.id,
                    steamid,
                    group_name,
                    expires_at_msk,
                    expires_at_utc,
                    permanent
                )
                
                logger.info(f"Привилегия сохранена в БД: {discord_user.id} -> {group_name}")
            except Exception as e:
                logger.error(f"Ошибка сохранения в БД: {e}", exc_info=True)
                await interaction.followup.send(
                    embed=create_error_embed("Ошибка БД", f"Не удалось сохранить данные: {str(e)}"),
                    ephemeral=True
                )
                return
            
            # Выдаем Discord роль
            role_id = ROLE_MAPPINGS.get(group_name.lower())
            if not role_id:
                # Пробуем получить из БД
                role_id = await self.db.get_role_mapping(group_name.lower())
            
            if role_id:
                try:
                    role = interaction.guild.get_role(role_id)
                    if role:
                        await discord_user.add_roles(role, reason=f"Привилегия {group_name} выдана через бота")
                        logger.info(f"Роль {role.name} выдана пользователю {discord_user.id}")
                    else:
                        logger.warning(f"Роль с ID {role_id} не найдена на сервере")
                except discord.Forbidden:
                    logger.error(f"Нет прав для выдачи роли {role_id}")
                    await interaction.followup.send(
                        embed=create_error_embed("Ошибка", "Бот не имеет прав для выдачи ролей"),
                        ephemeral=True
                    )
                    return
                except Exception as e:
                    logger.error(f"Ошибка выдачи роли: {e}", exc_info=True)
            else:
                logger.warning(f"Не найден маппинг роли для группы {group_name}")
                await self._log_to_channel(
                    interaction.guild,
                    f"⚠️ Не найден маппинг роли для группы `{group_name}`. Пользователь: {discord_user.mention}"
                )
            
            # Получаем executor как Member
            executor = interaction.user
            if not isinstance(executor, discord.Member) and interaction.guild:
                executor = interaction.guild.get_member(executor.id) or executor
            
            # Создаем Embed с результатом
            executor_member = executor if isinstance(executor, discord.Member) else discord_user
            embed = create_privilege_added_embed(
                discord_user,
                steamid,
                group_name,
                expires_at_msk,
                executor_member,
                permanent
            )
            
            await interaction.followup.send(embed=embed)
            
            # Отправляем ЛС пользователю
            try:
                dm_embed = embed.copy()
                dm_embed.set_footer(text="Сохраните это сообщение, чтобы не потерять информацию о привилегии")
                await discord_user.send(embed=dm_embed)
                logger.info(f"ЛС отправлено пользователю {discord_user.id} о выдаче привилегии {group_name}")
            except discord.Forbidden:
                logger.warning(f"Не удалось отправить ЛС пользователю {discord_user.id} (закрыты ЛС)")
            except Exception as e:
                logger.error(f"Ошибка отправки ЛС пользователю {discord_user.id}: {e}")
            
            # Логируем в канал
            await self._log_to_channel(
                interaction.guild,
                f"Привилегия `{group_name}` выдана пользователю {discord_user.mention} (SteamID: {steamid})"
            )
            
        except Exception as e:
            logger.error(f"Ошибка в команде addprivilege: {e}", exc_info=True)
            await interaction.followup.send(
                embed=create_error_embed("Ошибка", f"Произошла ошибка: {str(e)}"),
                ephemeral=True
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


async def setup(bot: commands.Bot, rcon_manager: RCONManager, db: Database):
    """Добавление команд в бота"""
    await bot.add_cog(PrivilegeCommands(bot, rcon_manager, db))

