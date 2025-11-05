import discord
from discord.ext import commands
import asyncio
import logging
import os
import json
from typing import Optional
from dotenv import load_dotenv
import websockets

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

# Конфигурация RCON (WebRCON) из .env
RCON_HOST = os.getenv('RCON_HOST', 'localhost')
RCON_PORT = int(os.getenv('RCON_PORT', '27015'))
RCON_PASSWORD = os.getenv('RCON_PASSWORD', '')

# Список портов для попыток подключения WebRCON
RCON_PORTS = [RCON_PORT, RCON_PORT - 2, RCON_PORT + 2, RCON_PORT - 10, RCON_PORT + 10]
RCON_PORTS = [p for p in RCON_PORTS if p > 0 and p < 65536]  # Фильтруем валидные порты
RCON_PORTS = list(dict.fromkeys(RCON_PORTS))  # Удаляем дубликаты

# Конфигурация Discord бота из .env
DISCORD_TOKEN = os.getenv('DISCORD_TOKEN', 'YOUR_DISCORD_BOT_TOKEN_HERE')

# Создание бота с префиксом команд
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents)

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
    
    # Автоматическое подключение к WebRCON при запуске бота
    logger.info("[DEBUG] Попытка автоматического подключения к WebRCON серверу...")
    success = await connect_to_rcon()
    
    if success:
        logger.info("[DEBUG] ✓ Успешное подключение к WebRCON серверу!")
        
        # Устанавливаем статус бота
        await bot.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name=f"WebRCON (порт {rcon_port})"
            ),
            status=discord.Status.online
        )
    else:
        logger.error("[DEBUG] ✗ Не удалось подключиться к WebRCON серверу!")
        logger.error("[DEBUG] Убедитесь, что в Startup Command установлено: +rcon.web true")
        await bot.change_presence(
            status=discord.Status.idle,
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name="WebRCON (не подключен)"
            )
        )


@bot.command(name='rcon_test')
async def rcon_test(ctx):
    """
    Тестовая команда для проверки WebRCON подключения
    """
    logger.info("[DEBUG] Запрос на тестирование WebRCON подключения")
    
    # Попытка подключения
    success = await connect_to_rcon()
    
    if success:
        # Попытка отправить команду
        response = await send_rcon_command("version")
        
        if response:
            await ctx.send(f"✅ WebRCON подключение работает!\n**Ответ сервера:**\n```{response}```")
        else:
            await ctx.send("⚠️ Подключение установлено, но команда не выполнена")
    else:
        await ctx.send("❌ Не удалось подключиться к WebRCON серверу. Проверьте логи.")


@bot.command(name='rcon')
async def rcon_command(ctx, *, command: str):
    """
    Команда для отправки RCON команд на сервер через WebRCON
    Использование: !rcon <команда>
    """
    logger.info(f"[DEBUG] Запрос на выполнение RCON команды: {command}")
    
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

