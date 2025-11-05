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
            # Отправка команды
            command_packet = self._create_packet(
                self.SERVERDATA_EXECCOMMAND,
                command.encode('utf-8')
            )
            self.sock.send(command_packet)
            
            # Получение ответа
            response = self._read_packet()
            
            if response and response['type'] == self.SERVERDATA_RESPONSE_VALUE:
                return response['body'].decode('utf-8', errors='ignore')
            
            return None
        except Exception as e:
            logger.error(f"Ошибка при отправке команды: {e}")
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
                logger.info(f"Попытка подключения к WebRCON: {uri}")
                # Увеличиваем timeout для WebSocket подключения
                # Используем правильный синтаксис для websockets библиотеки
                try:
                    # Пробуем с extra_headers (для новых версий)
                    self.websocket = await asyncio.wait_for(
                        websockets.connect(uri, ping_interval=None, extra_headers={
                            "User-Agent": "WebRcon"
                        }),
                        timeout=10.0
                    )
                except TypeError:
                    # Если extra_headers не поддерживается, пробуем без него
                    logger.debug("extra_headers не поддерживается, пробуем без него")
                    self.websocket = await asyncio.wait_for(
                        websockets.connect(uri, ping_interval=None),
                        timeout=10.0
                    )
                logger.info(f"WebSocket подключен к {self.host}:{self.port} (URI: {uri})")
                self.uri = uri  # Сохраняем рабочий URI
                return True
            except asyncio.TimeoutError:
                logger.warning(f"Таймаут при подключении к {uri}")
                continue
            except Exception as e:
                error_msg = str(e)
                logger.warning(f"Ошибка подключения к {uri}: {error_msg}")
                if "did not receive a valid HTTP response" in error_msg:
                    continue  # Пробуем следующий вариант
                elif "Connection refused" in error_msg or "Connection closed" in error_msg:
                    continue  # Пробуем следующий вариант
                elif "extra_headers" in error_msg:
                    continue  # Пробуем следующий вариант (уже обработано выше)
                else:
                    # Другие ошибки - пробуем следующий вариант
                    continue
        
        logger.error(f"Не удалось подключиться к WebRCON ни по одному из форматов URI")
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
            
            logger.debug(f"Отправка WebRCON команды: {json.dumps(message)}")
            await self.websocket.send(json.dumps(message))
            
            # Ожидание ответа с таймаутом
            try:
                response_text = await asyncio.wait_for(
                    self.websocket.recv(),
                    timeout=10.0
                )
                logger.debug(f"Получен ответ WebRCON: {response_text[:200]}")
                response = json.loads(response_text)
                
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
rcon_client: Optional[RCONClient] = None
rcon_library_client: Optional[Client] = None  # Клиент из библиотеки python-rcon
webrcon_client: Optional[WebRCONClient] = None
use_webrcon = False
use_rcon_library = False  # Флаг использования библиотеки python-rcon
rcon_port: Optional[int] = None


async def connect_to_rcon():
    """
    Функция для подключения к RCON серверу
    Пробует библиотеку python-rcon, затем WebRCON если включен, затем обычный RCON
    Возвращает True при успешном подключении, False при ошибке
    """
    global rcon_client, rcon_library_client, webrcon_client, use_webrcon, use_rcon_library, rcon_port
    
    # Закрываем предыдущие подключения если есть
    if rcon_client:
        rcon_client.close()
        rcon_client = None
    if rcon_library_client:
        try:
            rcon_library_client.close()
        except:
            pass
        rcon_library_client = None
    if webrcon_client:
        await webrcon_client.close()
        webrcon_client = None
    
    # Сначала пробуем библиотеку python-rcon (она лучше работает с Rust)
    logger.info("Попытка подключения через библиотеку python-rcon...")
    for port in RCON_PORTS:
        try:
            logger.info(f"Попытка подключения через python-rcon к {RCON_HOST}:{port}")
            loop = asyncio.get_event_loop()
            
            # Создаем клиент через executor (синхронная операция)
            # Библиотека rcon использует контекстный менеджер, но мы можем использовать клиент напрямую
            def create_and_test():
                try:
                    # Создаем клиент и сразу тестируем
                    with Client(RCON_HOST, port, passwd=RCON_PASSWORD) as client:
                        return client.run("version")
                except Exception as e:
                    logger.debug(f"Ошибка при создании/тестировании клиента rcon: {e}")
                    return None
            
            response = await loop.run_in_executor(None, create_and_test)
            
            if response:
                logger.info(f"✓ Успешное подключение через python-rcon на порту {port}!")
                logger.info(f"Ответ сервера на 'version': {response[:100] if response else 'пустой ответ'}")
                # Библиотека rcon использует контекстный менеджер, поэтому создаем клиент заново для постоянного использования
                # Но для постоянного использования нужно использовать другой подход
                # Пока оставляем самописную реализацию как основную, библиотеку используем только для проверки
                logger.info("Библиотека rcon работает, но используем самописную реализацию для постоянного подключения")
                # Пробуем подключиться самописным клиентом на этом порту
                client = RCONClient(RCON_HOST, port, RCON_PASSWORD, RCON_TIMEOUT)
                if client.connect() and client.authenticate():
                    test_response = client.send_command("version")
                    if test_response:
                        rcon_client = client
                        use_rcon_library = False
                        use_webrcon = False
                        rcon_port = port
                        return True
                    else:
                        client.close()
                else:
                    if client.sock:
                        client.close()
        except Exception as e:
            logger.warning(f"Не удалось подключиться через python-rcon к {RCON_HOST}:{port}: {e}")
            continue
    
    # Если WebRCON принудительно включен, пробуем его первым
    # WebRCON использует тот же порт, что и RCON, но через WebSocket протокол
    if WEBRCON_ENABLED:
        logger.info(f"WebRCON принудительно включен, пробуем подключение через WebSocket на порту {RCON_PORT}...")
        try:
            client = WebRCONClient(RCON_HOST, RCON_PORT, RCON_PASSWORD)
            if await client.connect():
                response = await client.send_command("version")
                if response:
                    logger.info(f"✓ Успешное подключение к WebRCON на порту {RCON_PORT}!")
                    logger.info(f"Ответ сервера на 'version': {response[:100]}")
                    webrcon_client = client
                    use_webrcon = True
                    rcon_port = RCON_PORT
                    return True
                else:
                    await client.close()
        except Exception as e:
            logger.error(f"Ошибка при подключении к WebRCON: {e}")
    
    # Пробуем обычный RCON на разных портах
    for port in RCON_PORTS:
        logger.info(f"Попытка подключения к RCON: {RCON_HOST}:{port}")
        
        try:
            client = RCONClient(RCON_HOST, port, RCON_PASSWORD, RCON_TIMEOUT)
            
            # Подключение
            if not client.connect():
                logger.warning(f"Не удалось подключиться к {RCON_HOST}:{port}")
                continue
            
            # Аутентификация
            if not client.authenticate():
                logger.warning(f"Не удалось аутентифицироваться на {RCON_HOST}:{port}")
                client.close()
                continue
            
            # Проверка подключения командой
            response = client.send_command("version")
            if response:
                logger.info(f"✓ Успешное подключение к RCON на порту {port}!")
                logger.info(f"Ответ сервера на 'version': {response[:100]}")
                rcon_client = client
                use_webrcon = False
                rcon_port = port
                return True
            else:
                logger.warning(f"Подключение установлено, но команда не выполнена на {port}")
                client.close()
                
        except Exception as e:
            logger.error(f"Ошибка при подключении к {RCON_HOST}:{port}: {e}")
            continue
    
    # Если обычный RCON не сработал, пробуем WebRCON как запасной вариант
    # WebRCON использует тот же порт, что и RCON, но через WebSocket протокол
    if not WEBRCON_ENABLED:
        logger.info(f"Обычный RCON не сработал, пробуем WebRCON как запасной вариант на порту {RCON_PORT}...")
        try:
            client = WebRCONClient(RCON_HOST, RCON_PORT, RCON_PASSWORD)
            if await client.connect():
                response = await client.send_command("version")
                if response:
                    logger.info(f"✓ Успешное подключение к WebRCON на порту {RCON_PORT}!")
                    logger.info(f"Ответ сервера на 'version': {response[:100]}")
                    webrcon_client = client
                    use_webrcon = True
                    rcon_port = RCON_PORT
                    return True
                else:
                    await client.close()
        except Exception as e:
            logger.error(f"Ошибка при подключении к WebRCON: {e}")
    
    logger.error("Не удалось подключиться ни к одному типу RCON")
    return False


async def send_rcon_command(command: str):
    """
    Отправка команды на RCON сервер (RCON или WebRCON)
    """
    global rcon_client, rcon_library_client, webrcon_client, use_webrcon, use_rcon_library
    
    try:
        # Проверяем наличие подключения
        if use_rcon_library:
            # Используем библиотеку python-rcon
            if rcon_library_client is None:
                success = await connect_to_rcon()
                if not success:
                    return None
            
            if rcon_library_client is None:
                return None
            
            # Отправка команды через библиотеку
            loop = asyncio.get_event_loop()
            def run_command():
                try:
                    return rcon_library_client.run(command)
                except Exception as e:
                    logger.error(f"Ошибка при выполнении команды через python-rcon: {e}")
                    return None
            
            response = await loop.run_in_executor(None, run_command)
            return response
        elif use_webrcon:
            if webrcon_client is None or webrcon_client.websocket is None:
                success = await connect_to_rcon()
                if not success:
                    return None
            if webrcon_client:
                return await webrcon_client.send_command(command)
        else:
            if rcon_client is None:
                success = await connect_to_rcon()
                if not success:
                    return None
            
            if rcon_client is None:
                return None
            
            # Отправка команды через executor для избежания блокировки
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                rcon_client.send_command,
                command
            )
            return response
            
    except Exception as e:
        logger.error(f"Ошибка при отправке команды '{command}': {e}")
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
    logger.info("Запрос на переподключение к RCON")
    
    await ctx.send("🔄 Попытка переподключения к RCON серверу...")
    success = await connect_to_rcon()
    
    if success:
        await ctx.send(f"✅ Успешно переподключено к RCON серверу на порту {rcon_port}!")
    else:
        await ctx.send("❌ Не удалось переподключиться. Проверьте логи.")


@bot.command(name='rcon_status')
async def rcon_status(ctx):
    """
    Команда для проверки статуса RCON подключения
    """
    global rcon_client, webrcon_client, use_webrcon, rcon_port
    
    connection_type = "WebRCON" if use_webrcon else "RCON"
    is_connected = False
    
    if use_webrcon:
        is_connected = webrcon_client and webrcon_client.websocket
    else:
        is_connected = rcon_client and rcon_client.sock
    
    if is_connected:
        # Проверяем подключение отправкой тестовой команды
        response = await send_rcon_command("version")
        if response:
            await ctx.send(
                f"✅ **{connection_type} подключение активно**\n"
                f"**Тип:** {connection_type}\n"
                f"**Хост:** {RCON_HOST}\n"
                f"**Порт:** {rcon_port or 'неизвестен'}\n"
                f"**Статус:** Работает"
            )
        else:
            await ctx.send(
                f"⚠️ **{connection_type} подключение установлено, но команды не выполняются**\n"
                f"**Тип:** {connection_type}\n"
                f"**Хост:** {RCON_HOST}\n"
                f"**Порт:** {rcon_port or 'неизвестен'}\n"
                f"**Статус:** Проблемы с выполнением команд"
            )
    else:
        await ctx.send(
            f"❌ **RCON не подключен**\n"
            f"**Хост:** {RCON_HOST}\n"
            f"**RCON порт:** {RCON_PORT}\n"
            f"**RCON порты для попытки:** {', '.join(map(str, RCON_PORTS))}\n"
            f"**WebRCON включен:** {'Да (использует тот же порт через WebSocket)' if WEBRCON_ENABLED else 'Нет'}\n"
            f"**Статус:** Отключен\n\n"
            f"Используйте `!rcon_reconnect` для переподключения."
        )


if __name__ == "__main__":
    if DISCORD_TOKEN == "YOUR_DISCORD_BOT_TOKEN_HERE" or not DISCORD_TOKEN:
        logger.error("Пожалуйста, установите DISCORD_TOKEN в файле .env")
        logger.error("Создайте файл .env на основе .env.example и заполните ваш токен Discord бота")
    else:
        bot.run(DISCORD_TOKEN)

