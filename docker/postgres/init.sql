-- InsideDCPulse — Event Store schema
-- Nothing is updated directly. world_state is a materialized projection of events.

CREATE TABLE IF NOT EXISTS agents (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    api_key_hash    TEXT NOT NULL UNIQUE,
    reputation      NUMERIC(5,4) NOT NULL DEFAULT 0.5000,
    total_submitted BIGINT NOT NULL DEFAULT 0,
    total_accepted  BIGINT NOT NULL DEFAULT 0,
    total_rejected  BIGINT NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at    TIMESTAMPTZ,
    created_via     TEXT NOT NULL DEFAULT 'admin'
);

-- Append-only event log. This IS the source of truth.
CREATE TABLE IF NOT EXISTS events (
    id              BIGSERIAL PRIMARY KEY,
    event_id        UUID NOT NULL UNIQUE,
    agent_id        TEXT NOT NULL REFERENCES agents(id),
    event_type      TEXT NOT NULL,           -- 'vision' | 'action' | 'genesis' | 'internal'
    payload         JSONB NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending', -- pending | accepted | rejected
    score           NUMERIC(5,4),
    reason          TEXT,
    source          TEXT NOT NULL DEFAULT 'queue',   -- queue | internal
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    processed_at    TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_events_agent ON events(agent_id);
CREATE INDEX IF NOT EXISTS idx_events_status ON events(status);
CREATE INDEX IF NOT EXISTS idx_events_created ON events(created_at DESC);

-- Materialized world state, derived purely by replaying accepted events.
CREATE TABLE IF NOT EXISTS world_state (
    key                TEXT PRIMARY KEY,
    value              JSONB NOT NULL,
    version            BIGINT NOT NULL DEFAULT 0,
    updated_by_event   BIGINT REFERENCES events(id),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Rolling drift samples: |simulated_value - committed_value| per key, for the Drift Meter dashboard.
CREATE TABLE IF NOT EXISTS drift_samples (
    id              BIGSERIAL PRIMARY KEY,
    key             TEXT NOT NULL,
    simulated_value JSONB,
    committed_value JSONB,
    drift           NUMERIC(10,4) NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_drift_created ON drift_samples(created_at DESC);

-- Genesis agent used for internal/system commits.
INSERT INTO agents (id, name, api_key_hash, reputation)
VALUES ('system', 'InsideDCPulse System', 'system-internal-no-login', 1.0)
ON CONFLICT (id) DO NOTHING;
