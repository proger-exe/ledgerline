#!/bin/sh
# Read-only role for the signer: it re-checks payments in the DB but can never write.
set -e
psql -v ON_ERROR_STOP=1 -v pw="$SIGNER_DB_PASSWORD" --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<'SQL'
CREATE ROLE signer LOGIN PASSWORD :'pw';
SQL
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<SQL
GRANT CONNECT ON DATABASE "$POSTGRES_DB" TO signer;
GRANT USAGE ON SCHEMA public TO signer;
ALTER DEFAULT PRIVILEGES FOR ROLE "$POSTGRES_USER" IN SCHEMA public GRANT SELECT ON TABLES TO signer;
SQL
