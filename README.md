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
- Excluded rows without `player_id`: **22**
- Turso-ready identified-player rows: **19,400**
- Primary key: `(game_id, player_id)`
- Source: `nflreadr::load_player_stats(2025, summary_level = "week")`
- Destination table: `nflreadr_player_weekly`

Rows without `player_id` are excluded to keep the ingestion path simple and reliable. Every field from the remaining identified-player rows is stored as a queryable Turso column, and each original row is also retained in `raw_json`.

## 2025 advanced weekly datasets

The updater also retrieves two nflverse datasets and maps them to the same GSIS `player_id` used by the player-week table:

- NFL Next Gen Stats receiving: **1,282** non-summary weekly rows from `nflreadr::load_nextgen_stats(2025, stat_type = "receiving")`
- PFR advanced passing: **684** weekly rows from `nflreadr::load_pfr_advstats(2025, stat_type = "pass", summary_level = "week")`
- NGS destination: `nflreadr_ngs_receiving_weekly`
- PFR destination: `nflreadr_pfr_passing_weekly`
- Combined query view: `nflreadr_player_weekly_enriched`

NGS supplies `avg_separation`, `avg_cushion`, intended air yards, and expected YAC fields. PFR supplies times blitzed, hurried, hit, pressured, sacked, and its source pressure percentage.

The enriched view keeps all 19,400 base player-week rows and adds nullable advanced fields. NGS only publishes receiving metrics for players who meet its weekly opportunity minimum. Passing rates use fixed definitions:

NGS labels the Super Bowl as postseason week 23 while the player-stat release labels it week 22. The source `week` is preserved and `player_stats_week` stores the normalized join value, preventing the seven Super Bowl receivers from being dropped from the enriched view.

```text
QB dropbacks = attempts + PFR times sacked
QB blitz rate = times blitzed / QB dropbacks
QB calculated pressure rate = times pressured / QB dropbacks
```

PFR's original `times_pressured_pct` is retained separately as `qb_pressure_rate_pfr` for source comparison.

## Automated validation

The `Verify nflreadr` workflow:

1. Installs the pinned package and runtime dependencies.
2. Verifies nflverse data access.
3. Runs Python loader unit tests.
4. Verifies the complete 19,422-row source and excludes the 22 rows missing `player_id`.
5. Verifies exactly 1,282 NGS receiving rows and 684 PFR advanced-passing rows.
6. Performs complete loads into temporary SQLite.
7. Confirms exactly 19,400 unique identified-player rows, matching statistical totals, and a one-to-one enriched view.

## Turso upload

The manual `Upload player-weekly data to Turso` workflow:

1. Downloads the locked nflreadr source.
2. Creates or evolves the Turso schema.
3. Upserts rows in retry-safe batches.
4. Removes stale season rows only after the upload completes.
5. Validates stored row counts and totals for attempts, carries, targets, passing yards, rushing yards, and receiving yards.
6. Loads and validates receiver separation, blitz, and pressure sources.
7. Creates `nflreadr_player_weekly_enriched` without changing the base-table row count.
8. Writes successful source runs to `nflreadr_update_log`.

Required GitHub Actions secrets:

- `NFLREADR_TURSO_DATABASE_URL`
- `NFLREADR_TURSO_AUTH_TOKEN`

Database setup and first-run instructions are in [`docs/turso-setup.md`](docs/turso-setup.md).

## Local validation

```bash
Rscript scripts/install_nflreadr.R
Rscript scripts/verify_nflreadr.R
Rscript scripts/export_player_weekly.R
Rscript scripts/export_advanced_weekly.R
python scripts/load_player_weekly.py \
  --source artifacts/nflreadr_player_weekly_2025.csv \
  --backend local
python scripts/load_advanced_weekly.py \
  --ngs-source artifacts/nflreadr_ngs_receiving_weekly_2025.csv \
  --pfr-source artifacts/nflreadr_pfr_passing_weekly_2025.csv \
  --backend local
```

No scheduled Turso writes are enabled until the first secret-backed manual load passes.
