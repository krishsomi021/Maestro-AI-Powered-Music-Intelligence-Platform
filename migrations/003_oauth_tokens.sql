-- Single-row table for Spotify OAuth tokens (Authorization Code flow).
-- Tokens live in Postgres only -- never in .env/source. Refresh updates in place.

CREATE TABLE IF NOT EXISTS spotify_oauth_tokens (
    id            SERIAL        PRIMARY KEY,
    access_token  TEXT          NOT NULL,
    refresh_token TEXT          NOT NULL,
    expires_at    TIMESTAMP     NOT NULL,
    updated_at    TIMESTAMP     DEFAULT now()
);
