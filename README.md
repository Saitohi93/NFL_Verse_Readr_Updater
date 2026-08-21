# NFL Verse Readr Updater

Reproducible nflverse-to-Turso data pipeline using the official [`nflreadr`](https://nflreadr.nflverse.com/) R package.

## Installed package

- Package: `nflreadr`
- Version: `1.5.1`
- R version in GitHub Actions: `4.4.3`
- Source: stable CRAN release documented by nflverse

The version is pinned so an upstream package release cannot silently change the updater.

## 2025 player-week dataset

The first locked dataset is the complete 2025 weekly player-stat release:

- Expected rows: **19,422**
- Primary key: `(game_id, player_id)`
- Source: `nflreadr::load_player_stats(2025, summary_level = "week")`
- Destination table: `nflreadr_player_weekly`

Rows without `player_id` are excluded to keep the ingestion path simple and reliable. Every field from the remaining identified-player rows is stored as a queryable Turso column, and each original row is also retained in `raw_json`.

## Automated validation

The `Verify nflreadr` workflow:

1. Installs the pinned package and runtime dependencies.
2. Verifies nflverse data access.
3. Runs Python loader unit tests.
4. Verifies the complete 19,422-row source and excludes rows missing `player_id`.
5. Performs a complete load into temporary SQLite.
6. Confirms unique keys, row counts, and statistical totals.

## Turso upload

The manual `Upload player-weekly data to Turso` workflow:

1. Downloads the locked nflreadr source.
2. Creates or evolves the Turso schema.
3. Upserts rows in retry-safe batches.
4. Removes stale season rows only after the upload completes.
5. Validates stored row counts and totals for attempts, carries, targets, passing yards, rushing yards, and receiving yards.
6. Writes a successful run to `nflreadr_update_log`.

Required GitHub Actions secrets:

- `NFLREADR_TURSO_DATABASE_URL`
- `NFLREADR_TURSO_AUTH_TOKEN`

Database setup and first-run instructions are in [`docs/turso-setup.md`](docs/turso-setup.md).

## Local validation

```bash
Rscript scripts/install_nflreadr.R
Rscript scripts/verify_nflreadr.R
Rscript scripts/export_player_weekly.R
python scripts/load_player_weekly.py \
  --source artifacts/nflreadr_player_weekly_2025.csv \
  --backend local
```

No scheduled Turso writes are enabled until the first secret-backed manual load passes.
