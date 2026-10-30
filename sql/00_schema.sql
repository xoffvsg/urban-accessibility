-- One schema per city slug. Usage: psql -v schema=<slug> -f sql/00_schema.sql
CREATE SCHEMA IF NOT EXISTS :"schema";
