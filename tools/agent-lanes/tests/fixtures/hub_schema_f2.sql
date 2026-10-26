-- Esquema `hub` de PRUEBA para el backend `supabase` del kanban (F2-02) y el importador (F2-03).
-- NO es una migración: la real es F2-01 (oscar-hq, supabase/migrations/20261108_hub_kanban.sql, número reservado
-- en el plan). Este fichero fija lo que el código del hub necesita; F2-01 debe tener al menos estas tablas y
-- columnas (SupabaseKanban.check_contract lo comprueba al arrancar). Se carga solo en una BD de prueba desechable.
CREATE SCHEMA IF NOT EXISTS hub;

CREATE TABLE hub.tasks (
    id                text PRIMARY KEY CHECK (id ~ '^t_[0-9a-f]{8}$'),
    board             text NOT NULL,
    title             text NOT NULL,
    body              text,
    status            text NOT NULL CHECK (status IN ('triage', 'todo', 'scheduled', 'ready', 'running', 'blocked',
                                                      'review', 'done', 'archived')),
    assignee          text,
    priority          integer NOT NULL DEFAULT 0,
    brand             text,
    origin_chat       text,
    origin_thread     text,
    parents           text[] NOT NULL DEFAULT '{}',
    idempotency_key   text,
    lease_owner       text,
    lease_until       timestamptz,
    lease_ttl         integer,
    attempts          integer NOT NULL DEFAULT 0,
    created_by        bigint,          -- telegram_id de la persona (si la tarjeta la pidió una persona)
    created_by_worker text,            -- actor técnico (agent-lanes, auditor, default de Hermes...)
    alcance           text[] NOT NULL DEFAULT '{}',
    rama              text,
    result            text,
    block_kind        text CHECK (block_kind IS NULL OR block_kind IN ('dependency', 'needs_input', 'capability',
                                                                       'transient')),
    block_recurrences integer NOT NULL DEFAULT 0,
    current_run_id    bigint,
    started_at        timestamptz,
    completed_at      timestamptz,
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now(),
    extra             jsonb NOT NULL DEFAULT '{}'::jsonb   -- columnas propias de Hermes (ida y vuelta, F2-03)
);
CREATE INDEX tasks_board_status ON hub.tasks (board, status);
CREATE INDEX tasks_assignee_status ON hub.tasks (board, assignee, status);
CREATE INDEX tasks_parents ON hub.tasks USING gin (parents);
CREATE INDEX tasks_idem ON hub.tasks (board, idempotency_key) WHERE idempotency_key IS NOT NULL;

CREATE TABLE hub.task_runs (
    id          bigserial PRIMARY KEY,
    task_id     text NOT NULL REFERENCES hub.tasks (id),
    origen_id   bigint,                 -- id del run en el SQLite de Hermes (importado)
    profile     text,
    step_key    text,
    status      text NOT NULL,
    outcome     text,
    summary     text,
    metadata    jsonb,
    error       text,
    worker_pid  integer,
    lease_owner text,
    lease_until timestamptz,
    started_at  timestamptz NOT NULL,
    ended_at    timestamptz,
    extra       jsonb NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX runs_task ON hub.task_runs (task_id, started_at);

CREATE TABLE hub.task_events (
    id         bigserial PRIMARY KEY,
    task_id    text NOT NULL REFERENCES hub.tasks (id),
    origen_id  bigint,
    run_id     bigint,
    kind       text NOT NULL,
    payload    jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX events_task ON hub.task_events (task_id, created_at);

-- Solo se añade: nunca se cambia ni se borra un evento (F2-01: «task_events append-only»).
CREATE FUNCTION hub.task_events_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'hub.task_events es append-only';
END $$;
CREATE TRIGGER task_events_append_only BEFORE UPDATE OR DELETE ON hub.task_events
    FOR EACH ROW EXECUTE FUNCTION hub.task_events_append_only();

CREATE TABLE hub.task_comments (
    id         bigserial PRIMARY KEY,
    task_id    text NOT NULL REFERENCES hub.tasks (id),
    origen_id  bigint,
    author_id  bigint,                  -- telegram_id de una persona
    worker_id  text,                    -- o actor técnico (oscar-telegram, lane-review, default...)
    body       text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (author_id IS NOT NULL OR worker_id IS NOT NULL)
);
CREATE INDEX comments_task ON hub.task_comments (task_id, created_at);

CREATE TABLE hub.kv (
    key        text PRIMARY KEY,
    value      jsonb NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);
