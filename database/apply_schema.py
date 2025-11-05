"""
Скрипт для применения схемы базы данных
Можно запустить вручную или через команду
"""
import asyncio
import sys
from pathlib import Path

# Добавляем корневую директорию проекта в PYTHONPATH
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.database.models import Database
from config.config import DB_URL


async def apply_schema():
    """Применение схемы базы данных"""
    print("Применение схемы базы данных...")
    
    # Читаем схему из файла
    schema_file = Path(__file__).parent / "schema.sql"
    if not schema_file.exists():
        print(f"❌ Файл схемы не найден: {schema_file}")
        return False
    
    with open(schema_file, 'r', encoding='utf-8') as f:
        schema_sql = f.read()
    
    # Подключаемся к БД
    db = Database(DB_URL)
    try:
        await db.connect()
        print("✓ Подключение к БД установлено")
        
        if not db.pool:
            print("❌ Ошибка: пул соединений не создан")
            return False
        
        # Разбиваем SQL на отдельные команды
        commands = [cmd.strip() for cmd in schema_sql.split(';') if cmd.strip() and not cmd.strip().startswith('--')]
        
        async with db.pool.acquire() as conn:
            async with conn.cursor() as cursor:
                for command in commands:
                    if command:
                        try:
                            await cursor.execute(command)
                            print(f"✓ Выполнено: {command[:50]}...")
                        except Exception as e:
                            # Игнорируем ошибки "table already exists"
                            if "already exists" in str(e).lower() or "Duplicate" in str(e):
                                print(f"ℹ️ Пропущено (уже существует): {command[:50]}...")
                            else:
                                print(f"⚠️ Ошибка при выполнении: {e}")
                                print(f"   Команда: {command[:100]}")
        
        await conn.commit()
        print("\n✅ Схема базы данных успешно применена!")
        return True
        
    except Exception as e:
        print(f"❌ Ошибка при применении схемы: {e}")
        return False
    finally:
        await db.close()


if __name__ == "__main__":
    success = asyncio.run(apply_schema())
    sys.exit(0 if success else 1)
