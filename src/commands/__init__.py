"""
Команды бота
"""
from .admin import setup as setup_admin
from .privilege import setup as setup_privilege
from .warn import setup as setup_warn

__all__ = ['setup_admin', 'setup_privilege', 'setup_warn']

