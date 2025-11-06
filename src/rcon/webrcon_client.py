"""
WebRCON клиент для Rust сервера через WebSocket
Перенесено и адаптировано из legacy/bot.py с поддержкой нескольких портов
"""
import asyncio
import json
import logging
from typing import Optional, List, Callable, Awaitable

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
        
        # Прослушивание консоли
        self._console_listener_task: Optional[asyncio.Task] = None
        self._console_callback: Optional[Callable[[str], Awaitable[None]]] = None
        self._listening = False
        
        # Хранилище для ответов на команды (Identifier -> Future)
        self._pending_responses: dict[int, asyncio.Future] = {}
        
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
                
                logger.info(f"WebSocket подключен к {self.host}:{port} (URI: {uri})")
                self.uri = uri
                
                # Автоматически запускаем listener после подключения
                await self.start_console_listener()
                
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
            cmd_id = self.identifier
            message = {
                "Identifier": cmd_id,
                "Message": command,
                "Name": "WebRcon"
            }
            
            # Создаем Future для ожидания ответа
            future = asyncio.Future()
            self._pending_responses[cmd_id] = future
            
            logger.debug(f"Отправка WebRCON команды: {json.dumps(message)}")
            await self.websocket.send(json.dumps(message))
            logger.debug(f"Команда отправлена (Identifier={cmd_id}), ожидание ответа...")
            
            try:
                # Ждем ответ через Future (который заполнит listener)
                response = await asyncio.wait_for(future, timeout=timeout)
                logger.debug(f"Получен ответ на команду {cmd_id}: {len(str(response))} символов")
                return response
            except asyncio.TimeoutError:
                logger.error(f"Таймаут при ожидании ответа WebRCON (Identifier={cmd_id})")
                # Удаляем Future из словаря
                self._pending_responses.pop(cmd_id, None)
                return None
            finally:
                # Очищаем Future из словаря (если еще там)
                self._pending_responses.pop(cmd_id, None)
                
        except Exception as e:
            logger.error(f"Ошибка при отправке команды WebRCON: {e}")
            return None
    
    def set_console_callback(self, callback: Optional[Callable[[str], Awaitable[None]]]):
        """
        Установка callback для обработки сообщений консоли
        
        Args:
            callback: Асинхронная функция, которая будет вызываться при получении сообщения консоли
        """
        self._console_callback = callback
    
    async def start_console_listener(self):
        """Запуск фонового прослушивания консоли"""
        if self._listening or not self.websocket:
            return
        
        self._listening = True
        self._console_listener_task = asyncio.create_task(self._console_listener_loop())
        logger.info("✓ Прослушивание консоли запущено")
    
    async def stop_console_listener(self):
        """Остановка прослушивания консоли"""
        self._listening = False
        if self._console_listener_task:
            self._console_listener_task.cancel()
            try:
                await self._console_listener_task
            except asyncio.CancelledError:
                pass
            self._console_listener_task = None
        logger.info("Прослушивание консоли остановлено")
    
    async def _console_listener_loop(self):
        """Фоновый цикл прослушивания консоли - ЕДИНСТВЕННОЕ место чтения из WebSocket"""
        # ВАЖНО: Этот цикл читает ВСЕ сообщения из WebSocket и раздает их:
        # - Type=3 (системные/логи) -> callback консоли
        # - Type=0 с Identifier -> Future для send_command
        
        while self._listening and self.websocket:
            try:
                # Получаем сообщение с таймаутом
                try:
                    response_text = await asyncio.wait_for(
                        self.websocket.recv(),
                        timeout=1.0
                    )
                except asyncio.TimeoutError:
                    continue  # Просто продолжаем слушать
                
                try:
                    response = json.loads(response_text)
                    msg_type = response.get("Type")
                    identifier = response.get("Identifier")
                    message = response.get("Message", "")
                    
                    # Type=3 - это системные сообщения/логи консоли (не ответы на команды)
                    if msg_type == 3:
                        # Это сообщение консоли/плагина, отправляем в callback
                        if self._console_callback and message:
                            try:
                                await self._console_callback(message)
                            except Exception as e:
                                logger.error(f"Ошибка в callback обработки консоли: {e}")
                    
                    # Type=0 (или отсутствует Type) с Identifier - это ответы на команды
                    elif identifier and identifier in self._pending_responses:
                        future = self._pending_responses.pop(identifier)
                        if not future.done():
                            future.set_result(message)
                            logger.debug(f"Ответ на команду {identifier} передан в Future")
                    
                except json.JSONDecodeError as e:
                    logger.debug(f"Ошибка парсинга JSON сообщения: {e}")
                except Exception as e:
                    logger.error(f"Ошибка обработки сообщения консоли: {e}")
                    
            except websockets.exceptions.ConnectionClosed:
                logger.warning("WebSocket соединение закрыто, останавливаем прослушивание")
                # Отменяем все ожидающие Future
                for future in self._pending_responses.values():
                    if not future.done():
                        future.cancel()
                self._pending_responses.clear()
                break
            except Exception as e:
                logger.error(f"Ошибка в цикле прослушивания консоли: {e}")
                await asyncio.sleep(1)  # Небольшая задержка перед повтором
    
    async def close(self):
        """Закрытие соединения"""
        await self.stop_console_listener()
        if self.websocket:
            await self.websocket.close()
            self.websocket = None

