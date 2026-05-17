-- LEO AI — Complete Database Schema
-- ═══════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS appointments (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    phone TEXT NOT NULL,
    date TEXT NOT NULL,
    time TEXT NOT NULL,
    service TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'booked',
    direction TEXT NOT NULL DEFAULT 'outbound',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS call_logs (
    id TEXT PRIMARY KEY,
    phone_number TEXT NOT NULL,
    lead_name TEXT,
    direction TEXT NOT NULL DEFAULT 'outbound',
    outcome TEXT,
    reason TEXT,
    duration_seconds INTEGER,
    recording_url TEXT,
    notes TEXT,
    agent_profile_id TEXT,
    timestamp TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS error_logs (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    level TEXT NOT NULL DEFAULT 'error',
    message TEXT NOT NULL,
    detail TEXT,
    timestamp TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS campaigns (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    contacts_json TEXT NOT NULL DEFAULT '[]',
    schedule_type TEXT NOT NULL DEFAULT 'once',
    schedule_time TEXT DEFAULT '09:00',
    call_delay_seconds INTEGER DEFAULT 3,
    system_prompt TEXT,
    agent_profile_id TEXT,
    total_dispatched INTEGER DEFAULT 0,
    total_failed INTEGER DEFAULT 0,
    last_run_at TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS contact_memory (
    id TEXT PRIMARY KEY,
    phone_number TEXT NOT NULL,
    insight TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_profiles (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    voice TEXT NOT NULL DEFAULT 'Aoede',
    model TEXT NOT NULL DEFAULT 'gemini-3.1-flash-live-preview',
    mode TEXT NOT NULL DEFAULT 'both',
    system_prompt TEXT,
    enabled_tools TEXT DEFAULT '[]',
    is_default INTEGER DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS inbound_routes (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    trunk_id TEXT NOT NULL,
    phone_number TEXT NOT NULL,
    agent_profile_id TEXT,
    greeting TEXT,
    system_prompt TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    total_calls INTEGER DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS faq_entries (
    id TEXT PRIMARY KEY,
    question TEXT NOT NULL,
    answer TEXT NOT NULL,
    category TEXT DEFAULT 'general',
    priority INTEGER DEFAULT 0,
    created_at TEXT NOT NULL
);

-- ═══════════════════════════════════════════════════════
-- Indexes
-- ═══════════════════════════════════════════════════════

CREATE INDEX IF NOT EXISTS idx_call_logs_phone ON call_logs(phone_number);
CREATE INDEX IF NOT EXISTS idx_call_logs_direction ON call_logs(direction);
CREATE INDEX IF NOT EXISTS idx_call_logs_timestamp ON call_logs(timestamp);
CREATE INDEX IF NOT EXISTS idx_appointments_date ON appointments(date);
CREATE INDEX IF NOT EXISTS idx_contact_memory_phone ON contact_memory(phone_number);
CREATE INDEX IF NOT EXISTS idx_inbound_routes_trunk ON inbound_routes(trunk_id);
CREATE INDEX IF NOT EXISTS idx_faq_category ON faq_entries(category);

-- ═══════════════════════════════════════════════════════
-- Seed: insert a test setting to verify connection
-- ═══════════════════════════════════════════════════════

INSERT INTO settings (key, value, updated_at)
VALUES ('TEST_KEY', 'LEO_AI_OK', NOW()::TEXT)
ON CONFLICT (key) DO UPDATE SET value = 'LEO_AI_OK', updated_at = NOW()::TEXT;
