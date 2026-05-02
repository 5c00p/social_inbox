#!/bin/bash
set -e

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    CREATE DATABASE social_inbox_test;
    GRANT ALL PRIVILEGES ON DATABASE social_inbox_test TO $POSTGRES_USER;
EOSQL
