import argparse
import csv
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from load_player_weekly import (
    LocalDatabase,
    TursoDatabase,
    create_audit_sql,
    infer_type,
    quote_identifier,
    sql_text,
    sql_value,
)


NGS_TABLE = "nflreadr_ngs_receiving_weekly"
PFR_TABLE = "nflreadr_pfr_passing_weekly"
PFR_RUSH_TABLE = "nflreadr_pfr_rushing_weekly"
PLAYER_TABLE = "nflreadr_player_weekly"
ENRICHED_VIEW = "nflreadr_player_weekly_enriched"
AUDIT_TABLE = "nflreadr_update_log"


def read_source(path, key_columns):
    with open(path, newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
        columns = list(reader.fieldnames or [])
    if not rows:
        raise RuntimeError(f"{path} contains zero rows.")
    missing = [column for column in ("season", *key_columns) if column not in columns]
    if missing:
        raise RuntimeError(f"{path} is missing columns: {', '.join(missing)}")
    keys = [tuple(row.get(column, "") for column in key_columns) for row in rows]
    if any(any(not value for value in key) for key in keys):
        raise RuntimeError(f"{path} contains a blank primary-key value.")
    if len(set(keys)) != len(keys):
        raise RuntimeError(f"{path} contains duplicate primary keys.")
    types = {
        column: infer_type([row.get(column, "") for row in rows])
        for column in columns
    }
    seasons = {int(row["season"]) for row in rows if row.get("season")}
    if len(seasons) != 1:
        raise RuntimeError(f"{path} must contain exactly one season; found {seasons}.")
    return columns, types, rows, next(iter(seasons))


def create_table_sql(table, columns, types, key_columns):
    definitions = [
        f"{quote_identifier(column)} {types[column]}"
        for column in columns
    ]
    definitions.extend(
        [
            '"raw_json" TEXT NOT NULL',
            '"source_updated_at" TEXT NOT NULL',
            f"PRIMARY KEY ({', '.join(quote_identifier(column) for column in key_columns)})",
        ]
    )
    return (
        f"CREATE TABLE IF NOT EXISTS {quote_identifier(table)} "
        f"({', '.join(definitions)});"
    )


def table_columns(database, table):
    return {
        row["name"]
        for row in database.query(f"PRAGMA table_info({quote_identifier(table)});")
    }


def add_missing_columns(database, table, columns, types):
    existing = table_columns(database, table)
    statements = []
    for column in columns:
        if column not in existing:
            statements.append(
                f"ALTER TABLE {quote_identifier(table)} ADD COLUMN "
                f"{quote_identifier(column)} {types[column]};"
            )
    if statements:
        database.execute_batch(statements)


def insert_sql(table, row, columns, types, key_columns, run_id):
    stored_columns = columns + ["raw_json", "source_updated_at"]
    values = [sql_value(row.get(column), types[column]) for column in columns]
    values.extend(
        [
            sql_text(json.dumps(row, separators=(",", ":"), ensure_ascii=False)),
            sql_text(run_id),
        ]
    )
    update_columns = [column for column in stored_columns if column not in key_columns]
    assignments = ", ".join(
        f"{quote_identifier(column)}=excluded.{quote_identifier(column)}"
        for column in update_columns
    )
    return (
        f"INSERT INTO {quote_identifier(table)} "
        f"({', '.join(quote_identifier(column) for column in stored_columns)}) "
        f"VALUES ({', '.join(values)}) "
        f"ON CONFLICT ({', '.join(quote_identifier(column) for column in key_columns)}) "
        f"DO UPDATE SET {assignments};"
    )


def scalar(database, statement):
    rows = database.query(statement)
    if not rows:
        return 0
    return next(iter(rows[0].values()), 0)


def load_dataset(
    database,
    source,
    table,
    key_columns,
    required_metrics,
    expected_rows,
    source_id,
    batch_size,
    run_id,
):
    columns, types, rows, season = read_source(source, key_columns)
    missing_metrics = [column for column in required_metrics if column not in columns]
    if missing_metrics:
        raise RuntimeError(f"{source} is missing metrics: {', '.join(missing_metrics)}")
    if expected_rows is not None and len(rows) != expected_rows:
        raise RuntimeError(
            f"{source_id} expected {expected_rows} rows but found {len(rows)}."
        )
    for column in required_metrics:
        if any(row.get(column, "") == "" for row in rows):
            raise RuntimeError(f"{source_id} contains missing {column} values.")

    database.execute_batch(
        [create_table_sql(table, columns, types, key_columns), create_audit_sql()]
    )
    add_missing_columns(database, table, columns, types)

    statements = []
    for index, row in enumerate(rows, start=1):
        statements.append(
            insert_sql(table, row, columns, types, key_columns, run_id)
        )
        if len(statements) >= batch_size:
            database.execute_batch(statements)
            statements = []
            print(f"{source_id}: upserted {index:,}/{len(rows):,} rows")
    if statements:
        database.execute_batch(statements)

    database.execute_batch(
        [
            f"DELETE FROM {quote_identifier(table)} WHERE season={season} "
            f"AND source_updated_at <> {sql_text(run_id)};"
        ]
    )
    stored_rows = int(
        float(
            scalar(
                database,
                f"SELECT COUNT(*) FROM {quote_identifier(table)} WHERE season={season};",
            )
            or 0
        )
    )
    if stored_rows != len(rows):
        raise RuntimeError(
            f"{source_id} stored {stored_rows} rows but source contains {len(rows)}."
        )

    detail = json.dumps(
        {
            "columns": len(columns),
            "required_metrics": list(required_metrics),
            "backend_rows": stored_rows,
        },
        separators=(",", ":"),
    )
    database.execute_batch(
        [
            f"INSERT INTO {quote_identifier(AUDIT_TABLE)} "
            "(source_id,season,source_rows,stored_rows,status,updated_at,detail_json) "
            f"VALUES ({sql_text(source_id)},{season},{len(rows)},{stored_rows},"
            f"'ok',{sql_text(run_id)},{sql_text(detail)});"
        ]
    )
    print(f"{source_id}: validation passed with {stored_rows:,} rows")
    return season, stored_rows


def create_enriched_view(database):
    if not table_columns(database, PLAYER_TABLE):
        raise RuntimeError(
            f"{PLAYER_TABLE} must exist before the enriched view can be created."
        )
    denominator = "(COALESCE(p.attempts, 0) + COALESCE(q.times_sacked, 0))"
    database.execute_batch(
        [
            f"DROP VIEW IF EXISTS {quote_identifier(ENRICHED_VIEW)};",
            f"""
            CREATE VIEW {quote_identifier(ENRICHED_VIEW)} AS
            SELECT
                p.*,
                n.avg_separation AS receiver_avg_separation,
                n.avg_cushion AS receiver_avg_cushion,
                n.avg_intended_air_yards AS receiver_avg_intended_air_yards,
                n.percent_share_of_intended_air_yards AS receiver_air_yards_share_pct,
                n.avg_yac AS receiver_avg_yac,
                n.avg_expected_yac AS receiver_avg_expected_yac,
                n.avg_yac_above_expectation AS receiver_avg_yac_above_expectation,
                q.times_sacked AS qb_pfr_times_sacked,
                q.times_blitzed AS qb_times_blitzed,
                CASE WHEN {denominator} > 0
                    THEN q.times_blitzed * 1.0 / {denominator}
                END AS qb_blitz_rate,
                q.times_hurried AS qb_times_hurried,
                q.times_hit AS qb_times_hit,
                q.times_pressured AS qb_times_pressured,
                CASE WHEN {denominator} > 0
                    THEN q.times_pressured * 1.0 / {denominator}
                END AS qb_pressure_rate_calculated,
                q.times_pressured_pct AS qb_pressure_rate_pfr,
                r.carries AS pfr_rush_carries,
                r.rushing_yards_before_contact AS pfr_rushing_yards_before_contact,
                r.rushing_yards_before_contact_avg AS pfr_rushing_yards_before_contact_avg,
                r.rushing_yards_after_contact AS pfr_rushing_yards_after_contact,
                r.rushing_yards_after_contact_avg AS pfr_rushing_yards_after_contact_avg,
                r.rushing_broken_tackles AS pfr_rushing_broken_tackles,
                r.receiving_broken_tackles AS pfr_receiving_broken_tackles
            FROM {quote_identifier(PLAYER_TABLE)} AS p
            LEFT JOIN {quote_identifier(NGS_TABLE)} AS n
                ON n.season = p.season
                AND n.season_type = p.season_type
                AND n.player_stats_week = p.week
                AND n.player_id = p.player_id
            LEFT JOIN {quote_identifier(PFR_TABLE)} AS q
                ON q.game_id = p.game_id
                AND q.player_id = p.player_id
            LEFT JOIN {quote_identifier(PFR_RUSH_TABLE)} AS r
                ON r.game_id = p.game_id
                AND r.player_id = p.player_id;
            """,
        ]
    )

    base_rows = int(float(scalar(database, f"SELECT COUNT(*) FROM {PLAYER_TABLE};") or 0))
    view_rows = int(float(scalar(database, f"SELECT COUNT(*) FROM {ENRICHED_VIEW};") or 0))
    if view_rows != base_rows:
        raise RuntimeError(
            f"Enriched view has {view_rows} rows but base table has {base_rows}."
        )
    separation_rows = int(
        float(
            scalar(
                database,
                f"SELECT COUNT(*) FROM {ENRICHED_VIEW} "
                "WHERE receiver_avg_separation IS NOT NULL;",
            )
            or 0
        )
    )
    pressure_rows = int(
        float(
            scalar(
                database,
                f"SELECT COUNT(*) FROM {ENRICHED_VIEW} "
                "WHERE qb_pressure_rate_pfr IS NOT NULL;",
            )
            or 0
        )
    )
    ngs_rows = int(float(scalar(database, f"SELECT COUNT(*) FROM {NGS_TABLE};") or 0))
    pfr_rows = int(float(scalar(database, f"SELECT COUNT(*) FROM {PFR_TABLE};") or 0))
    rushing_rows = int(
        float(
            scalar(
                database,
                f"SELECT COUNT(*) FROM {ENRICHED_VIEW} "
                "WHERE pfr_rushing_yards_before_contact IS NOT NULL;",
            )
            or 0
        )
    )
    pfr_rush_rows = int(
        float(scalar(database, f"SELECT COUNT(*) FROM {PFR_RUSH_TABLE};") or 0)
    )
    if (
        separation_rows != ngs_rows
        or pressure_rows != pfr_rows
        or rushing_rows != pfr_rush_rows
    ):
        raise RuntimeError(
            "Enriched view did not match every advanced source row: "
            f"separation={separation_rows}/{ngs_rows}, pressure={pressure_rows}/{pfr_rows}, "
            f"rushing={rushing_rows}/{pfr_rush_rows}."
        )
    print(
        f"Enriched view validation passed: rows={view_rows:,}, "
        f"separation_rows={separation_rows:,}, pressure_rows={pressure_rows:,}, "
        f"rushing_rows={rushing_rows:,}"
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ngs-source", required=True)
    parser.add_argument("--pfr-source", required=True)
    parser.add_argument("--pfr-rush-source", required=True)
    parser.add_argument("--backend", choices=("local", "turso"), default="local")
    parser.add_argument("--local-db", default="artifacts/nflreadr_validation.sqlite")
    parser.add_argument("--batch-size", type=int, default=50)
    parser.add_argument("--expected-ngs-rows", type=int)
    parser.add_argument("--expected-pfr-rows", type=int)
    parser.add_argument("--expected-pfr-rush-rows", type=int)
    args = parser.parse_args()
    if args.batch_size < 1 or args.batch_size > 100:
        raise RuntimeError("Batch size must be between 1 and 100.")

    if args.backend == "turso":
        url = os.environ.get("NFLREADR_TURSO_DATABASE_URL")
        token = os.environ.get("NFLREADR_TURSO_AUTH_TOKEN")
        if not url or not token:
            raise RuntimeError(
                "NFLREADR_TURSO_DATABASE_URL and NFLREADR_TURSO_AUTH_TOKEN are required."
            )
        database = TursoDatabase(url, token)
    else:
        local_path = Path(args.local_db)
        if not local_path.exists():
            raise RuntimeError(
                "Local validation database must already contain the player-week table."
            )
        database = LocalDatabase(local_path)

    run_id = datetime.now(timezone.utc).isoformat()
    try:
        ngs_season, _ = load_dataset(
            database=database,
            source=args.ngs_source,
            table=NGS_TABLE,
            key_columns=("season", "season_type", "week", "player_id"),
            required_metrics=("player_stats_week", "avg_separation"),
            expected_rows=args.expected_ngs_rows,
            source_id="nflreadr_ngs_receiving_weekly",
            batch_size=args.batch_size,
            run_id=run_id,
        )
        pfr_season, _ = load_dataset(
            database=database,
            source=args.pfr_source,
            table=PFR_TABLE,
            key_columns=("game_id", "player_id"),
            required_metrics=(
                "times_blitzed",
                "times_pressured",
                "times_pressured_pct",
            ),
            expected_rows=args.expected_pfr_rows,
            source_id="nflreadr_pfr_passing_weekly",
            batch_size=args.batch_size,
            run_id=run_id,
        )
        pfr_rush_season, _ = load_dataset(
            database=database,
            source=args.pfr_rush_source,
            table=PFR_RUSH_TABLE,
            key_columns=("game_id", "player_id"),
            required_metrics=(
                "carries",
                "rushing_yards_before_contact",
                "rushing_yards_before_contact_avg",
                "rushing_yards_after_contact",
                "rushing_yards_after_contact_avg",
                "rushing_broken_tackles",
            ),
            expected_rows=args.expected_pfr_rush_rows,
            source_id="nflreadr_pfr_rushing_weekly",
            batch_size=args.batch_size,
            run_id=run_id,
        )
        if len({ngs_season, pfr_season, pfr_rush_season}) != 1:
            raise RuntimeError(
                "Advanced sources disagree on season: "
                f"NGS={ngs_season}, PFR passing={pfr_season}, "
                f"PFR rushing={pfr_rush_season}."
            )
        database.execute_batch(
            [
                f"CREATE INDEX IF NOT EXISTS idx_ngs_receiving_player_week "
                f"ON {quote_identifier(NGS_TABLE)} (player_id, season, season_type, week);",
                f"CREATE INDEX IF NOT EXISTS idx_pfr_passing_player_game "
                f"ON {quote_identifier(PFR_TABLE)} (player_id, game_id);",
                f"CREATE INDEX IF NOT EXISTS idx_pfr_rushing_player_game "
                f"ON {quote_identifier(PFR_RUSH_TABLE)} (player_id, game_id);",
            ]
        )
        create_enriched_view(database)
    finally:
        database.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise
