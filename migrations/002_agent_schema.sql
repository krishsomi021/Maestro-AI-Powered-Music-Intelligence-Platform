-- Agent layer tables: sessions, messages, tool-call audit, eval runs.
-- No role creation, no interpolated secrets (see scripts/provision_agent_role.py).
-- UUID default: gen_random_uuid() matches migrations/init.sql.

CREATE TABLE IF NOT EXISTS agent_sessions (
    session_id   VARCHAR(255)  PRIMARY KEY,
    model        VARCHAR(100)  NOT NULL,
    metadata     JSONB         NOT NULL DEFAULT '{}',
    created_at   TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    updated_at   TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS agent_messages (
    id           UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id   VARCHAR(255)  NOT NULL,
    role         VARCHAR(20)   NOT NULL,
    content      TEXT          NOT NULL,
    turn_index   INTEGER       NOT NULL,
    created_at   TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS agent_tool_calls (
    id             UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id     VARCHAR(255)  NOT NULL,
    call_id        VARCHAR(100),
    tool_name      VARCHAR(100)  NOT NULL,
    tool_input     JSONB         NOT NULL,
    tool_output    JSONB,
    latency_ms     INTEGER,
    success        BOOLEAN       NOT NULL DEFAULT TRUE,
    error_message  TEXT,
    iteration      INTEGER       NOT NULL DEFAULT 1,
    created_at     TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS eval_runs (
    id           UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id   VARCHAR(255)  NOT NULL,
    case_id      VARCHAR(255)  NOT NULL,
    judge_model  VARCHAR(100)  NOT NULL,
    score        NUMERIC(4,3),
    reasoning    TEXT,
    created_at   TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_agent_messages_session_id   ON agent_messages   (session_id);
CREATE INDEX IF NOT EXISTS idx_agent_tool_calls_session_id ON agent_tool_calls (session_id);
CREATE INDEX IF NOT EXISTS idx_eval_runs_session_id        ON eval_runs        (session_id);
