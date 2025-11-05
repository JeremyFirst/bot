"""
Утилиты для работы с временем
Конвертация UTC → MSK (UTC+3)
"""
from datetime import datetime, timezone, timedelta
from typing import Optional

MSK_TZ = timezone(timedelta(hours=3))  # MSK = UTC+3


def parse_utc_datetime(date_str: str, time_str: str, month_name: str, day: int, year: int) -> Optional[datetime]:
    """
    Парсинг даты и времени из строки RCON ответа
    
    Args:
        date_str: День недели (не используется)
        time_str: Время в формате HH:MM
        month_name: Название месяца (англ.)
        day: День месяца
        year: Год
        
    Returns:
        datetime объект в UTC или None при ошибке
    """
    month_map = {
        'january': 1, 'jan': 1,
        'february': 2, 'feb': 2,
        'march': 3, 'mar': 3,
        'april': 4, 'apr': 4,
        'may': 5,
        'june': 6, 'jun': 6,
        'july': 7, 'jul': 7,
        'august': 8, 'aug': 8,
        'september': 9, 'sep': 9, 'sept': 9,
        'october': 10, 'oct': 10,
        'november': 11, 'nov': 11,
        'december': 12, 'dec': 12
    }
    
    month_name_lower = month_name.lower()
    month = month_map.get(month_name_lower)
    if not month:
        return None
    
    try:
        hour, minute = map(int, time_str.split(':'))
        dt_utc = datetime(year, month, day, hour, minute, tzinfo=timezone.utc)
        return dt_utc
    except (ValueError, KeyError) as e:
        return None


def utc_to_msk(utc_dt: datetime) -> datetime:
    """
    Конвертация UTC времени в MSK (UTC+3)
    
    Args:
        utc_dt: datetime объект в UTC
        
    Returns:
        datetime объект в MSK (UTC+3)
    """
    if utc_dt.tzinfo is None:
        # Если timezone не указан, считаем что это UTC
        utc_dt = utc_dt.replace(tzinfo=timezone.utc)
    
    # Добавляем 3 часа
    msk_dt = utc_dt.astimezone(MSK_TZ)
    return msk_dt


def format_datetime_msk(dt: datetime) -> str:
    """
    Форматирование datetime для отображения (MSK)
    
    Args:
        dt: datetime объект
        
    Returns:
        Отформатированная строка
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=MSK_TZ)
    
    return dt.strftime("%d.%m.%Y %H:%M MSK")

