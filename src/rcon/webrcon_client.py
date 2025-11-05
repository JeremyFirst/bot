"""
WebRCON клиент для Rust сервера через WebSocket
Перенесено и адаптировано из legacy/bot.py с поддержкой нескольких портов
"""
import asyncio
import json
import logging
from typing import Optional, List

import websockets

logger = logging.getLogger(__name__)


class WebRCONClient:
    """
    WebRCON клиент для Rust сервера через WebSocket
    Поддерживает автоматическое подключение к нескольким портам
    """
    def __init__(self, host: str, port: int, password: str, try_ports: bool = True):
        self.host = host
        self.port = port
        self.password = password
        self.websocket = None
        self.identifier = 0
        self.uri = None
        self.connected_port = None
        
        # Список портов для попыток подключения (если try_ports=True)
        if try_ports:
            self.ports_to_try = [port, port - 2, port + 2, port - 10, port + 10]
            self.ports_to_try = [p for p in self.ports_to_try if 0 < p < 65536]
            self.ports_to_try = list(dict.fromkeys(self.ports_to_try))  # Удаляем дубликаты
        else:
            self.ports_to_try = [port]
        
    async def connect(self, test_command: str = "version") -> bool:
        """
        Подключение к WebRCON серверу с автоматическим перебором портов
        
        Args:
            test_command: Команда для проверки подключения после установки соединения
            
        Returns:
            True если подключение успешно установлено и проверено
        """
        # Пробуем подключиться на разных портах
        for port in self.ports_to_try:
            if await self._connect_to_port(port, test_command):
                self.connected_port = port
                return True
        
        logger.error(f"Не удалось подключиться к WebRCON на хосте {self.host} ни на одном из портов: {self.ports_to_try}")
        return False
    
    async def _connect_to_port(self, port: int, test_command: Optional[str] = None) -> bool:
        """
        Попытка подключения к конкретному порту
        
        Args:
            port: Порт для подключения
            test_command: Команда для проверки подключения
            
        Returns:
            True если подключение успешно и проверено
        """
        uri_variants = [
            f"ws://{self.host}:{port}/{self.password}",
            f"ws://{self.host}:{port}/",
            f"ws://{self.host}:{port}",
            f"wss://{self.host}:{port}/{self.password}",
        ]
        
        for uri in uri_variants:
            try:
                logger.debug(f"Попытка подключения к WebRCON: {uri}")
                try:
                    self.websocket = await asyncio.wait_for(
                        websockets.connect(uri, ping_interval=None, extra_headers={
                            "User-Agent": "WebRcon"
                        }),
                        timeout=10.0
                    )
                except TypeError:
                    self.websocket = await asyncio.wait_for(
                        websockets.connect(uri, ping_interval=None),
                        timeout=10.0
                    )
                
                logger.info(f"✓ WebSocket подключен к {self.host}:{port} (URI: {uri})")
                self.uri = uri
                
                # Проверяем подключение командой
                if test_command:
                    response = await self.send_command(test_command, timeout=5.0)
                    if response:
                        logger.info(f"✓ Подключение проверено, ответ на '{test_command}': {response[:100]}")
                        return True
                    else:
                        logger.warning(f"WebSocket подключен, но команда '{test_command}' не вернула ответ")
                        await self.close()
                        continue
                
                return True
                
            except asyncio.TimeoutError:
                logger.warning(f"Таймаут при подключении к {uri}")
                continue
            except Exception as e:
                error_msg = str(e)
                logger.debug(f"Ошибка подключения к {uri}: {error_msg}")
                continue
        
        return False
    
    async def send_command(self, command: str, timeout: float = 10.0) -> Optional[str]:
        """
        Отправка команды на WebRCON сервер
        
        Args:
            command: Команда для отправки
            timeout: Таймаут ожидания ответа в секундах
            
        Returns:
            Ответ сервера или None при ошибке
        """
        if not self.websocket:
            return None
        
        try:
            self.identifier += 1
            message = {
                "Identifier": self.identifier,
                "Message": command,
                "Name": "WebRcon"
            }
            
            logger.debug(f"Отправка WebRCON команды: {json.dumps(message)}")
            await self.websocket.send(json.dumps(message))
            logger.debug("Команда отправлена, ожидание ответа...")
            
            try:
                response_text = await asyncio.wait_for(
                    self.websocket.recv(),
                    timeout=timeout
                )
                logger.debug(f"Получен ответ WebRCON: {response_text[:200]}")
                response = json.loads(response_text)
                logger.debug(f"Ответ распарсен: Identifier={response.get('Identifier')}, Type={response.get('Type')}")
                
                if response.get("Identifier") == self.identifier:
                    return response.get("Message", "")
                else:
                    msg_type = response.get("Type")
                    resp_id = response.get("Identifier")
                    logger.debug(f"Получен ответ с Identifier={resp_id}, Type={msg_type}, ожидалось Identifier={self.identifier}")
                    if msg_type == 3:
                        logger.debug("Получено системное сообщение (Type=3), возможно это не ответ на команду")
                    return None
            except asyncio.TimeoutError:
                logger.error("Таймаут при ожидании ответа WebRCON")
                return None
                
        except json.JSONDecodeError as e:
            logger.error(f"Ошибка парсинга JSON ответа WebRCON: {e}")
            return None
        except Exception as e:
            logger.error(f"Ошибка при отправке команды WebRCON: {e}")
            return None
    
    async def close(self):
        """Закрытие соединения"""
        if self.websocket:
            await self.websocket.close()
            self.websocket = None

