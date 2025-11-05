-- Схема базы данных для Discord-бота управления RUST-сервером
-- Поддержка MariaDB/MySQL

-- users: кешируем связь discord <-> steam
CREATE TABLE IF NOT EXISTS users (
  id INT AUTO_INCREMENT PRIMARY KEY,
  discord_id BIGINT NOT NULL UNIQUE,
  steamid VARCHAR(32) NOT NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- privileges: текущие активные привилегии
CREATE TABLE IF NOT EXISTS privileges (
  id INT AUTO_INCREMENT PRIMARY KEY,
  discord_id BIGINT NOT NULL,
  steamid VARCHAR(32) NOT NULL,
  group_name VARCHAR(64) NOT NULL,
  expires_at DATETIME NULL, -- в MSK (UTC+3)
  expires_at_utc DATETIME NULL, -- исходное UTC
  permanent BOOLEAN DEFAULT FALSE, -- флаг для перманентных привилегий
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- warnings: выговоры
CREATE TABLE IF NOT EXISTS warnings (
  id INT AUTO_INCREMENT PRIMARY KEY,
  discord_id BIGINT NOT NULL,
  executor_id BIGINT NOT NULL,
  reason TEXT,
  category INT DEFAULT 0, -- 0 = Наборная (recruitment), 1 = Донатная (donat)
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- config: таблица маппинга групп к ролям
CREATE TABLE IF NOT EXISTS role_mappings (
  group_name VARCHAR(64) PRIMARY KEY,
  discord_role_id BIGINT NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Индексы для оптимизации запросов
CREATE INDEX IF NOT EXISTS idx_privileges_discord_id ON privileges(discord_id);
CREATE INDEX IF NOT EXISTS idx_privileges_steamid ON privileges(steamid);
CREATE INDEX IF NOT EXISTS idx_privileges_expires_at ON privileges(expires_at);
CREATE INDEX IF NOT EXISTS idx_warnings_discord_id ON warnings(discord_id);
CREATE INDEX IF NOT EXISTS idx_warnings_category ON warnings(category);
CREATE INDEX IF NOT EXISTS idx_users_discord_id ON users(discord_id);
CREATE INDEX IF NOT EXISTS idx_users_steamid ON users(steamid);
