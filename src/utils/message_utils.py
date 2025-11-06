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
        # Передаем только не-None параметры
        if content is not None and embed is not None:
            message = await interaction.followup.send(
                content=content,
                embed=embed,
                ephemeral=True
            )
        elif content is not None:
            message = await interaction.followup.send(
                content=content,
                ephemeral=True
            )
        elif embed is not None:
            message = await interaction.followup.send(
                embed=embed,
                ephemeral=True
            )
        else:
            message = await interaction.followup.send(
                content="*Сообщение*",
                ephemeral=True
            )
        
        if not message:
            return None
        
        # Создаем задачу для удаления через указанное время
        # Сохраняем ID и ссылку на сообщение для использования в замыкании
        # message гарантированно не None после проверки выше
        assert message is not None, "Message should not be None here"
        message_id: int = message.id
        message_ref: discord.WebhookMessage = message
        
        async def delete_message():
            try:
                await asyncio.sleep(delete_after)
                # Пытаемся удалить через webhook (для ephemeral followup сообщений)
                try:
                    # Используем webhook для удаления ephemeral followup сообщения
                    webhook = interaction.followup  # followup это Webhook
                    if webhook:
                        await webhook.delete_message(message_id)
                except (discord.NotFound, discord.HTTPException, AttributeError):
                    # Если не получилось через webhook, пробуем через message.delete()
                    try:
                        # Используем сохраненную ссылку на сообщение
                        await message_ref.delete()  # type: ignore
                    except (discord.NotFound, discord.HTTPException):
                        # Если и это не сработало, редактируем сообщение, делая его пустым
                        try:
                            webhook = interaction.followup
                            if webhook:
                                await webhook.edit_message(
                                    message_id,
                                    content="*Сообщение удалено*",
                                    embed=None
                                )
                        except Exception:
                            # Если ничего не помогло, просто логируем
                            logger.debug(f"Не удалось удалить ephemeral сообщение {message_id}")
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
        # Передаем только не-None параметры
        if content is not None and embed is not None:
            await interaction.response.send_message(
                content=content,
                embed=embed,
                ephemeral=True
            )
        elif content is not None:
            await interaction.response.send_message(
                content=content,
                ephemeral=True
            )
        elif embed is not None:
            await interaction.response.send_message(
                embed=embed,
                ephemeral=True
            )
        else:
            await interaction.response.send_message(
                content="*Сообщение*",
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

