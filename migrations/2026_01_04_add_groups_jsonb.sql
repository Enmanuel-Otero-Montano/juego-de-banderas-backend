-- Migration: Add groups JSONB column to career tables
-- Target: PostgreSQL
-- Description: Adds JSONB columns to stage_runs and stage_best for persisting attempt details.

ALTER TABLE stage_runs ADD COLUMN IF NOT EXISTS groups JSONB;
ALTER TABLE stage_best ADD COLUMN IF NOT EXISTS groups JSONB;

-- Note: If you are not using Postgres, replace JSONB with JSON or TEXT based on your DB.
