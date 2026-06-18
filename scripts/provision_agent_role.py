"""
Provision the agent_readonly PostgreSQL role (idempotent).

Run after every docker compose down -v because the role is cluster-level
and is not recreated by the schema migrations.

Usage:
    docker compose exec fastapi python scripts/provision_agent_role.py

Grants applied:
    CONNECT  on database {postgres_db}
    USAGE    on schema public
    SELECT   on listening_history
    SELECT   on track_metadata

Explicit REVOKE applied (defense-in-depth, idempotent no-op if no grant existed):
    REVOKE ALL on spotify_oauth_tokens

Deliberately NO grant on: jobs, agent_sessions, agent_messages,
agent_tool_calls, eval_runs, spotify_oauth_tokens — the agent reads those
through the trusted admin connection, not through the read-only role.
spotify_oauth_tokens holds live Spotify access/refresh tokens and must never
be reachable by the agent's run_sql_query tool, so its REVOKE is explicit
rather than relying on the absence of a grant.
"""

import asyncio
import sys

sys.path.insert(0, ".")

import asyncpg
from app.config import settings


async def provision() -> None:
    role = settings.agent_readonly_db_user
    password = settings.agent_readonly_db_password
    db = settings.postgres_db

    if not password:
        print(
            "[provision] ERROR: AGENT_READONLY_DB_PASSWORD is not set. "
            "Add it to .env and retry.",
            file=sys.stderr,
        )
        sys.exit(1)

    dsn = (
        f"postgresql://{settings.postgres_user}:{settings.postgres_password}"
        f"@{settings.postgres_host}:{settings.postgres_port}/{db}"
    )
    conn = await asyncpg.connect(dsn)
    try:
        # Escape single quotes in password for the DO $$ block
        safe_password = password.replace("'", "''")

        # Idempotent role creation / password update
        await conn.execute(f"""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT FROM pg_catalog.pg_roles WHERE rolname = '{role}'
                ) THEN
                    CREATE ROLE "{role}" LOGIN PASSWORD '{safe_password}';
                    RAISE NOTICE 'Role "{role}" created.';
                ELSE
                    ALTER ROLE "{role}" WITH PASSWORD '{safe_password}';
                    RAISE NOTICE 'Role "{role}" already exists — password updated.';
                END IF;
            END
            $$;
        """)

        # Grants (all idempotent — re-running is a no-op)
        await conn.execute(f'GRANT CONNECT ON DATABASE "{db}" TO "{role}";')
        await conn.execute(f'GRANT USAGE ON SCHEMA public TO "{role}";')
        await conn.execute(
            f'GRANT SELECT ON listening_history, track_metadata TO "{role}";'
        )

        # spotify_oauth_tokens may not exist yet (e.g. fresh clone, or a
        # `down -v` not yet followed by `up -d` to apply migration 003) --
        # check before revoking so this script stays safe to run at any point.
        table_exists = await conn.fetchval(
            """
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name = 'spotify_oauth_tokens'
            """
        )
        if table_exists:
            await conn.execute(f'REVOKE ALL ON spotify_oauth_tokens FROM "{role}";')
            print(f"[provision] REVOKE ALL on spotify_oauth_tokens from '{role}': done")
        else:
            print(
                "[provision] spotify_oauth_tokens not found — skipping REVOKE "
                "(migration 003 not yet applied)"
            )

        print(f"[provision] Role '{role}' provisioned successfully.")
        print(f"[provision] Grants:")
        print(f"            CONNECT on database '{db}'")
        print(f"            USAGE on schema public")
        print(f"            SELECT on listening_history, track_metadata")
        print(f"[provision] No grant on: jobs, agent_*, eval_runs, spotify_oauth_tokens (by design).")

    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(provision())
