CREATE TABLE IF NOT EXISTS endpoints (
    id TEXT PRIMARY KEY,
    url TEXT NOT NULL UNIQUE,
    secret TEXT NOT NULL,
    description TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    payload TEXT NOT NULL,
    idempotency_key TEXT UNIQUE,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS deliveries (
    id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL REFERENCES events(id),
    endpoint_id TEXT NOT NULL REFERENCES endpoints(id),
    status TEXT NOT NULL CHECK (status IN ('pending', 'succeeded', 'failed')),
    created_at TEXT NOT NULL,
    next_attempt_at TEXT,
    UNIQUE (event_id, endpoint_id)
);

CREATE TABLE IF NOT EXISTS delivery_attempts (
    id TEXT PRIMARY KEY,
    delivery_id TEXT NOT NULL REFERENCES deliveries(id),
    attempt_number INTEGER NOT NULL,
    status_code INTEGER,
    error TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    UNIQUE (delivery_id, attempt_number)
);
