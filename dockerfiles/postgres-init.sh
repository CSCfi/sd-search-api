#!/bin/sh
# Creates the two database users and the tables. Run once by the postgres image's
# initdb as the superuser. The tables are created by the admin user, who owns them,
# and grant.sql gives the api user access to the tables. The user names below are
# the defaults in search_api/conf.py, which this image cannot read.
set -e

POSTGRES_ADMIN_USER=sd_search_admin
POSTGRES_API_USER=sd_search_api

psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
    -v admin_user="$POSTGRES_ADMIN_USER" \
    -v admin_password="$POSTGRES_ADMIN_PASSWORD" \
    -v api_user="$POSTGRES_API_USER" \
    -v api_password="$POSTGRES_API_PASSWORD" <<'SQL'
CREATE ROLE :"admin_user" LOGIN PASSWORD :'admin_password';
CREATE ROLE :"api_user" LOGIN PASSWORD :'api_password';
GRANT USAGE, CREATE ON SCHEMA public TO :"admin_user";
SQL

psql -v ON_ERROR_STOP=1 -U "$POSTGRES_ADMIN_USER" -d "$POSTGRES_DB" \
    -v api_user="$POSTGRES_API_USER" -f /schema/create.sql -f /schema/grant.sql
