"""
Утилиты для работы с сообщениями Discord
"""
import asyncio
import logging
from typing import Optional

import discord

logger = logging.getLogger(__name__)


async def send_ephemeral_with_delete(
    interaction: discord.Interaction,
    content: Optional[str] = None,
    embed: Optional[discord.Embed] = None,
    delete_after: float = 10.0
) -> Optional[discord.WebhookMessage]:
    """
    Отправляет ephemeral сообщение и автоматически удаляет его через указанное время
    
    Args:
        interaction: Discord interaction объект
        content: Текст сообщения (опционально)
        embed: Embed сообщения (опционально)
        delete_after: Время в секундах до удаления (по умолчанию 10 секунд)
    
    Returns:
        WebhookMessage объект или None
    """
    try:
        # Отправляем ephemeral сообщение
        message = await interaction.followup.send(
            content=content,
            embed=embed,
            ephemeral=True
        )
        
        # Создаем задачу для удаления через указанное время
        async def delete_message():
            try:
                await asyncio.sleep(delete_after)
                await message.delete()
            except discord.NotFound:
                # Сообщение уже удалено
                pass
            except discord.HTTPException as e:
                logger.warning(f"Не удалось удалить ephemeral сообщение: {e}")
            except Exception as e:
                logger.error(f"Ошибка при удалении ephemeral сообщения: {e}")
        
        # Запускаем задачу в фоне
        asyncio.create_task(delete_message())
        
        return message
    except Exception as e:
        logger.error(f"Ошибка при отправке ephemeral сообщения: {e}")
        return None


async def send_response_with_delete(
    interaction: discord.Interaction,
    content: Optional[str] = None,
    embed: Optional[discord.Embed] = None,
    delete_after: float = 10.0
) -> Optional[discord.InteractionResponse]:
    """
    Отправляет initial response как ephemeral и автоматически удаляет его через указанное время
    
    Args:
        interaction: Discord interaction объект
        content: Текст сообщения (опционально)
        embed: Embed сообщения (опционально)
        delete_after: Время в секундах до удаления (по умолчанию 10 секунд)
    
    Returns:
        InteractionResponse объект или None
    """
    try:
        # Отправляем ephemeral сообщение
        await interaction.response.send_message(
            content=content,
            embed=embed,
            ephemeral=True
        )
        
        # Для initial response нужно получить сообщение через followup
        # Но initial response нельзя удалить напрямую, поэтому используем другой подход
        # Создаем задачу, которая удалит followup сообщение (если оно будет отправлено)
        async def delete_after_delay():
            try:
                await asyncio.sleep(delete_after)
                # Пытаемся удалить original response через followup
                try:
                    await interaction.delete_original_response()
                except discord.NotFound:
                    # Сообщение уже удалено или не существует
                    pass
                except discord.HTTPException as e:
                    logger.warning(f"Не удалось удалить original response: {e}")
            except Exception as e:
                logger.error(f"Ошибка при удалении original response: {e}")
        
        # Запускаем задачу в фоне
        asyncio.create_task(delete_after_delay())
        
        return interaction.response
    except Exception as e:
        logger.error(f"Ошибка при отправке response сообщения: {e}")
        return None

