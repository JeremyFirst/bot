"""
Тикет-система для Discord-бота
"""
import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import discord
from discord import app_commands
from discord.ext import commands

from config.config import TICKET_SYSTEM, EPHEMERAL_DELETE_AFTER
from src.database.models import Database
from src.utils.message_utils import send_ephemeral_with_delete

logger = logging.getLogger(__name__)


TICKET_REASONS: Dict[str, Dict[str, object]] = {
    "admin_complaint": {
        "label": "Жалоба на Администрацию",
        "modal_title": "Жалоба на администрацию",
        "requires_violator": True,
        "requires_evidence": True,
        "description": "Опишите конфликт с администрацией и приложите информацию для проверки."
    },
    "player_report": {
        "label": "Репорт на игрока",
        "modal_title": "Репорт на игрока",
        "requires_violator": True,
        "requires_evidence": True,
        "description": "Укажите нарушителя, приложите доказательства и опишите ситуацию."
    },
    "technical_issue": {
        "label": "Техническая проблема",
        "modal_title": "Техническая проблема",
        "requires_violator": False,
        "requires_evidence": False,
        "description": "Расскажите подробно о технической проблеме, чтобы мы могли помочь."
    },
    "punishment_appeal": {
        "label": "Апелляция наказания",
        "modal_title": "Апелляция наказания",
        "requires_violator": False,
        "requires_evidence": True,
        "description": "Предоставьте информацию по наказанию и ваши аргументы для пересмотра."
    }
}


class TicketManager:
    """Управление тикетами и взаимодействие с базой данных."""

    def __init__(self, bot: commands.Bot, db: Database):
        self.bot = bot
        self.db = db
        self._lock = asyncio.Lock()
        self.transcript_dir = Path(TICKET_SYSTEM.get('TRANSCRIPT_DIRECTORY', 'transcripts'))
        self.transcript_dir.mkdir(parents=True, exist_ok=True)

    # --- Общие проверки и утилиты ---

    def is_enabled(self) -> bool:
        return TICKET_SYSTEM.get('ENABLED', False)

    def get_staff_roles(self, guild: discord.Guild) -> List[discord.Role]:
        role_ids = TICKET_SYSTEM.get('ADMIN_ROLES', []) or []
        roles: List[discord.Role] = []
        for role_id in role_ids:
            role = guild.get_role(role_id)
            if role:
                roles.append(role)
        return roles

    def is_staff(self, member: discord.Member) -> bool:
        if member.guild_permissions.manage_guild or member.guild_permissions.manage_channels:
            return True
        staff_roles = self.get_staff_roles(member.guild)
        return any(role in member.roles for role in staff_roles)

    async def _get_assignee_candidate(self, guild: discord.Guild) -> Optional[discord.Member]:
        """Определить администратора для назначения тикета по наименьшей нагрузке."""
        staff_roles = self.get_staff_roles(guild)
        if not staff_roles:
            return None

        candidates: Dict[int, discord.Member] = {}
        for role in staff_roles:
            for member in role.members:
                if member.bot:
                    continue
                candidates[member.id] = member

        if not candidates:
            return None

        load_map = await self.db.get_ticket_load_by_assignee(guild.id)

        def sort_key(member: discord.Member) -> Tuple[int, int, datetime]:
            load = load_map.get(member.id, 0)
            top_role_position = member.top_role.position if member.top_role else 0
            joined_at = member.joined_at or datetime.now(timezone.utc)
            return (load, -top_role_position, joined_at)

        sorted_members = sorted(candidates.values(), key=sort_key)
        return sorted_members[0] if sorted_members else None

    # --- Панель тикетов ---

    async def register_panel_view(self, guild_id: int, message_id: int):
        """Зарегистрировать persistent view для панели."""
        view = TicketPanelView(self)
        self.bot.add_view(view, message_id=message_id)
        logger.debug(f"Persistent view для панели тикетов зарегистрирован (guild={guild_id}, message={message_id})")

    async def ensure_panels_loaded(self):
        """Восстановить панели тикетов после перезапуска."""
        if not self.is_enabled():
            return
        for guild in self.bot.guilds:
            try:
                panel = await self.db.get_ticket_panel(guild.id)
                if panel:
                    await self.register_panel_view(guild.id, panel['message_id'])
            except Exception as exc:
                logger.error(f"Ошибка восстановления панели тикетов для сервера {guild.id}: {exc}", exc_info=True)

    async def publish_panel(self, guild: discord.Guild) -> Optional[discord.Message]:
        """Опубликовать или обновить сообщение панели."""
        if not self.is_enabled():
            return None

        channel_id = TICKET_SYSTEM.get('PANEL_CHANNEL')
        if not channel_id:
            raise ValueError("Не задан канал панели тикетов (PANEL_CHANNEL).")

        channel = guild.get_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            raise ValueError("Указанный канал для панели тикетов не найден или не текстовый.")

        embed = discord.Embed(
            title="Служба поддержки Demonic Project",
            description=(
                "Используйте селектор ниже, чтобы открыть тикет по нужной категории.\n"
                "Пожалуйста, заполните модальное окно максимально подробно. "
                "После создания тикета вас свяжет назначенный администратор."
            ),
            color=discord.Color.blurple(),
            timestamp=datetime.now(timezone.utc)
        )
        embed.set_footer(text="Demonic Project • Тикет-система")

        view = TicketPanelView(self)
        message = await channel.send(embed=embed, view=view)
        await self.db.upsert_ticket_panel(guild.id, channel.id, message.id)
        await self.register_panel_view(guild.id, message.id)
        return message

    async def restore_panel(self, guild: discord.Guild) -> Optional[discord.Message]:
        """Восстановить сообщение панели (повторно создать, если удалено)."""
        panel = await self.db.get_ticket_panel(guild.id)
        if not panel:
            return await self.publish_panel(guild)

        channel = guild.get_channel(panel['channel_id'])
        if not isinstance(channel, discord.TextChannel):
            return await self.publish_panel(guild)

        try:
            message = await channel.fetch_message(panel['message_id'])
            await self.register_panel_view(guild.id, message.id)
            return message
        except discord.NotFound:
            logger.warning("Сообщение панели тикетов не найдено, создаю заново.")
            return await self.publish_panel(guild)

    # --- Тикеты ---

    async def ensure_ticket_views_loaded(self):
        """Восстановить вьюхи открытых тикетов после перезапуска."""
        if not self.is_enabled():
            return
        for guild in self.bot.guilds:
            try:
                tickets = await self.db.get_open_tickets(guild.id)
                for ticket in tickets:
                    message_id = ticket.get('control_message_id')
                    if not message_id:
                        continue
                    view = TicketView(self, ticket_id=ticket['id'])
                    self.bot.add_view(view, message_id=message_id)
            except Exception as exc:
                logger.error(f"Ошибка восстановления тикетов для сервера {guild.id}: {exc}", exc_info=True)

    async def create_ticket_channel(
        self,
        interaction: discord.Interaction,
        reason_key: str,
        form_data: Dict[str, str]
    ) -> Tuple[discord.TextChannel, int, Optional[discord.Member]]:
        guild = interaction.guild
        if not guild:
            raise ValueError("Команда доступна только на сервере.")

        category_id = TICKET_SYSTEM.get('CATEGORY_ID')
        if not category_id:
            raise ValueError("Не указана категория для тикетов (CATEGORY_ID).")

        category = guild.get_channel(category_id)
        if category is None or not isinstance(category, discord.CategoryChannel):
            raise ValueError("Категория для тикетов не найдена или указана неверно.")

        async with self._lock:
            ticket_number = await self.db.get_next_ticket_number(guild.id)

        padding = max(2, int(TICKET_SYSTEM.get('TICKET_NUMBER_PADDING', 4)))
        channel_name = f"ticket-{ticket_number:0{padding}d}"

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.user: discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                attach_files=True,
                embed_links=True
            )
        }

        for role in self.get_staff_roles(guild):
            overwrites[role] = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                manage_messages=True,
                attach_files=True,
                embed_links=True
            )

        channel = await guild.create_text_channel(
            channel_name,
            category=category,
            overwrites=overwrites,
            reason=f"Создание тикета {channel_name} пользователем {interaction.user}"
        )

        assignee = await self._get_assignee_candidate(guild)

        reason_info = TICKET_REASONS.get(reason_key, {})
        form_payload = {
            "reason_key": reason_key,
            "reason_label": reason_info.get("label", reason_key),
            "fields": form_data
        }

        ticket_id = await self.db.create_ticket(
            guild_id=guild.id,
            channel_id=channel.id,
            owner_id=interaction.user.id,
            ticket_number=ticket_number,
            reason=reason_key,
            form_data=json.dumps(form_payload, ensure_ascii=False),
            assignee_id=assignee.id if assignee else None
        )

        embed = discord.Embed(
            title=f"Тикет #{ticket_number:0{padding}d}",
            description=reason_info.get("description", ""),
            color=discord.Color.dark_teal(),
            timestamp=datetime.now(timezone.utc)
        )
        embed.add_field(
            name="Заявитель",
            value=f"{interaction.user.mention}\nID: `{interaction.user.id}`",
            inline=False
        )
        embed.add_field(
            name="Причина",
            value=reason_info.get("label", reason_key),
            inline=False
        )

        if assignee:
            embed.add_field(
                name="Назначенный администратор",
                value=f"{assignee.mention}\nID: `{assignee.id}`",
                inline=False
            )
        else:
            embed.add_field(
                name="Назначенный администратор",
                value="Будет назначен автоматически, ожидайте.",
                inline=False
            )

        field_mapping = {
            "steam_id": "SteamID",
            "violator": "SteamID/Ник нарушителя",
            "evidence": "Доказательства",
            "date": "Дата происходящего",
            "details": "Описание"
        }
        for key, value in form_data.items():
            if not value:
                continue
            label = field_mapping.get(key, key)
            embed.add_field(name=label, value=value, inline=False)

        view = TicketView(self, ticket_id=ticket_id)
        message = await channel.send(
            content=f"{interaction.user.mention}",
            embed=embed,
            view=view
        )

        await self.db.set_ticket_control_message(ticket_id, message.id)
        self.bot.add_view(view, message_id=message.id)

        if assignee:
            await channel.send(f"{assignee.mention}, назначен новый тикет.")

        await self._log_ticket_creation(channel.guild, channel, interaction.user, assignee, ticket_number, reason_key)
        return channel, ticket_number, assignee

    async def _log_ticket_creation(
        self,
        guild: discord.Guild,
        channel: discord.TextChannel,
        opener: discord.Member,
        assignee: Optional[discord.Member],
        ticket_number: int,
        reason_key: str
    ):
        log_channel_id = TICKET_SYSTEM.get('LOG_CHANNEL')
        if not log_channel_id:
            return
        log_channel = guild.get_channel(log_channel_id)
        if not isinstance(log_channel, discord.TextChannel):
            return

        reason_label = TICKET_REASONS.get(reason_key, {}).get("label", reason_key)
        embed = discord.Embed(
            title=f"Открыт тикет #{ticket_number:0{TICKET_SYSTEM.get('TICKET_NUMBER_PADDING', 4)}d}",
            color=discord.Color.green(),
            timestamp=datetime.now(timezone.utc)
        )
        embed.add_field(name="Канал", value=channel.mention, inline=False)
        embed.add_field(name="Заявитель", value=f"{opener.mention} (`{opener.id}`)", inline=True)
        embed.add_field(
            name="Назначенный администратор",
            value=f"{assignee.mention} (`{assignee.id}`)" if assignee else "Не назначен",
            inline=True
        )
        embed.add_field(name="Причина", value=reason_label, inline=False)

        view = discord.ui.View()
        view.add_item(discord.ui.Button(style=discord.ButtonStyle.link, label="Открыть тикет", url=channel.jump_url))
        await log_channel.send(embed=embed, view=view)

    async def append_ticket_note(
        self,
        ticket_id: int,
        author: discord.Member,
        note: str
    ):
        ticket = await self.db.get_ticket_by_id(ticket_id)
        if not ticket:
            raise ValueError("Тикет не найден.")

        form_payload = json.loads(ticket.get('form_data') or "{}")
        updates = form_payload.setdefault("updates", [])
        updates.append({
            "author_id": author.id,
            "author_name": author.display_name,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "content": note
        })
        await self.db.update_ticket_form_data(ticket_id, json.dumps(form_payload, ensure_ascii=False))

    async def assign_ticket(self, ticket_id: int, member: discord.Member):
        await self.db.set_ticket_assignee(ticket_id, member.id)

    async def release_ticket(self, ticket_id: int):
        await self.db.set_ticket_assignee(ticket_id, None)

    async def close_ticket(
        self,
        ticket_id: int,
        closer: discord.Member,
        closing_comment: Optional[str] = None
    ):
        ticket = await self.db.get_ticket_by_id(ticket_id)
        if not ticket:
            raise ValueError("Тикет не найден.")

        channel = closer.guild.get_channel(ticket['channel_id'])
        if not isinstance(channel, discord.TextChannel):
            await self.db.close_ticket(ticket_id)
            return

        transcript_file, message_count = await self._build_transcript(channel)
        transcript_url: Optional[str] = None

        log_channel_id = TICKET_SYSTEM.get('LOG_CHANNEL')
        log_channel = closer.guild.get_channel(log_channel_id) if log_channel_id else None

        embed = discord.Embed(
            title=f"Тикет #{ticket['ticket_number']:0{TICKET_SYSTEM.get('TICKET_NUMBER_PADDING', 4)}d} закрыт",
            color=discord.Color.gold(),
            timestamp=datetime.now(timezone.utc)
        )
        embed.add_field(name="Канал", value=f"<#{ticket['channel_id']}>", inline=False)
        embed.add_field(name="Закрыл", value=f"{closer.mention} (`{closer.id}`)", inline=False)
        if closing_comment:
            embed.add_field(name="Комментарий", value=closing_comment, inline=False)
        embed.add_field(name="Количество сообщений", value=str(message_count), inline=True)

        if log_channel and transcript_file:
            sent_message = await log_channel.send(embed=embed, file=discord.File(transcript_file))
            if sent_message.attachments:
                transcript_url = sent_message.attachments[0].url
                view = discord.ui.View()
                view.add_item(discord.ui.Button(
                    style=discord.ButtonStyle.link,
                    label="Открыть транскрипт",
                    url=transcript_url
                ))
                await sent_message.edit(view=view)
        elif log_channel:
            await log_channel.send(embed=embed)

        await self.db.close_ticket(ticket_id, transcript_url)
        try:
            await channel.send("Тикет будет закрыт через несколько секунд.")
            await asyncio.sleep(5)
        except Exception:
            pass
        await channel.delete(reason=f"Тикет #{ticket['ticket_number']} закрыт администратором {closer}")

    async def _build_transcript(self, channel: discord.TextChannel) -> Tuple[Optional[Path], int]:
        messages: List[discord.Message] = []
        try:
            async for message in channel.history(limit=None, oldest_first=True):
                messages.append(message)
        except discord.Forbidden:
            logger.warning(f"Недостаточно прав для чтения истории канала {channel.id}")
        except Exception as exc:
            logger.error(f"Ошибка чтения истории канала {channel.id}: {exc}", exc_info=True)

        count = len(messages)
        if not messages:
            return None, 0

        guild = channel.guild
        guild_icon = guild.icon.url if guild.icon else ""
        transcript_name = f"{channel.name}.html"
        transcript_path = self.transcript_dir / transcript_name

        def escape(text: str) -> str:
            return (
                text.replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
            )

        lines: List[str] = []
        lines.append('<body class="tScript dark bg">')
        lines.append('    <div>')
        lines.append('        <div class="preamble">')
        lines.append('            <div class="preamble__guild-icon-container">')
        lines.append(f'                <img class="preamble__guild-icon" src="{guild_icon}" alt="Guild icon">')
        lines.append('            </div>')
        lines.append('            <div class="preamble__entries-container">')
        lines.append(f'                <div class="preamble__entry">{escape(guild.name)}</div>')
        lines.append(f'                <div class="preamble__entry">{escape(channel.name)}</div>')
        lines.append(f'                <div class="preamble__entry">{count} messages</div>')
        lines.append('            </div>')
        lines.append('        </div>')
        lines.append('        <div class="chatlog">')

        current_author_id: Optional[int] = None
        for index, message in enumerate(messages):
            avatar_url = message.author.display_avatar.url
            timestamp_str = message.created_at.strftime("%b %d, %Y %I:%M %p")
            author_name = escape(message.author.display_name)

            new_group = message.author.id != current_author_id
            current_author_id = message.author.id

            if new_group:
                lines.append('        <div class="chatlog__message-group" style="border-top:0px;">')
                lines.append('            <div class="chatlog__author-avatar-container">')
                lines.append(f'                <img class="chatlog__author-avatar" src="{avatar_url}">')
                lines.append('            </div>')
                lines.append('            <div class="chatlog__messages">')
                lines.append(f'                <span class="chatlog__author-name" data-user-id="{message.author.id}">{author_name}</span>')
                if message.author.bot:
                    lines.append('                <span class="chatlog__bot-tag">BOT</span>')
                lines.append(f'                <span class="chatlog__timestamp">{timestamp_str}</span>')

            content_parts: List[str] = []
            if message.content:
                content_parts.append(escape(message.content))
            for attachment in message.attachments:
                content_parts.append(f'<div class="attachment"><a href="{attachment.url}">{escape(attachment.filename)}</a></div>')
            if message.embeds:
                content_parts.append('<div class="chatlog__embed"><div class="chatlog__embed-description">Embed содержимое опущено</div></div>')

            content_html = "<br>".join(content_parts) if content_parts else "&nbsp;"
            lines.append(f'        <div class="chatlog__message" id="message-{message.id}" data-message-id="{message.id}">')
            lines.append('            <div class="chatlog__content">')
            lines.append(f'                <span class="markdown">{content_html}</span>')
            lines.append('            </div>')
            lines.append('        </div>')

            next_author_id = messages[index + 1].author.id if index + 1 < len(messages) else None
            if next_author_id != current_author_id:
                lines.append('            </div>')
                lines.append('        </div>')

        lines.append('        </div>')
        lines.append('    </div>')
        lines.append('</body>')

        transcript_path.write_text("\n".join(lines), encoding="utf-8")
        return transcript_path, count


class TicketPanelView(discord.ui.View):
    """Persistent view с селектором причин тикета."""

    def __init__(self, manager: TicketManager):
        super().__init__(timeout=None)
        self.manager = manager
        self.add_item(TicketReasonSelect(manager))


class TicketReasonSelect(discord.ui.Select):
    """Селектор причин тикета."""

    def __init__(self, manager: TicketManager):
        options = [
            discord.SelectOption(label=info["label"], value=key)
            for key, info in TICKET_REASONS.items()
        ]
        super().__init__(
            placeholder="Выберите причину",
            options=options,
            custom_id="ticket_reason_select"
        )
        self.manager = manager

    async def callback(self, interaction: discord.Interaction):
        reason_key = self.values[0]
        reason = TICKET_REASONS.get(reason_key)
        if not reason:
            await interaction.response.send_message("Причина не найдена.", ephemeral=True)
            return

        modal = TicketModal(self.manager, reason_key=reason_key, reason=reason)
        await interaction.response.send_modal(modal)


class TicketModal(discord.ui.Modal):
    """Модальное окно для создания тикета."""

    def __init__(self, manager: TicketManager, reason_key: str, reason: Dict[str, object]):
        super().__init__(title=str(reason.get("modal_title", "Создание тикета")), timeout=None)
        self.manager = manager
        self.reason_key = reason_key

        self.steam_id = discord.ui.TextInput(
            label="Ваш SteamID",
            placeholder="Введите ваш SteamID",
            custom_id="steam_id",
            required=True,
            max_length=64
        )
        self.add_item(self.steam_id)

        if reason.get("requires_violator", False):
            self.violator = discord.ui.TextInput(
                label="SteamID/Ник нарушителя",
                placeholder="Укажите нарушителя",
                custom_id="violator",
                required=False,
                max_length=128
            )
            self.add_item(self.violator)
        else:
            self.violator = None

        if reason.get("requires_evidence", False):
            self.evidence = discord.ui.TextInput(
                label="Доказательства (если имеются)",
                placeholder="Ссылки на скриншоты/видео",
                custom_id="evidence",
                required=False,
                max_length=512
            )
            self.add_item(self.evidence)
        else:
            self.evidence = None

        self.date = discord.ui.TextInput(
            label="Дата происходящего",
            placeholder="Укажите дату (если известна)",
            custom_id="date",
            required=False,
            max_length=64
        )
        self.add_item(self.date)

        self.details = discord.ui.TextInput(
            label="Подробно опишите суть",
            style=discord.TextStyle.long,
            placeholder="Опишите ситуацию максимально подробно",
            custom_id="details",
            required=True,
            max_length=1900
        )
        self.add_item(self.details)

    async def on_submit(self, interaction: discord.Interaction):
        if not self.manager.is_enabled():
            await interaction.response.send_message("Тикет-система отключена.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        form_data = {
            "steam_id": self.steam_id.value.strip(),
            "violator": self.violator.value.strip() if self.violator else "",
            "evidence": self.evidence.value.strip() if self.evidence else "",
            "date": self.date.value.strip(),
            "details": self.details.value.strip()
        }

        try:
            channel, ticket_number, assignee = await self.manager.create_ticket_channel(
                interaction,
                reason_key=self.reason_key,
                form_data=form_data
            )
            await send_ephemeral_with_delete(
                interaction,
                content=(
                    f"Тикет #{ticket_number:0{TICKET_SYSTEM.get('TICKET_NUMBER_PADDING', 4)}d} создан. "
                    f"Перейдите в канал {channel.mention}."
                ),
                delete_after=EPHEMERAL_DELETE_AFTER
            )
        except Exception as exc:
            logger.error(f"Ошибка создания тикета: {exc}", exc_info=True)
            await send_ephemeral_with_delete(
                interaction,
                content=f"Не удалось создать тикет: {exc}",
                delete_after=EPHEMERAL_DELETE_AFTER
            )

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        logger.error(f"Ошибка при обработке модального окна тикета: {error}", exc_info=True)
        if interaction.response.is_done():
            await interaction.followup.send("Произошла ошибка при создании тикета.", ephemeral=True)
        else:
            await interaction.response.send_message("Произошла ошибка при создании тикета.", ephemeral=True)


class TicketView(discord.ui.View):
    """Основное меню управления тикетом."""

    def __init__(self, manager: TicketManager, ticket_id: int):
        super().__init__(timeout=None)
        self.manager = manager
        self.ticket_id = ticket_id

    @discord.ui.button(label="Закрыть тикет", style=discord.ButtonStyle.danger, custom_id="ticket_close")
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("Команда доступна только на сервере.", ephemeral=True)
            return

        if not self.manager.is_staff(interaction.user):
            await interaction.response.send_message("Только администрация может закрыть тикет.", ephemeral=True)
            return

        modal = CloseTicketModal(self.manager, ticket_id=self.ticket_id)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Дополнить тикет", style=discord.ButtonStyle.primary, custom_id="ticket_append")
    async def append_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("Команда доступна только на сервере.", ephemeral=True)
            return

        modal = AppendInfoModal(self.manager, ticket_id=self.ticket_id)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Панель администратора", style=discord.ButtonStyle.success, custom_id="ticket_admin_panel")
    async def admin_panel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("Команда доступна только на сервере.", ephemeral=True)
            return

        if not self.manager.is_staff(interaction.user):
            await interaction.response.send_message("Панель доступна только администрации.", ephemeral=True)
            return

        ticket = await self.manager.db.get_ticket_by_id(self.ticket_id)
        if not ticket:
            await interaction.response.send_message("Тикет не найден.", ephemeral=True)
            return

        current_assignee_id = ticket.get('assignee_id')
        view = AdminPanelView(self.manager, ticket_id=self.ticket_id, current_assignee_id=current_assignee_id)
        description = (
            f"Текущий назначенный администратор: "
            f"<@{current_assignee_id}>" if current_assignee_id else "Назначенного администратора нет."
        )
        await interaction.response.send_message(description, view=view, ephemeral=True)


class AppendInfoModal(discord.ui.Modal):
    """Модалка для добавления дополнительной информации."""

    def __init__(self, manager: TicketManager, ticket_id: int):
        super().__init__(title="Дополнить тикет", timeout=None)
        self.manager = manager
        self.ticket_id = ticket_id

        self.details = discord.ui.TextInput(
            label="Дополнительная информация",
            style=discord.TextStyle.long,
            placeholder="Введите новую информацию или комментарии",
            required=True,
            max_length=1900
        )
        self.add_item(self.details)

    async def on_submit(self, interaction: discord.Interaction):
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("Команда доступна только на сервере.", ephemeral=True)
            return

        ticket = await self.manager.db.get_ticket_by_id(self.ticket_id)
        if not ticket:
            await interaction.response.send_message("Тикет не найден.", ephemeral=True)
            return

        channel = interaction.guild.get_channel(ticket['channel_id'])
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message("Канал тикета недоступен.", ephemeral=True)
            return

        await self.manager.append_ticket_note(self.ticket_id, interaction.user, self.details.value.strip())
        await channel.send(
            embed=discord.Embed(
                title="Дополнительная информация",
                description=self.details.value.strip(),
                color=discord.Color.blue(),
                timestamp=datetime.now(timezone.utc)
            ).set_author(name=interaction.user.display_name, icon_url=interaction.user.display_avatar.url)
        )
        await interaction.response.send_message("Информация добавлена.", ephemeral=True)

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        logger.error(f"Ошибка при дополнении тикета: {error}", exc_info=True)
        if interaction.response.is_done():
            await interaction.followup.send("Не удалось добавить информацию.", ephemeral=True)
        else:
            await interaction.response.send_message("Не удалось добавить информацию.", ephemeral=True)


class CloseTicketModal(discord.ui.Modal):
    """Модалка для закрытия тикета с комментариями."""

    def __init__(self, manager: TicketManager, ticket_id: int):
        super().__init__(title="Закрытие тикета", timeout=None)
        self.manager = manager
        self.ticket_id = ticket_id

        self.comment = discord.ui.TextInput(
            label="Комментарий (опционально)",
            style=discord.TextStyle.long,
            placeholder="Опишите результат рассмотрения тикета",
            required=False,
            max_length=1000
        )
        self.add_item(self.comment)

    async def on_submit(self, interaction: discord.Interaction):
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("Команда доступна только на сервере.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        try:
            await self.manager.close_ticket(self.ticket_id, interaction.user, self.comment.value.strip())
            await send_ephemeral_with_delete(
                interaction,
                content="Тикет закрыт и канал будет удалён.",
                delete_after=EPHEMERAL_DELETE_AFTER
            )
        except Exception as exc:
            logger.error(f"Ошибка при закрытии тикета: {exc}", exc_info=True)
            await send_ephemeral_with_delete(
                interaction,
                content=f"Не удалось закрыть тикет: {exc}",
                delete_after=EPHEMERAL_DELETE_AFTER
            )

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        logger.error(f"Ошибка модалки закрытия тикета: {error}", exc_info=True)
        if interaction.response.is_done():
            await interaction.followup.send("Не удалось закрыть тикет.", ephemeral=True)
        else:
            await interaction.response.send_message("Не удалось закрыть тикет.", ephemeral=True)


class AdminPanelView(discord.ui.View):
    """Эфемерная панель администратора."""

    def __init__(self, manager: TicketManager, ticket_id: int, current_assignee_id: Optional[int]):
        super().__init__(timeout=60)
        self.manager = manager
        self.ticket_id = ticket_id
        self.current_assignee_id = current_assignee_id

    @discord.ui.button(label="Принять тикет", style=discord.ButtonStyle.primary, custom_id="ticket_claim")
    async def claim_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("Команда доступна только на сервере.", ephemeral=True)
            return

        if not self.manager.is_staff(interaction.user):
            await interaction.response.send_message("Недостаточно прав для принятия тикета.", ephemeral=True)
            return

        ticket = await self.manager.db.get_ticket_by_id(self.ticket_id)
        if not ticket:
            await interaction.response.send_message("Тикет не найден.", ephemeral=True)
            return

        if ticket.get('assignee_id') == interaction.user.id:
            await interaction.response.send_message("Вы уже назначены на этот тикет.", ephemeral=True)
            return

        await self.manager.assign_ticket(self.ticket_id, interaction.user)

        channel = interaction.guild.get_channel(ticket['channel_id'])
        if isinstance(channel, discord.TextChannel):
            await channel.send(f"{interaction.user.mention} назначен на этот тикет.")

        await interaction.response.send_message("Вы назначены ответственным за тикет.", ephemeral=True)

    @discord.ui.button(label="Снять назначение", style=discord.ButtonStyle.secondary, custom_id="ticket_release")
    async def release_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("Команда доступна только на сервере.", ephemeral=True)
            return

        if not self.manager.is_staff(interaction.user):
            await interaction.response.send_message("Недостаточно прав для изменения назначения.", ephemeral=True)
            return

        ticket = await self.manager.db.get_ticket_by_id(self.ticket_id)
        if not ticket:
            await interaction.response.send_message("Тикет не найден.", ephemeral=True)
            return

        if not ticket.get('assignee_id'):
            await interaction.response.send_message("На тикет никто не назначен.", ephemeral=True)
            return

        await self.manager.release_ticket(self.ticket_id)

        channel = interaction.guild.get_channel(ticket['channel_id'])
        if isinstance(channel, discord.TextChannel):
            await channel.send("Назначенный администратор снят. Тикет ожидает нового назначения.")

        await interaction.response.send_message("Назначение снято.", ephemeral=True)


class TicketSystem(commands.Cog):
    """Cog тикет-системы."""

    def __init__(self, bot: commands.Bot, db: Database):
        self.bot = bot
        self.db = db
        self.manager = TicketManager(bot, db)

    async def cog_load(self):
        if not self.manager.is_enabled():
            logger.info("Тикет-система отключена в конфиге.")
            return
        await self.bot.wait_until_ready()
        await self.manager.ensure_panels_loaded()
        await self.manager.ensure_ticket_views_loaded()

    @app_commands.command(name="ticketpanel", description="Управление панелью тикетов")
    @app_commands.describe(action="publish - опубликовать, restore - восстановить сообщение, remove - удалить запись из БД")
    @app_commands.choices(action=[
        app_commands.Choice(name="publish - Опубликовать сообщение панели", value="publish"),
        app_commands.Choice(name="restore - Восстановить сообщение панели", value="restore"),
        app_commands.Choice(name="remove - Удалить запись панели", value="remove")
    ])
    async def ticketpanel(
        self,
        interaction: discord.Interaction,
        action: str
    ):
        if not interaction.guild:
            await interaction.response.send_message("Команда доступна только на сервере.", ephemeral=True)
            return

        if not self.manager.is_enabled():
            await interaction.response.send_message("Тикет-система отключена в конфигурации.", ephemeral=True)
            return

        if not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message("Недостаточно прав для управления панелью.", ephemeral=True)
            return

        action_lower = action.lower()

        try:
            if action_lower == "publish":
                await interaction.response.defer(ephemeral=True)
                message = await self.manager.publish_panel(interaction.guild)
                if message:
                    await send_ephemeral_with_delete(
                        interaction,
                        content=f"Панель тикетов опубликована в канале {message.channel.mention}.",
                        delete_after=EPHEMERAL_DELETE_AFTER
                    )
                else:
                    await send_ephemeral_with_delete(
                        interaction,
                        content="Не удалось опубликовать панель тикетов.",
                        delete_after=EPHEMERAL_DELETE_AFTER
                    )
            elif action_lower == "restore":
                await interaction.response.defer(ephemeral=True)
                message = await self.manager.restore_panel(interaction.guild)
                if message:
                    await send_ephemeral_with_delete(
                        interaction,
                        content=f"Панель тикетов восстановлена в канале {message.channel.mention}.",
                        delete_after=EPHEMERAL_DELETE_AFTER
                    )
                else:
                    await send_ephemeral_with_delete(
                        interaction,
                        content="Не удалось восстановить панель тикетов.",
                        delete_after=EPHEMERAL_DELETE_AFTER
                    )
            elif action_lower == "remove":
                await interaction.response.defer(ephemeral=True)
                await self.db.delete_ticket_panel(interaction.guild.id)
                await send_ephemeral_with_delete(
                    interaction,
                    content="Запись панели тикетов удалена из базы данных.",
                    delete_after=EPHEMERAL_DELETE_AFTER
                )
            else:
                await interaction.response.send_message("Неизвестное действие.", ephemeral=True)
        except Exception as exc:
            logger.error(f"Ошибка при управлении панелью: {exc}", exc_info=True)
            if interaction.response.is_done():
                await interaction.followup.send(f"Ошибка: {exc}", ephemeral=True)
            else:
                await interaction.response.send_message(f"Ошибка: {exc}", ephemeral=True)


async def setup(bot: commands.Bot, db: Database):
    """Регистрация тикет-системы."""
    await bot.add_cog(TicketSystem(bot, db))

