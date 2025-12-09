"""
HTTP API для приема логов от AdminLogCore плагина Rust сервера
"""
import json
import logging
from typing import Optional
from aiohttp import web
from datetime import datetime
import discord

logger = logging.getLogger(__name__)


class LogReceiver:
    """HTTP сервер для приема логов от AdminLogCore"""
    
    def __init__(self, bot, auth_token: str, log_channel_id: Optional[int] = None):
        """
        Инициализация приемника логов
        
        Args:
            bot: Экземпляр Discord бота
            auth_token: Токен авторизации (должен совпадать с AuthToken в AdminLogCore)
            log_channel_id: ID канала Discord для отправки логов (опционально)
        """
        self.bot = bot
        self.auth_token = auth_token
        self.log_channel_id = log_channel_id
        self.app = web.Application()
        self.runner: Optional[web.AppRunner] = None
        self.site: Optional[web.TCPSite] = None
        
        # Настройка маршрутов
        self.app.router.add_post('/log', self.handle_log)
        self.app.router.add_get('/health', self.handle_health)
        
        logger.info("LogReceiver инициализирован")
    
    async def handle_log(self, request: web.Request) -> web.Response:
        """
        Обработка POST запроса с логом от AdminLogCore
        
        Формат запроса:
        {
            "source": "AdminMenu",
            "category": "ADMIN",
            "message": "ARC выдал кит starter игроку TestUser",
            "timestamp": "2025-12-08T00:00:00Z"
        }
        
        Заголовки:
        - Auth: <auth_token>
        - Content-Type: application/json
        """
        try:
            # Проверка авторизации
            auth_header = request.headers.get('Auth')
            if auth_header != self.auth_token:
                logger.warning(f"Неавторизованный запрос от {request.remote}")
                return web.json_response(
                    {"error": "Unauthorized"},
                    status=401
                )
            
            # Получение JSON данных
            try:
                data = await request.json()
            except json.JSONDecodeError as e:
                logger.error(f"Ошибка парсинга JSON: {e}")
                return web.json_response(
                    {"error": "Invalid JSON"},
                    status=400
                )
            
            # Валидация данных
            required_fields = ['source', 'category', 'message', 'timestamp']
            missing_fields = [field for field in required_fields if field not in data]
            if missing_fields:
                logger.error(f"Отсутствуют обязательные поля: {missing_fields}")
                return web.json_response(
                    {"error": f"Missing required fields: {', '.join(missing_fields)}"},
                    status=400
                )
            
            # Извлечение данных
            source = data.get('source', 'Unknown')
            category = data.get('category', 'INFO')
            message = data.get('message', '')
            timestamp = data.get('timestamp', '')
            
            # Логирование в консоль
            logger.info(f"[{category}] {source}: {message}")
            
            # Отправка в Discord канал (если настроен)
            if self.log_channel_id:
                await self.send_to_discord(source, category, message, timestamp)
            
            return web.json_response(
                {"status": "ok", "received": True},
                status=200
            )
            
        except Exception as e:
            logger.error(f"Ошибка обработки лога: {e}", exc_info=True)
            return web.json_response(
                {"error": "Internal server error"},
                status=500
            )
    
    async def send_to_discord(self, source: str, category: str, message: str, timestamp: str):
        """
        Отправка лога в Discord канал
        
        Args:
            source: Источник лога (название плагина)
            category: Категория лога (INFO, WARN, ADMIN, DANGER, DEBUG)
            message: Текст сообщения
            timestamp: Временная метка события
        """
        try:
            if not self.bot or not self.bot.is_ready():
                logger.warning("Бот не готов, пропускаем отправку в Discord")
                return
            
            # Поиск канала
            channel = None
            for guild in self.bot.guilds:
                channel = guild.get_channel(self.log_channel_id)
                if channel:
                    break
            
            if not channel:
                logger.warning(f"Канал с ID {self.log_channel_id} не найден")
                return
            
            # Определение цвета embed в зависимости от категории
            color_map = {
                'INFO': 0x3498db,      # Синий
                'WARN': 0xf39c12,      # Оранжевый
                'ADMIN': 0x2ecc71,     # Зеленый
                'DANGER': 0xe74c3c,    # Красный
                'DEBUG': 0x95a5a6,     # Серый
            }
            color = color_map.get(category.upper(), 0x3498db)
            
            # Создание embed
            embed = discord.Embed(
                title=f"[{category}] {source}",
                description=message,
                color=color,
                timestamp=datetime.utcnow()
            )
            
            # Добавление временной метки события (если есть)
            if timestamp:
                try:
                    # Парсим timestamp из формата ISO
                    event_time = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
                    embed.add_field(
                        name="Время события",
                        value=f"<t:{int(event_time.timestamp())}:F>",
                        inline=False
                    )
                except Exception:
                    # Если не удалось распарсить, просто добавляем как текст
                    embed.add_field(
                        name="Время события",
                        value=timestamp,
                        inline=False
                    )
            
            # Отправка в канал
            await channel.send(embed=embed)
            logger.debug(f"Лог отправлен в Discord канал {self.log_channel_id}")
            
        except Exception as e:
            logger.error(f"Ошибка отправки лога в Discord: {e}", exc_info=True)
    
    async def handle_health(self, request: web.Request) -> web.Response:
        """Health check endpoint"""
        return web.json_response({
            "status": "ok",
            "service": "AdminLogCore Log Receiver",
            "bot_ready": self.bot.is_ready() if self.bot else False
        })
    
    async def start(self, host: str = '127.0.0.1', port: int = 5000):
        """
        Запуск HTTP сервера
        
        Args:
            host: Хост для прослушивания (по умолчанию 127.0.0.1)
            port: Порт для прослушивания (по умолчанию 5000)
        """
        try:
            self.runner = web.AppRunner(self.app)
            await self.runner.setup()
            self.site = web.TCPSite(self.runner, host, port)
            await self.site.start()
            logger.info(f"✓ HTTP сервер запущен на {host}:{port}")
            logger.info(f"  Endpoint: http://{host}:{port}/log")
        except Exception as e:
            logger.error(f"Ошибка запуска HTTP сервера: {e}", exc_info=True)
            raise
    
    async def stop(self):
        """Остановка HTTP сервера"""
        try:
            if self.site:
                await self.site.stop()
            if self.runner:
                await self.runner.cleanup()
            logger.info("HTTP сервер остановлен")
        except Exception as e:
            logger.error(f"Ошибка остановки HTTP сервера: {e}", exc_info=True)

