#!/bin/sh
set -eu
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" -v app_password="$DB_APP_PASSWORD" <<'SQL'
CREATE ROLE orgproekt_app LOGIN PASSWORD :'app_password';
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO orgproekt_app;
SQL
