"""
Планировщик задач для автоматического снятия прав
"""
import asyncio
import logging
from datetime import datetime
from typing import Optional

import discord
from discord.ext import commands

from config.config import (
    SCHEDULER_CHECK_INTERVAL,
    PRIVILEGE_REMOVAL_RETRY_DELAY,
    MAX_REMOVAL_RETRIES,
    PURCHASE_LINK,
    ROLE_MAPPINGS,
    CHANNELS
)
from src.database.models import Database
from src.rcon.rcon_manager import RCONManager
from src.utils.parsers import parse_pinfo_response
from src.utils.embeds import create_privilege_removed_embed

logger = logging.getLogger(__name__)


class PrivilegeScheduler:
    """Планировщик для автоматического снятия истекших привилегий"""
    
    def __init__(self, bot: commands.Bot, db: Database, rcon_manager: RCONManager):
        self.bot = bot
        self.db = db
        self.rcon_manager = rcon_manager
        self.running = False
        self.task: Optional[asyncio.Task] = None
    
    async def start(self):
        """Запуск планировщика"""
        if self.running:
            logger.warning("Планировщик уже запущен")
            return
        
        self.running = True
        self.task = asyncio.create_task(self._scheduler_loop())
        logger.info("Планировщик запущен")
    
    async def stop(self):
        """Остановка планировщика"""
        self.running = False
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
        logger.info("Планировщик остановлен")
    
    async def _scheduler_loop(self):
        """Основной цикл планировщика"""
        while self.running:
            try:
                await self._check_expired_privileges()
            except Exception as e:
                logger.error(f"Ошибка в планировщике: {e}", exc_info=True)
            
            await asyncio.sleep(SCHEDULER_CHECK_INTERVAL)
    
    async def _check_expired_privileges(self):
        """Проверка истекших привилегий"""
        try:
            expired_privileges = await self.db.get_expired_privileges()
            
            if not expired_privileges:
                return
            
            logger.info(f"Найдено {len(expired_privileges)} истекших привилегий")
            
            for privilege in expired_privileges:
                await self._process_expired_privilege(privilege)
                
        except Exception as e:
            error_msg = str(e)
            # Игнорируем ошибки отсутствия таблиц (БД еще не настроена)
            if "doesn't exist" in error_msg or "Table" in error_msg:
                logger.debug(f"Таблицы БД еще не созданы: {error_msg}")
                return
            logger.error(f"Ошибка при проверке истекших привилегий: {e}", exc_info=True)
    
    async def _process_expired_privilege(self, privilege: dict):
        """Обработка одной истекшей привилегии"""
        discord_id = privilege['discord_id']
        steamid = privilege['steamid']
        group_name = privilege['group_name']
        privilege_id = privilege['id']
        
        logger.info(f"Обработка истекшей привилегии: {discord_id} -> {group_name}")
        
        # Ждем 2 минуты перед проверкой
        await asyncio.sleep(PRIVILEGE_REMOVAL_RETRY_DELAY)
        
        # Проверяем через RCON, что группа действительно отсутствует
        success = await self._verify_group_removed(steamid, group_name, max_retries=MAX_REMOVAL_RETRIES)
        
        if not success:
            logger.warning(f"Не удалось подтвердить снятие группы {group_name} у пользователя {steamid}")
            return
        
        # Получаем пользователя Discord
        try:
            user = await self.bot.fetch_user(discord_id)
        except discord.NotFound:
            logger.warning(f"Пользователь Discord {discord_id} не найден")
            user = None
        
        # Снимаем Discord роль
        if user:
            await self._remove_discord_role(user, group_name)
        
        # Удаляем привилегию из БД
        await self.db.delete_privilege(privilege_id)
        
        # Отправляем уведомления
        if user:
            await self._send_notifications(user, group_name)
        
        logger.info(f"Привилегия {group_name} успешно снята у пользователя {discord_id}")
    
    async def _verify_group_removed(
        self,
        steamid: str,
        group_name: str,
        max_retries: int = 3,
        retry_delay: int = 120
    ) -> bool:
        """
        Проверка, что группа удалена через RCON
        
        Args:
            steamid: SteamID пользователя
            group_name: Название группы
            max_retries: Максимальное количество попыток
            retry_delay: Задержка между попытками в секундах
            
        Returns:
            True если группа удалена, False иначе
        """
        for attempt in range(max_retries):
            try:
                response = await self.rcon_manager.send_command(f"pinfo {steamid}")
                
                if not response:
                    logger.warning(f"Не получен ответ на pinfo для {steamid} (попытка {attempt + 1}/{max_retries})")
                    if attempt < max_retries - 1:
                        await asyncio.sleep(retry_delay)
                    continue
                
                pinfo_data = parse_pinfo_response(response)
                
                # Если нет привилегий вообще
                if not pinfo_data['has_privileges']:
                    return True
                
                # Проверяем, что нужной группы нет
                group_exists = any(
                    g['name'].lower() == group_name.lower()
                    for g in pinfo_data['groups']
                )
                
                if not group_exists:
                    return True
                
                logger.warning(
                    f"Группа {group_name} все еще присутствует у {steamid} "
                    f"(попытка {attempt + 1}/{max_retries})"
                )
                
                if attempt < max_retries - 1:
                    await asyncio.sleep(retry_delay)
                    
            except Exception as e:
                logger.error(f"Ошибка при проверке группы: {e}", exc_info=True)
                if attempt < max_retries - 1:
                    await asyncio.sleep(retry_delay)
        
        return False
    
    async def _remove_discord_role(self, user: discord.User, group_name: str):
        """Снятие Discord роли у пользователя"""
        try:
            role_id = ROLE_MAPPINGS.get(group_name.lower())
            if not role_id:
                role_id = await self.db.get_role_mapping(group_name.lower())
            
            if not role_id:
                logger.warning(f"Не найден маппинг роли для группы {group_name}")
                return
            
            # Получаем все серверы, где бот и пользователь есть
            for guild in self.bot.guilds:
                member = guild.get_member(user.id)
                if not member:
                    continue
                
                role = guild.get_role(role_id)
                if not role:
                    continue
                
                if role in member.roles:
                    try:
                        await member.remove_roles(role, reason="Автоматическое снятие истекшей привилегии")
                        logger.info(f"Роль {role.name} снята у пользователя {user.id} на сервере {guild.id}")
                    except discord.Forbidden:
                        logger.warning(f"Нет прав для снятия роли на сервере {guild.id}")
                    except Exception as e:
                        logger.error(f"Ошибка снятия роли: {e}", exc_info=True)
                        
        except Exception as e:
            logger.error(f"Ошибка при снятии Discord роли: {e}", exc_info=True)
    
    async def _send_notifications(self, user: discord.User, group_name: str):
        """Отправка уведомлений пользователю и в канал логов"""
        try:
            # Отправляем ЛС пользователю
            purchase_link = PURCHASE_LINK or "Не указана"
            embed = create_privilege_removed_embed(
                user,
                f"Истек срок действия привилегии {group_name}",
                purchase_link
            )
            
            try:
                await user.send(embed=embed)
            except discord.Forbidden:
                logger.warning(f"Не удалось отправить ЛС пользователю {user.id}")
            
            # Логируем в канал
            await self._log_to_channel(
                f"Автоматическое снятие привилегии `{group_name}` у {user.mention} (ID: {user.id})\n"
                f"Причина: Истек срок действия"
            )
            
        except Exception as e:
            logger.error(f"Ошибка при отправке уведомлений: {e}", exc_info=True)
    
    async def _log_to_channel(self, message: str):
        """Логирование в канал"""
        try:
            channel_id = CHANNELS.get('ADMIN_LOGS')
            if not channel_id:
                return
            
            for guild in self.bot.guilds:
                channel = guild.get_channel(channel_id)
                if channel and isinstance(channel, discord.TextChannel):
                    await channel.send(message)
                    break
        except Exception as e:
            logger.error(f"Ошибка логирования в канал: {e}")

