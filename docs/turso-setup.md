# Turso setup

The uploader expects a dedicated Turso database for nflreadr-derived data. Do not reuse the MLB database.

## Required repository secrets

Add these GitHub Actions secrets to `Saitohi93/NFL_Verse_Readr_Updater`:

- `NFLREADR_TURSO_DATABASE_URL` — the database URL in `libsql://...` form
- `NFLREADR_TURSO_AUTH_TOKEN` — a token with write access to that database

The uploader never prints either value.

## First load

After the setup pull request is merged and both secrets are present:

1. Open the repository's **Actions** tab.
2. Choose **Upload player-weekly data to Turso**.
3. Select **Run workflow**. The first workflow is locked to the validated 2025 dataset.

The workflow creates the schema, upserts every row, removes stale rows for the selected season only after a complete upload, validates the stored row count and statistical totals, and writes an audit record.

## Tables

### `nflreadr_player_weekly`

One row per identified `(game_id, player_id)`. Source rows without `player_id` are excluded before upload. Every column delivered by `nflreadr::load_player_stats(..., summary_level = "week")` is stored as a queryable SQLite column. The table also stores:

- `raw_json` — lossless source-row representation
- `source_updated_at` — identifier for the successful refresh

Indexes support season/week, player history, and team/week queries.

### `nflreadr_update_log`

One audit row per successful upload with source rows, stored rows, season, timestamp, status, and validation details.

## Safety rules

- The complete 2025 source must contain exactly 19,422 rows before filtering.
- The 22 rows without `player_id` are counted and excluded.
- Exactly 19,400 identified-player rows must reach the uploader.
- Blank game IDs or duplicate `(game_id, player_id)` keys stop the upload.
- Upserts make retries safe.
- Old rows are retained if a run fails before completion.
- Passing attempts, carries, targets, and passing/rushing/receiving yard totals must match the source.
- No scheduled Turso writes are enabled until the first manual load passes.
