import discord
from discord.ext import commands
import asyncio
import logging
import socket
import struct
import os
import json
from typing import Optional
from dotenv import load_dotenv
import websockets
from rcon.source import Client

# Загрузка переменных окружения из .env файла
load_dotenv()

# Настройка логирования
logging.basicConfig(
    level=logging.DEBUG,  # Включаем DEBUG для детальной диагностики
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)
# Уменьшаем уровень логирования для discord библиотеки
logging.getLogger('discord').setLevel(logging.INFO)
logging.getLogger('websockets').setLevel(logging.INFO)

# Конфигурация RCON из .env
RCON_HOST = os.getenv('RCON_HOST', '212.232.75.180')
RCON_PORT = int(os.getenv('RCON_PORT', '27025'))
RCON_PASSWORD = os.getenv('RCON_PASSWORD', '7gj-2R4-k32-6Uk')
RCON_TIMEOUT = int(os.getenv('RCON_TIMEOUT', '10'))

# Альтернативные порты для попытки подключения
RCON_PORTS = [27025, 27023, 27015]

# Конфигурация WebRCON из .env
# WebRCON использует тот же порт, что и RCON, но через WebSocket протокол
WEBRCON_ENABLED = os.getenv('WEBRCON_ENABLED', 'false').lower() == 'true'

# Конфигурация Discord бота из .env
DISCORD_TOKEN = os.getenv('DISCORD_TOKEN', 'YOUR_DISCORD_BOT_TOKEN_HERE')

# Создание бота с префиксом команд
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents)

# Глобальная переменная для хранения сокета
rcon_socket: Optional[socket.socket] = None
rcon_port: Optional[int] = None


class RCONClient:
    """
    Простой RCON клиент для Rust сервера
    """
    SERVERDATA_AUTH = 3
    SERVERDATA_AUTH_RESPONSE = 2
    SERVERDATA_EXECCOMMAND = 2
    SERVERDATA_RESPONSE_VALUE = 0
    SERVERDATA_UNKNOWN = 4  # Rust может отправлять пакеты Type=4, их нужно игнорировать
    
    def __init__(self, host: str, port: int, password: str, timeout: int = 10):
        self.host = host
        self.port = port
        self.password = password
        self.timeout = timeout
        self.sock = None
        self.request_id = 0
        
    def connect(self) -> bool:
        """Подключение к RCON серверу"""
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            # Увеличиваем timeout для подключения
            self.sock.settimeout(self.timeout)
            self.sock.connect((self.host, self.port))
            logger.info(f"Сокет подключен к {self.host}:{self.port}")
            # Увеличиваем timeout для чтения после подключения
            self.sock.settimeout(30)  # 30 секунд для чтения ответов
            return True
        except Exception as e:
            logger.error(f"Ошибка подключения сокета: {e}")
            if self.sock:
                self.sock.close()
            self.sock = None
            return False
    
    def authenticate(self) -> bool:
        """Аутентификация на RCON сервере"""
        if not self.sock:
            return False
            
        try:
            # Сохраняем ID запроса для проверки ответа
            auth_request_id = self.request_id + 1
            
            # Отправка пакета аутентификации
            auth_packet = self._create_packet(
                self.SERVERDATA_AUTH,
                self.password.encode('utf-8')
            )
            logger.debug(f"Отправка пакета аутентификации (ID: {auth_request_id}, размер: {len(auth_packet)} байт)")
            self.sock.send(auth_packet)
            logger.debug("Пакет аутентификации отправлен, ожидание ответа...")
            
            # Увеличиваем таймаут для чтения ответа
            original_timeout = self.sock.gettimeout()
            self.sock.settimeout(15)  # 15 секунд на ответ
            
            try:
                # Rust сервер отправляет два пакета в ответ на аутентификацию
                # Первый пакет - подтверждение аутентификации
                response1 = self._read_packet()
                if not response1:
                    logger.error("Не получен первый пакет аутентификации (таймаут или пустой ответ)")
                    self.sock.settimeout(original_timeout)
                    return False
                
                logger.debug(f"Получен первый пакет: ID={response1.get('id')}, Type={response1.get('type')}")
                
                # Rust сервер отправляет два пакета: первый Type=0, второй Type=2 (AUTH_RESPONSE)
                # Также может отправлять дополнительные пакеты Type=4, которые нужно игнорировать
                # Пробуем прочитать второй пакет
                try:
                    self.sock.settimeout(2)  # Короткий таймаут для второго пакета
                    response2 = self._read_packet()
                    if response2:
                        logger.debug(f"Получен второй пакет: ID={response2.get('id')}, Type={response2.get('type')}")
                        
                        # Проверяем второй пакет - это должен быть AUTH_RESPONSE
                        if (response2['type'] == self.SERVERDATA_AUTH_RESPONSE and 
                            response2['id'] == auth_request_id):
                            logger.info("Успешная аутентификация на RCON сервере (по второму пакету)")
                            
                            # Rust может отправить дополнительные пакеты (Type=4), читаем их и игнорируем
                            try:
                                self.sock.settimeout(0.5)  # Очень короткий таймаут
                                while True:
                                    extra_packet = self._read_packet()
                                    if not extra_packet:
                                        break
                                    if extra_packet.get('type') == self.SERVERDATA_UNKNOWN:
                                        logger.debug(f"Получен дополнительный пакет Type=4, игнорируем")
                                    else:
                                        logger.debug(f"Получен дополнительный пакет Type={extra_packet.get('type')}, игнорируем")
                            except (socket.timeout, Exception):
                                pass  # Игнорируем ошибки при чтении дополнительных пакетов
                            
                            self.sock.settimeout(original_timeout)
                            return True
                except socket.timeout:
                    logger.debug("Второй пакет не получен (возможно, Rust не отправляет его)")
                except Exception as e:
                    logger.debug(f"Ошибка при чтении второго пакета: {e}")
                
                self.sock.settimeout(original_timeout)
                
                # Также проверяем первый пакет на случай если Rust отправляет только один
                if (response1['type'] == self.SERVERDATA_AUTH_RESPONSE and 
                    response1['id'] == auth_request_id):
                    logger.info("Успешная аутентификация на RCON сервере (по первому пакету)")
                    return True
                else:
                    logger.error(f"Неудачная аутентификация. Первый пакет: Тип={response1.get('type')}, ID={response1.get('id')}, ожидалось: {auth_request_id}")
                    logger.error(f"Ожидался тип {self.SERVERDATA_AUTH_RESPONSE}, получен {response1.get('type')}")
                    return False
                    
            except socket.timeout:
                logger.error("Таймаут при чтении ответа аутентификации (сервер не отвечает)")
                self.sock.settimeout(original_timeout)
                return False
                
        except socket.timeout:
            logger.error("Таймаут при отправке пакета аутентификации")
            return False
        except Exception as e:
            logger.error(f"Ошибка при аутентификации: {e}")
            return False
    
    def _create_packet(self, packet_type: int, body: bytes) -> bytes:
        """Создание RCON пакета для Rust сервера"""
        self.request_id += 1
        packet_id = self.request_id
        
        # Rust RCON формат: [SIZE(4)][ID(4)][TYPE(4)][BODY][PADDING(2)]
        # SIZE = размер данных пакета (без самого размера)
        # ID и TYPE - 4 байта каждое (little-endian)
        # BODY - данные команды/пароля
        # PADDING - два нулевых байта в конце
        
        # Создаем тело пакета (без размера)
        packet_body = struct.pack('<ii', packet_id, packet_type)
        packet_body += body
        packet_body += b'\x00\x00'  # Padding
        
        # Размер пакета (без 4 байт самого размера)
        packet_size = len(packet_body)
        
        # Полный пакет: размер + тело
        packet = struct.pack('<i', packet_size) + packet_body
        
        logger.debug(f"Создан RCON пакет: size={packet_size}, id={packet_id}, type={packet_type}, body_len={len(body)}")
        return packet
    
    def _read_packet(self) -> Optional[dict]:
        """Чтение RCON пакета"""
        try:
            # Чтение размера пакета (4 байта)
            size_data = self._recv_exact(4)
            if not size_data:
                logger.warning("Не удалось прочитать размер пакета")
                return None
            size = struct.unpack('<i', size_data)[0]
            logger.debug(f"Ожидаемый размер пакета: {size} байт")
            
            if size <= 0 or size > 4096:  # Защита от некорректных данных
                logger.error(f"Некорректный размер пакета: {size}")
                return None
            
            # Чтение данных пакета
            packet_data = self._recv_exact(size)
            if not packet_data or len(packet_data) < 8:
                logger.warning(f"Недостаточно данных в пакете: получено {len(packet_data) if packet_data else 0} байт, минимум 8")
                return None
            
            # Распаковка ID и типа
            packet_id, packet_type = struct.unpack('<ii', packet_data[:8])
            logger.debug(f"Получен пакет: ID={packet_id}, Type={packet_type}")
            
            # Тело пакета (без последних 2 байт padding)
            body = packet_data[8:-2] if len(packet_data) > 10 else packet_data[8:]
            
            return {
                'id': packet_id,
                'type': packet_type,
                'body': body
            }
        except socket.timeout:
            logger.error("Таймаут при чтении пакета (возможно, сервер не отвечает на RCON команды)")
            return None
        except Exception as e:
            logger.error(f"Ошибка при чтении пакета: {e}")
            return None
    
    def _recv_exact(self, size: int) -> Optional[bytes]:
        """Чтение точного количества байт"""
        data = b''
        while len(data) < size:
            chunk = self.sock.recv(size - len(data))
            if not chunk:
                return None
            data += chunk
        return data
    
    def send_command(self, command: str) -> Optional[str]:
        """Отправка команды на сервер"""
        if not self.sock:
            return None
        
        try:
            # Сохраняем ID команды для проверки ответа
            command_id = self.request_id + 1
            
            # Отправка команды
            command_packet = self._create_packet(
                self.SERVERDATA_EXECCOMMAND,
                command.encode('utf-8')
            )
            logger.debug(f"Отправка команды '{command}' (ID: {command_id})")
            self.sock.send(command_packet)
            
            # Rust может отправлять несколько пакетов в ответ
            # Читаем все пакеты и собираем ответ
            response_parts = []
            original_timeout = self.sock.gettimeout()
            self.sock.settimeout(10)  # 10 секунд на получение ответа
            
            try:
                # Читаем первый пакет ответа
                response = self._read_packet()
                if not response:
                    logger.warning("Не получен ответ на команду")
                    self.sock.settimeout(original_timeout)
                    return None
                
                logger.debug(f"Получен пакет ответа: ID={response.get('id')}, Type={response.get('type')}")
                
                # Проверяем, что это ответ на нашу команду
                if response['id'] == command_id and response['type'] == self.SERVERDATA_RESPONSE_VALUE:
                    # Добавляем тело ответа
                    body = response['body'].decode('utf-8', errors='ignore')
                    if body:
                        response_parts.append(body)
                
                # Пробуем прочитать дополнительные пакеты (Rust может отправлять несколько)
                try:
                    self.sock.settimeout(1)  # Короткий таймаут для дополнительных пакетов
                    while True:
                        extra_packet = self._read_packet()
                        if not extra_packet:
                            break
                        
                        # Игнорируем Type=4 и другие служебные пакеты
                        if extra_packet.get('type') == self.SERVERDATA_UNKNOWN:
                            logger.debug("Получен служебный пакет Type=4, игнорируем")
                            continue
                        
                        # Если это ответ на нашу команду, добавляем его
                        if extra_packet['id'] == command_id and extra_packet['type'] == self.SERVERDATA_RESPONSE_VALUE:
                            body = extra_packet['body'].decode('utf-8', errors='ignore')
                            if body:
                                response_parts.append(body)
                        else:
                            logger.debug(f"Получен пакет с другим ID или типом: ID={extra_packet.get('id')}, Type={extra_packet.get('type')}")
                except socket.timeout:
                    # Нет дополнительных пакетов - это нормально
                    pass
                except Exception as e:
                    logger.debug(f"Ошибка при чтении дополнительных пакетов: {e}")
                
                self.sock.settimeout(original_timeout)
                
                # Объединяем все части ответа
                if response_parts:
                    full_response = ''.join(response_parts)
                    logger.debug(f"Получен полный ответ на команду '{command}': {len(full_response)} символов")
                    return full_response
                else:
                    logger.warning(f"Получен ответ, но тело пустое")
                    return None
                    
            except socket.timeout:
                logger.error(f"Таймаут при ожидании ответа на команду '{command}'")
                self.sock.settimeout(original_timeout)
                return None
                
        except Exception as e:
            logger.error(f"Ошибка при отправке команды '{command}': {e}")
            return None
    
    def close(self):
        """Закрытие соединения"""
        if self.sock:
            self.sock.close()
            self.sock = None


class WebRCONClient:
    """
    WebRCON клиент для Rust сервера через WebSocket
    """
    def __init__(self, host: str, port: int, password: str):
        self.host = host
        self.port = port
        self.password = password
        self.websocket = None
        self.identifier = 0
        # Rust WebRCON может использовать разные форматы URI
        # Пробуем стандартный формат
        self.uri = f"ws://{host}:{port}/{password}"
        
    async def connect(self) -> bool:
        """Подключение к WebRCON серверу"""
        # Пробуем разные форматы URI для Rust WebRCON
        # Согласно rust-experimental-webrcon: https://github.com/acupofspirt/rust-experimental-webrcon
        # Формат может быть ws://host:port/password или ws://host:port/
        uri_variants = [
            f"ws://{self.host}:{self.port}/{self.password}",  # Стандартный формат с паролем в URL
            f"ws://{self.host}:{self.port}/",  # Без пароля в URI (пароль передается после подключения)
            f"ws://{self.host}:{self.port}",  # Без слеша в конце
            f"wss://{self.host}:{self.port}/{self.password}",  # WSS (если есть SSL)
        ]
        
        for uri in uri_variants:
            try:
                logger.debug(f"[DEBUG] Попытка подключения к WebRCON: {uri}")
                # Увеличиваем timeout для WebSocket подключения
                # Используем правильный синтаксис для websockets библиотеки
                try:
                    # Пробуем с extra_headers (для новых версий)
                    logger.debug(f"[DEBUG] Пробуем подключение с extra_headers")
                    self.websocket = await asyncio.wait_for(
                        websockets.connect(uri, ping_interval=None, extra_headers={
                            "User-Agent": "WebRcon"
                        }),
                        timeout=10.0
                    )
                except TypeError:
                    # Если extra_headers не поддерживается, пробуем без него
                    logger.debug("[DEBUG] extra_headers не поддерживается, пробуем без него")
                    self.websocket = await asyncio.wait_for(
                        websockets.connect(uri, ping_interval=None),
                        timeout=10.0
                    )
                logger.info(f"[DEBUG] ✓ WebSocket подключен к {self.host}:{self.port} (URI: {uri})")
                self.uri = uri  # Сохраняем рабочий URI
                return True
            except asyncio.TimeoutError:
                logger.warning(f"[DEBUG] Таймаут при подключении к {uri}")
                continue
            except Exception as e:
                error_msg = str(e)
                logger.warning(f"[DEBUG] Ошибка подключения к {uri}: {error_msg}")
                if "did not receive a valid HTTP response" in error_msg:
                    logger.debug(f"[DEBUG] Неверный HTTP ответ, пробуем следующий вариант URI")
                    continue  # Пробуем следующий вариант
                elif "Connection refused" in error_msg or "Connection closed" in error_msg:
                    logger.debug(f"[DEBUG] Соединение отклонено, пробуем следующий вариант URI")
                    continue  # Пробуем следующий вариант
                elif "extra_headers" in error_msg:
                    logger.debug(f"[DEBUG] Проблема с extra_headers, пробуем следующий вариант URI")
                    continue  # Пробуем следующий вариант (уже обработано выше)
                else:
                    # Другие ошибки - пробуем следующий вариант
                    logger.debug(f"[DEBUG] Другая ошибка, пробуем следующий вариант URI")
                    continue
        
        logger.error(f"[DEBUG] Не удалось подключиться к WebRCON ни по одному из форматов URI")
        logger.error(f"[DEBUG] Попробованы форматы: ws://host:port/password, ws://host:port/, ws://host:port, wss://host:port/password")
        self.websocket = None
        return False
    
    async def send_command(self, command: str) -> Optional[str]:
        """Отправка команды на WebRCON сервер"""
        if not self.websocket:
            return None
        
        try:
            self.identifier += 1
            # Rust WebRCON использует формат JSON с полями Identifier, Message, Name
            message = {
                "Identifier": self.identifier,
                "Message": command,
                "Name": "WebRcon"
            }
            
            logger.debug(f"[DEBUG] Отправка WebRCON команды: {json.dumps(message)}")
            await self.websocket.send(json.dumps(message))
            logger.debug(f"[DEBUG] Команда отправлена, ожидание ответа...")
            
            # Ожидание ответа с таймаутом
            try:
                response_text = await asyncio.wait_for(
                    self.websocket.recv(),
                    timeout=10.0
                )
                logger.debug(f"[DEBUG] Получен ответ WebRCON: {response_text[:200]}")
                response = json.loads(response_text)
                logger.debug(f"[DEBUG] Ответ распарсен: Identifier={response.get('Identifier')}, Type={response.get('Type')}")
                
                # Формат ответа согласно rust-experimental-webrcon:
                # {"Identifier": 0, "Message": "...", "Stacktrace": "", "Type": 3}
                # Проверяем, что это ответ на нашу команду
                if response.get("Identifier") == self.identifier:
                    return response.get("Message", "")
                else:
                    # Если Type = 3, это может быть системное сообщение
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


# Глобальные переменные для хранения подключений
webrcon_client: Optional[WebRCONClient] = None
rcon_port: Optional[int] = None


async def connect_to_rcon():
    """
    Функция для подключения к RCON серверу через WebRCON (WebSocket)
    Используется только WebRCON, без обычного RCON
    Возвращает True при успешном подключении, False при ошибке
    """
    global webrcon_client, rcon_port
    
    # Закрываем предыдущее подключение если есть
    if webrcon_client:
        await webrcon_client.close()
        webrcon_client = None
    
    # Пробуем WebRCON на всех портах
    logger.info("[DEBUG] Попытка подключения к RCON серверу через WebRCON (WebSocket)...")
    for port in RCON_PORTS:
        try:
            logger.info(f"[DEBUG] Попытка подключения к WebRCON: {RCON_HOST}:{port}")
            client = WebRCONClient(RCON_HOST, port, RCON_PASSWORD)
            
            # Подключение через WebSocket
            if not await client.connect():
                logger.warning(f"[DEBUG] Не удалось подключиться к WebRCON на {RCON_HOST}:{port}")
                continue
            
            logger.debug(f"[DEBUG] WebSocket подключен к {RCON_HOST}:{port}")
            
            # Проверка подключения командой
            response = await client.send_command("version")
            if response:
                logger.info(f"[DEBUG] ✓ Успешное подключение к WebRCON на порту {port}!")
                logger.info(f"[DEBUG] Ответ сервера на 'version': {response[:100]}")
                webrcon_client = client
                rcon_port = port
                return True
            else:
                logger.warning(f"[DEBUG] WebRCON подключен, но команда не выполнена на {port}")
                await client.close()
                
        except Exception as e:
            logger.error(f"[DEBUG] Ошибка при подключении к WebRCON {RCON_HOST}:{port}: {e}")
            continue
    
    logger.error("[DEBUG] Не удалось подключиться к WebRCON серверу")
    logger.error("[DEBUG] Убедитесь, что в Startup Command установлено: +rcon.web true")
    return False


async def send_rcon_command(command: str):
    """
    Отправка команды на RCON сервер через WebRCON (WebSocket)
    """
    global webrcon_client
    
    try:
        # Проверяем наличие подключения
        if webrcon_client is None or webrcon_client.websocket is None:
            logger.debug(f"[DEBUG] WebRCON клиент не подключен, пытаемся подключиться...")
            success = await connect_to_rcon()
            if not success:
                logger.error(f"[DEBUG] Не удалось подключиться к WebRCON серверу")
                return None
        
        if webrcon_client is None or webrcon_client.websocket is None:
            logger.error(f"[DEBUG] WebRCON клиент все еще не подключен")
            return None
        
        # Отправка команды через WebRCON
        logger.debug(f"[DEBUG] Отправка команды '{command}' на WebRCON сервер")
        response = await webrcon_client.send_command(command)
        
        if response:
            logger.debug(f"[DEBUG] Получен ответ на команду '{command}': {len(response)} символов")
        else:
            logger.warning(f"[DEBUG] Команда '{command}' не вернула ответ")
        
        return response
            
    except Exception as e:
        logger.error(f"[DEBUG] Ошибка при отправке команды '{command}': {e}")
        return None


@bot.event
async def on_ready():
    """
    Событие при запуске бота
    """
    logger.info(f'{bot.user} успешно запущен!')
    logger.info(f'Бот подключен к Discord как {bot.user.name}')
    
    # Автоматическое подключение к RCON при запуске бота
    logger.info("Попытка автоматического подключения к RCON серверу...")
    success = await connect_to_rcon()
    
    if success:
        logger.info("✓ Успешное подключение к RCON серверу!")
        
        # Устанавливаем статус бота
        await bot.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name="Rust сервер"
            ),
            status=discord.Status.online
        )
    else:
        logger.error("✗ Не удалось подключиться к RCON серверу!")
        await bot.change_presence(
            status=discord.Status.idle,
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name="RCON (не подключен)"
            )
        )


@bot.command(name='rcon_test')
async def rcon_test(ctx):
    """
    Тестовая команда для проверки RCON подключения
    """
    logger.info("Запрос на тестирование RCON подключения")
    
    # Попытка подключения
    success = await connect_to_rcon()
    
    if success:
        # Попытка отправить команду
        response = await send_rcon_command("version")
        
        if response:
            await ctx.send(f"✅ RCON подключение работает!\n**Ответ сервера:**\n```{response}```")
        else:
            await ctx.send("⚠️ Подключение установлено, но команда не выполнена")
    else:
        await ctx.send("❌ Не удалось подключиться к RCON серверу. Проверьте логи.")


@bot.command(name='rcon')
async def rcon_command(ctx, *, command: str):
    """
    Команда для отправки RCON команд на сервер
    Использование: !rcon <команда>
    """
    logger.info(f"Запрос на выполнение RCON команды: {command}")
    
    response = await send_rcon_command(command)
    
    if response:
        await ctx.send(f"**Результат команды `{command}`:**\n```{response}```")
    else:
        await ctx.send(f"❌ Ошибка при выполнении команды `{command}`")


@bot.command(name='rcon_reconnect')
async def rcon_reconnect(ctx):
    """
    Команда для переподключения к RCON серверу
    """
    global webrcon_client, rcon_port
    
    logger.info("[DEBUG] Запрос на переподключение к WebRCON")
    
    await ctx.send("🔄 [DEBUG] Попытка переподключения к WebRCON серверу...")
    
    # Закрываем текущее подключение
    if webrcon_client:
        await webrcon_client.close()
        webrcon_client = None
    
    success = await connect_to_rcon()
    
    if success:
        # Проверяем подключение командой
        response = await send_rcon_command("version")
        if response:
            await ctx.send(
                f"✅ **WebRCON переподключен успешно!**\n"
                f"**Хост:** {RCON_HOST}\n"
                f"**Порт:** {rcon_port or 'неизвестен'}\n"
                f"**Статус:** Работает\n\n"
                f"🔍 [DEBUG] Подключение установлено через WebRCON (WebSocket)"
            )
        else:
            await ctx.send(
                f"⚠️ **WebRCON подключение установлено, но команды не выполняются**\n"
                f"**Хост:** {RCON_HOST}\n"
                f"**Порт:** {rcon_port or 'неизвестен'}\n"
                f"**Статус:** Проблемы с выполнением команд\n\n"
                f"🔍 [DEBUG] Проверьте логи для деталей"
            )
    else:
        await ctx.send(
            f"❌ **WebRCON не подключен**\n"
            f"**Хост:** {RCON_HOST}\n"
            f"**Порты для попытки:** {', '.join(map(str, RCON_PORTS))}\n"
            f"**Статус:** Отключен\n\n"
            f"🔍 [DEBUG] Используется только WebRCON (WebSocket)\n"
            f"🔍 [DEBUG] Убедитесь, что в Startup Command установлено: +rcon.web true\n"
            f"Проверьте логи для деталей."
        )


@bot.command(name='rcon_status')
async def rcon_status(ctx):
    """
    Команда для проверки статуса RCON подключения
    """
    global webrcon_client, rcon_port
    
    if webrcon_client and webrcon_client.websocket:
        # Проверяем подключение отправкой тестовой команды
        response = await send_rcon_command("version")
        if response:
            await ctx.send(
                f"✅ **WebRCON подключен**\n"
                f"**Хост:** {RCON_HOST}\n"
                f"**Порт:** {rcon_port}\n"
                f"**Статус:** Активно\n\n"
                f"🔍 [DEBUG] Подключение установлено через WebRCON (WebSocket)"
            )
        else:
            await ctx.send(
                f"⚠️ **WebRCON подключение установлено, но команды не выполняются**\n"
                f"**Хост:** {RCON_HOST}\n"
                f"**Порт:** {rcon_port or 'неизвестен'}\n"
                f"**Статус:** Проблемы с выполнением команд\n\n"
                f"🔍 [DEBUG] Проверьте логи для деталей"
            )
    else:
        await ctx.send(
            f"❌ **WebRCON не подключен**\n"
            f"**Хост:** {RCON_HOST}\n"
            f"**Порты для попытки:** {', '.join(map(str, RCON_PORTS))}\n"
            f"**Статус:** Отключен\n\n"
            f"🔍 [DEBUG] Используется только WebRCON (WebSocket)\n"
            f"🔍 [DEBUG] Убедитесь, что в Startup Command установлено: +rcon.web true\n"
            f"Используйте `!rcon_reconnect` для переподключения."
        )


if __name__ == "__main__":
    if DISCORD_TOKEN == "YOUR_DISCORD_BOT_TOKEN_HERE" or not DISCORD_TOKEN:
        logger.error("Пожалуйста, установите DISCORD_TOKEN в файле .env")
        logger.error("Создайте файл .env на основе .env.example и заполните ваш токен Discord бота")
    else:
        bot.run(DISCORD_TOKEN)

