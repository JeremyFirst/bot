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

-- admin_list_messages: хранение message_id для автоматического обновления состава администрации
CREATE TABLE IF NOT EXISTS admin_list_messages (
  id INT AUTO_INCREMENT PRIMARY KEY,
  guild_id BIGINT NOT NULL UNIQUE,
  channel_id BIGINT NOT NULL,
  message_id BIGINT NOT NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX IF NOT EXISTS idx_admin_list_guild_id ON admin_list_messages(guild_id);
CREATE INDEX IF NOT EXISTS idx_admin_list_message_id ON admin_list_messages(message_id);

-- ticket_panels: хранение сообщения панели тикетов
CREATE TABLE IF NOT EXISTS ticket_panels (
  id INT AUTO_INCREMENT PRIMARY KEY,
  guild_id BIGINT NOT NULL UNIQUE,
  channel_id BIGINT NOT NULL,
  message_id BIGINT NOT NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX IF NOT EXISTS idx_ticket_panels_guild_id ON ticket_panels(guild_id);
CREATE INDEX IF NOT EXISTS idx_ticket_panels_message_id ON ticket_panels(message_id);

-- tickets: основные данные по тикетам
CREATE TABLE IF NOT EXISTS tickets (
  id INT AUTO_INCREMENT PRIMARY KEY,
  ticket_number INT NOT NULL,
  guild_id BIGINT NOT NULL,
  channel_id BIGINT NOT NULL,
  control_message_id BIGINT NULL,
  owner_id BIGINT NOT NULL,
  assignee_id BIGINT NULL,
  status VARCHAR(32) NOT NULL DEFAULT 'open',
  reason VARCHAR(64) NOT NULL,
  form_data TEXT,
  transcript_url TEXT,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  closed_at TIMESTAMP NULL,
  INDEX idx_tickets_guild_id (guild_id),
  INDEX idx_tickets_channel_id (channel_id),
  INDEX idx_tickets_owner_id (owner_id),
  INDEX idx_tickets_assignee_id (assignee_id),
  INDEX idx_tickets_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Индексы для оптимизации запросов
CREATE INDEX IF NOT EXISTS idx_privileges_discord_id ON privileges(discord_id);
CREATE INDEX IF NOT EXISTS idx_privileges_steamid ON privileges(steamid);
CREATE INDEX IF NOT EXISTS idx_privileges_expires_at ON privileges(expires_at);
CREATE INDEX IF NOT EXISTS idx_warnings_discord_id ON warnings(discord_id);
CREATE INDEX IF NOT EXISTS idx_warnings_category ON warnings(category);
CREATE INDEX IF NOT EXISTS idx_users_discord_id ON users(discord_id);
CREATE INDEX IF NOT EXISTS idx_users_steamid ON users(steamid);
