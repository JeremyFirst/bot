"""
Шаблоны Embed-сообщений для Discord
"""
import discord
from datetime import datetime
from typing import Optional

from src.utils.time_utils import format_datetime_msk


def create_privilege_added_embed(
    discord_user: discord.Member,
    steamid: str,
    group_name: str,
    expires_at: Optional[datetime],
    executor: discord.Member,
    permanent: bool = False
) -> discord.Embed:
    """Embed для успешного добавления привилегии"""
    embed = discord.Embed(
        title="Привилегия выдана",
        color=discord.Color.green(),
        timestamp=datetime.utcnow()
    )
    
    embed.add_field(
        name="Discord пользователь",
        value=f"{discord_user.mention}\nID: `{discord_user.id}`",
        inline=False
    )
    
    embed.add_field(name="SteamID", value=f"`{steamid}`", inline=True)
    embed.add_field(name="Группа", value=f"`{group_name}`", inline=True)
    
    if permanent:
        embed.add_field(name="Действует до", value="**Перманентно**", inline=False)
    elif expires_at:
        embed.add_field(
            name="Действует до",
            value=f"**{format_datetime_msk(expires_at)}**",
            inline=False
        )
    else:
        embed.add_field(name="Действует до", value="Не указано", inline=False)
    
    embed.add_field(
        name="Кто выдал",
        value=f"{executor.mention} ({executor.display_name})",
        inline=False
    )
    
    embed.set_thumbnail(url=discord_user.display_avatar.url)
    
    return embed


def create_no_privileges_embed(
    steamid: str,
    raw_response: str
) -> discord.Embed:
    """Embed для случая, когда у пользователя нет привилегий"""
    embed = discord.Embed(
        title="У пользователя нет привилегий",
        color=discord.Color.red(),
        timestamp=datetime.utcnow()
    )
    
    embed.add_field(name="SteamID", value=f"`{steamid}`", inline=False)
    
    # Показываем короткий ответ RCON
    short_response = raw_response[:500] if raw_response else "Нет ответа от сервера"
    embed.add_field(
        name="Ответ сервера",
        value=f"```{short_response}```",
        inline=False
    )
    
    return embed


def create_warning_embed(
    warned_user: discord.Member,
    executor: discord.Member,
    reason: str,
    warnings_count: int,
    limit: int,
    category: str = "Наборная"
) -> discord.Embed:
    """Embed для выговора"""
    embed = discord.Embed(
        title="⚠️ Выговор",
        color=discord.Color.orange(),
        timestamp=datetime.utcnow()
    )
    
    embed.add_field(
        name="Кому",
        value=f"{warned_user.mention}\nID: `{warned_user.id}`",
        inline=False
    )
    
    embed.add_field(name="Причина", value=reason or "Не указана", inline=False)
    
    embed.add_field(
        name="Кто выдал",
        value=f"{executor.mention} ({executor.display_name})",
        inline=True
    )
    
    embed.add_field(
        name="Категория",
        value=category,
        inline=True
    )
    
    embed.add_field(
        name="Текущее количество выговоров",
        value=f"**{warnings_count}/{limit}**",
        inline=False
    )
    
    if warnings_count >= limit:
        embed.add_field(
            name="ВНИМАНИЕ",
            value=f"Достигнут лимит выговоров! Права будут сняты.",
            inline=False
        )
        embed.color = discord.Color.red()
    
    embed.set_thumbnail(url=warned_user.display_avatar.url)
    
    return embed


def create_privilege_removed_embed(
    discord_user: discord.abc.User,
    reason: str,
    purchase_link: str
) -> discord.Embed:
    """Embed для уведомления о снятии прав"""
    embed = discord.Embed(
        title="Привилегии сняты",
        color=discord.Color.red(),
        timestamp=datetime.utcnow()
    )
    
    embed.add_field(
        name="Пользователь",
        value=f"{discord_user.mention}\nID: `{discord_user.id}`",
        inline=False
    )
    
    embed.add_field(name="Причина", value=reason, inline=False)
    
    embed.add_field(
        name="Действия",
        value=f"Если вы считаете, что это ошибка, обратитесь к администрации.\n"
              f"[Приобрести привилегии]({purchase_link})",
        inline=False
    )
    
    embed.set_thumbnail(url=discord_user.display_avatar.url)
    
    return embed


def create_admin_list_embed(
    admin_categories: dict,
    guild: discord.Guild
) -> discord.Embed:
    """
    Embed для списка администрации (широкий формат)
    
    Args:
        admin_categories: Словарь {category_name: {role_name: [discord.Member]}}
        guild: Discord сервер
    """
    embed = discord.Embed(
        title="📋 Состав Администрации",
        color=discord.Color.blue(),
        timestamp=datetime.utcnow()
    )
    
    # Проходим по категориям
    for category_name, roles_dict in admin_categories.items():
        # Формируем значение для поля
        value_parts = []
        
        if not roles_dict:
            # Нет ролей в категории - ищем роли по названию категории
            category_roles = [role for role in guild.roles if category_name.lower() in role.name.lower() or role.name.lower() in category_name.lower()]
            category_roles = [r for r in category_roles if r.name != "@everyone"]
            
            if category_roles:
                # Сортируем роли по позиции (от высшей к низшей)
                category_roles.sort(key=lambda r: r.position, reverse=True)
                
                for role in category_roles:
                    role_members = [m for m in guild.members if role in m.roles]
                    if role_members:
                        mentions = " ".join([m.mention for m in role_members])
                        value_parts.append(f"{role.mention} {mentions}")
                    else:
                        value_parts.append(f"{role.mention} (нет пользователей)")
            else:
                value_parts.append("(нет пользователей)")
        else:
            # Есть роли в категории - сортируем по позиции роли
            def get_role_position(role_name):
                role_obj = discord.utils.get(guild.roles, name=role_name)
                return role_obj.position if role_obj else 0
            
            sorted_roles = sorted(
                roles_dict.items(),
                key=lambda x: get_role_position(x[0]),
                reverse=True
            )
            
            for role_name, role_members in sorted_roles:
                role_obj = discord.utils.get(guild.roles, name=role_name)
                if role_obj:
                    if role_members:
                        mentions = " ".join([m.mention for m in role_members])
                        value_parts.append(f"{role_obj.mention} {mentions}")
                    else:
                        value_parts.append(f"{role_obj.mention} (нет пользователей)")
                else:
                    # Если роль не найдена, просто упоминания
                    if role_members:
                        mentions = " ".join([m.mention for m in role_members])
                        value_parts.append(f"@{role_name} {mentions}")
                    else:
                        value_parts.append(f"@{role_name} (нет пользователей)")
        
        value = "\n".join(value_parts) if value_parts else "(нет пользователей)"
        embed.add_field(name=category_name, value=value, inline=False)
    
    embed.set_footer(text=datetime.utcnow().strftime("%d.%m.%Y %H:%M"))
    
    return embed


def create_error_embed(
    title: str,
    description: str
) -> discord.Embed:
    """Embed для ошибок"""
    embed = discord.Embed(
        title=f"{title}",
        description=description,
        color=discord.Color.red(),
        timestamp=datetime.utcnow()
    )
    return embed


def create_success_embed(
    title: str,
    description: str
) -> discord.Embed:
    """Embed для успешных операций"""
    embed = discord.Embed(
        title=f"{title}",
        description=description,
        color=discord.Color.green(),
        timestamp=datetime.utcnow()
    )
    return embed

