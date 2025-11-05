"""
Менеджер для управления WebRCON подключением
Обеспечивает автоматическое переподключение и проверку соединения
"""
import asyncio
import logging
from typing import Optional

from config.config import RCON_HOST, RCON_PORT, RCON_PASS
from src.rcon.webrcon_client import WebRCONClient

logger = logging.getLogger(__name__)


class RCONManager:
    """Менеджер для управления WebRCON подключением"""
    
    def __init__(self):
        self.client: Optional[WebRCONClient] = None
        self.host = RCON_HOST
        self.port = RCON_PORT
        self.password = RCON_PASS
        self.connected_port: Optional[int] = None
    
    async def connect(self) -> bool:
        """Подключение к RCON серверу"""
        # Закрываем предыдущее подключение если есть
        if self.client:
            await self.client.close()
            self.client = None
        
        logger.info(f"Попытка подключения к RCON серверу через WebRCON (WebSocket)...")
        
        self.client = WebRCONClient(self.host, self.port, self.password, try_ports=True)
        
        success = await self.client.connect(test_command="version")
        
        if success:
            self.connected_port = self.client.connected_port
            logger.info(f"✓ Успешное подключение к WebRCON на порту {self.connected_port}!")
        else:
            logger.error("✗ Не удалось подключиться к WebRCON серверу")
            logger.error("Убедитесь, что в Startup Command установлено: +rcon.web true")
            self.client = None
        
        return success
    
    async def send_command(self, command: str, timeout: float = 10.0) -> Optional[str]:
        """
        Отправка команды на RCON сервер с автоматическим переподключением
        
        Args:
            command: Команда для отправки
            timeout: Таймаут ожидания ответа
            
        Returns:
            Ответ сервера или None при ошибке
        """
        # Проверяем наличие подключения
        if self.client is None or self.client.websocket is None:
            logger.debug("WebRCON клиент не подключен, пытаемся подключиться...")
            success = await self.connect()
            if not success:
                logger.error("Не удалось подключиться к WebRCON серверу")
                return None
        
        if self.client is None:
            logger.error("WebRCON клиент все еще не подключен")
            return None
        
        # Отправка команды
        logger.debug(f"Отправка команды '{command}' на WebRCON сервер")
        response = await self.client.send_command(command, timeout=timeout)
        
        if response:
            logger.debug(f"Получен ответ на команду '{command}': {len(response)} символов")
        else:
            logger.warning(f"Команда '{command}' не вернула ответ, пробуем переподключиться...")
            # Пробуем переподключиться и повторить команду один раз
            success = await self.connect()
            if success:
                response = await self.client.send_command(command, timeout=timeout)
        
        return response
    
    async def close(self):
        """Закрытие соединения"""
        if self.client:
            await self.client.close()
            self.client = None
            self.connected_port = None
    
    def is_connected(self) -> bool:
        """Проверка, подключен ли клиент"""
        return self.client is not None and self.client.websocket is not None
    
    def set_console_callback(self, callback):
        """
        Установка callback для обработки сообщений консоли
        
        Args:
            callback: Асинхронная функция для обработки сообщений консоли
        """
        if self.client:
            self.client.set_console_callback(callback)

