"""
Парсеры для RCON ответов
"""
import re
import logging
from typing import Optional, List, Dict, Tuple
from datetime import datetime

from config.config import GROUP_NAME_DATABASE, GROUP_HIERARCHY
from src.utils.time_utils import parse_utc_datetime, utc_to_msk

logger = logging.getLogger(__name__)


def remove_color_tags(text: str) -> str:
    """
    Удаление HTML-подобных цветовых тегов <color=...> из текста
    
    Args:
        text: Исходный текст
        
    Returns:
        Текст без цветовых тегов
    """
    # Удаляем теги <color=...> и </color>
    text = re.sub(r'<color=[^>]*>', '', text)
    text = re.sub(r'</color>', '', text)
    # Удаляем другие возможные теги
    text = re.sub(r'<[^>]+>', '', text)
    return text.strip()


def parse_pinfo_response(response: str) -> Dict:
    """
    Парсинг ответа RCON команды pinfo
    
    Args:
        response: Ответ от RCON
        
    Returns:
        Словарь с информацией:
        {
            'has_privileges': bool,
            'groups': List[Dict],  # [{'name': str, 'expires_at_utc': datetime, 'permanent': bool}]
            'raw_response': str
        }
    """
    if not response:
        return {'has_privileges': False, 'groups': [], 'raw_response': response}
    
    # Удаляем цветовые теги
    clean_response = remove_color_tags(response)
    
    # Проверяем, есть ли привилегии
    if 'There is no info about this player' in clean_response or 'No player found' in clean_response.lower():
        return {'has_privileges': False, 'groups': [], 'raw_response': response}
    
    # Ищем блок Groups
    groups = []
    
    # Регулярное выражение для групп с датой
    # Формат: group_name until Day, DD Month YYYY HH:MM UTC
    # Также поддерживаем формат: Groups: group_name until Day, DD Month YYYY HH:MM UTC
    pattern = r'(?:Groups?:\s*)?([A-Za-z0-9_\-]+)\s+until\s+([A-Za-z]+),\s+(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})\s+(\d{2}:\d{2})\s+UTC'
    
    matches = re.finditer(pattern, clean_response, re.IGNORECASE)
    
    for match in matches:
        group_name = match.group(1)
        day_of_week = match.group(2)
        day = int(match.group(3))
        month_name = match.group(4)
        year = int(match.group(5))
        time_str = match.group(6)
        
        logger.debug(f"Найдена группа с датой: {group_name} until {day_of_week}, {day} {month_name} {year} {time_str} UTC")
        
        # Парсим дату
        expires_at_utc = parse_utc_datetime(day_of_week, time_str, month_name, day, year)
        
        if expires_at_utc:
            groups.append({
                'name': group_name,
                'expires_at_utc': expires_at_utc,
                'permanent': False
            })
            logger.debug(f"Добавлена группа: {group_name}, expires_at_utc: {expires_at_utc}")
        else:
            logger.warning(f"Не удалось распарсить дату для группы {group_name}")
    
    # Ищем группы без даты (перманентные)
    # Ищем строки вида "group_name" или "group_name (permanent)"
    permanent_pattern = r'^\s*([A-Za-z0-9_\-]+)\s*(?:\(permanent\))?\s*$'
    
    # Разбиваем на строки и ищем перманентные группы
    lines = clean_response.split('\n')
    for line in lines:
        line = line.strip()
        if not line or 'until' in line.lower():
            continue
        
        # Проверяем, не является ли это группой
        match = re.match(r'^([A-Za-z0-9_\-]+)$', line)
        if match:
            group_name = match.group(1)
            # Проверяем, что это валидное название группы
            if any(group_name.lower() == db_group.lower() for db_group in GROUP_NAME_DATABASE):
                # Проверяем, что эта группа еще не добавлена
                if not any(g['name'].lower() == group_name.lower() for g in groups):
                    groups.append({
                        'name': group_name,
                        'expires_at_utc': None,
                        'permanent': True
                    })
    
    logger.debug(f"Парсинг pinfo завершен. Найдено групп: {len(groups)}, группы: {[g['name'] for g in groups]}")
    
    return {
        'has_privileges': len(groups) > 0,
        'groups': groups,
        'raw_response': response
    }


def select_highest_group(groups: List[Dict]) -> Optional[Dict]:
    """
    Выбор самой высокой группы по иерархии
    
    Args:
        groups: Список групп из parse_pinfo_response
        
    Returns:
        Группа с наивысшим приоритетом или None
    """
    if not groups:
        return None
    
    # Фильтруем группы по базе наименований
    valid_groups = [
        g for g in groups
        if any(g['name'].lower() == db_group.lower() for db_group in GROUP_NAME_DATABASE)
    ]
    
    if not valid_groups:
        return None
    
    # Выбираем самую высокую по иерархии
    highest_group = None
    highest_priority = len(GROUP_HIERARCHY)  # Начинаем с максимального значения
    
    for group in valid_groups:
        group_name_lower = group['name'].lower()
        try:
            priority = GROUP_HIERARCHY.index(group_name_lower)
            if priority < highest_priority:
                highest_priority = priority
                highest_group = group
        except ValueError:
            # Группа не найдена в иерархии, пропускаем
            continue
    
    return highest_group


def parse_removegroup_response(response: str) -> bool:
    """
    Парсинг ответа RCON команды removegroup для проверки успешности
    
    Args:
        response: Ответ от RCON
        
    Returns:
        True если группа успешно удалена, False иначе
    """
    if not response:
        return False
    
    clean_response = remove_color_tags(response).lower()
    
    success_phrases = [
        'вы успешно исключили игрока',
        'removed from group',
        'removed',
        'config saved',
        "player ' removed from group",
        'successfully removed',
        'group removed'
    ]
    
    for phrase in success_phrases:
        if phrase in clean_response:
            return True
    
    return False

