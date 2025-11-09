"""
Команды бота
"""
from .admin import setup as setup_admin
from .privilege import setup as setup_privilege
from .warn import setup as setup_warn
from .tickets import setup as setup_tickets

__all__ = ['setup_admin', 'setup_privilege', 'setup_warn', 'setup_tickets']

