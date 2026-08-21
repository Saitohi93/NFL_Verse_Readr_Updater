import argparse
import csv
import json
import math
import os
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


TABLE = "nflreadr_player_weekly"
AUDIT_TABLE = "nflreadr_update_log"
KEY_COLUMNS = ("game_id", "player_id")
INTEGER_RE = re.compile(r"^-?\d+$")
METRIC_COLUMNS = (
    "attempts",
    "carries",
    "targets",
    "passing_yards",
    "rushing_yards",
    "receiving_yards",
)


def quote_identifier(value):
    return '"' + str(value).replace('"', '""') + '"'


def sql_text(value):
    return "'" + str(value).replace("\x00", "").replace("'", "''") + "'"


def infer_type(values):
    present = [value for value in values if value not in (None, "")]
    if not present:
        return "TEXT"
    if all(INTEGER_RE.fullmatch(value) for value in present):
        return "INTEGER"
    try:
        numbers = [float(value) for value in present]
    except ValueError:
        return "TEXT"
    return "REAL" if all(math.isfinite(value) for value in numbers) else "TEXT"


def sql_value(value, sqlite_type):
    if value in (None, ""):
        return "NULL"
    if sqlite_type == "INTEGER":
        return str(int(value))
    if sqlite_type == "REAL":
        number = float(value)
        if not math.isfinite(number):
            return sql_text(value)
        return repr(number)
    return sql_text(value)


def read_source(path):
    with open(path, newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
        columns = list(reader.fieldnames or [])
    if not rows:
        raise RuntimeError("Source CSV contains zero rows.")
    missing = [column for column in KEY_COLUMNS + ("season",) if column not in columns]
    if missing:
        raise RuntimeError(f"Source CSV is missing required columns: {', '.join(missing)}")
    keys = [(row["game_id"], row["player_id"]) for row in rows]
    if any(not game_id or not player_id for game_id, player_id in keys):
        raise RuntimeError("Source CSV contains blank game_id or player_id values.")
    if len(set(keys)) != len(keys):
        raise RuntimeError("Source CSV contains duplicate (game_id, player_id) keys.")
    types = {
        column: infer_type([row.get(column, "") for row in rows])
        for column in columns
    }
    return columns, types, rows


def source_metrics(rows):
    season_values = {int(row["season"]) for row in rows if row.get("season")}
    if len(season_values) != 1:
        raise RuntimeError(f"Expected exactly one source season, found {season_values}.")
    metrics = {"season": next(iter(season_values)), "row_count": len(rows)}
    for column in METRIC_COLUMNS:
        metrics[column] = sum(float(row.get(column) or 0) for row in rows)
    return metrics


def create_table_sql(columns, types):
    definitions = [
        f"{quote_identifier(column)} {types[column]}"
        for column in columns
    ]
    definitions.extend([
        '"raw_json" TEXT NOT NULL',
        '"source_updated_at" TEXT NOT NULL',
        'PRIMARY KEY ("game_id", "player_id")',
    ])
    return f"CREATE TABLE IF NOT EXISTS {quote_identifier(TABLE)} ({', '.join(definitions)});"


def create_audit_sql():
    return f"""
    CREATE TABLE IF NOT EXISTS {quote_identifier(AUDIT_TABLE)} (
        update_id INTEGER PRIMARY KEY AUTOINCREMENT,
        source_id TEXT NOT NULL,
        season INTEGER NOT NULL,
        source_rows INTEGER NOT NULL,
        stored_rows INTEGER NOT NULL,
        status TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        detail_json TEXT NOT NULL
    );
    """


def index_sql():
    return [
        f"CREATE INDEX IF NOT EXISTS idx_nflreadr_weekly_season_week "
        f"ON {quote_identifier(TABLE)} (season, week);",
        f"CREATE INDEX IF NOT EXISTS idx_nflreadr_weekly_player "
        f"ON {quote_identifier(TABLE)} (player_id, season, week);",
        f"CREATE INDEX IF NOT EXISTS idx_nflreadr_weekly_team "
        f"ON {quote_identifier(TABLE)} (team, season, week);",
    ]


def insert_sql(row, columns, types, run_id):
    stored_columns = columns + ["raw_json", "source_updated_at"]
    values = [sql_value(row.get(column), types[column]) for column in columns]
    values.extend([
        sql_text(json.dumps(row, separators=(",", ":"), ensure_ascii=False)),
        sql_text(run_id),
    ])
    update_columns = [column for column in stored_columns if column not in KEY_COLUMNS]
    assignments = ", ".join(
        f"{quote_identifier(column)}=excluded.{quote_identifier(column)}"
        for column in update_columns
    )
    return (
        f"INSERT INTO {quote_identifier(TABLE)} "
        f"({', '.join(quote_identifier(column) for column in stored_columns)}) "
        f"VALUES ({', '.join(values)}) "
        f"ON CONFLICT ({', '.join(quote_identifier(column) for column in KEY_COLUMNS)}) "
        f"DO UPDATE SET {assignments};"
    )


class LocalDatabase:
    def __init__(self, path):
        self.connection = sqlite3.connect(path)

    def execute_batch(self, statements):
        with self.connection:
            for statement in statements:
                self.connection.execute(statement)

    def query(self, statement):
        cursor = self.connection.execute(statement)
        columns = [column[0] for column in cursor.description or []]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]

    def table_columns(self):
        return {
            row["name"]
            for row in self.query(f"PRAGMA table_info({quote_identifier(TABLE)});")
        }

    def close(self):
        self.connection.close()


class TursoDatabase:
    def __init__(self, url, token):
        self.url = url.replace("libsql://", "https://").rstrip("/")
        self.token = token

    def _pipeline(self, statements):
        payload = {
            "requests": [
                {"type": "execute", "stmt": {"sql": statement}}
                for statement in statements
            ] + [{"type": "close"}]
        }
        last_error = None
        for attempt in range(4):
            try:
                request = urllib.request.Request(
                    self.url + "/v2/pipeline",
                    data=json.dumps(payload).encode(),
                    headers={
                        "Authorization": f"Bearer {self.token}",
                        "Content-Type": "application/json",
                        "User-Agent": "nfl-verse-readr-updater/1.0",
                    },
                    method="POST",
                )
                with urllib.request.urlopen(request, timeout=180) as response:
                    result = json.loads(response.read().decode())
                for item in result.get("results", []):
                    if item.get("type") == "error":
                        raise RuntimeError(str(item.get("error")))
                return result
            except (urllib.error.URLError, TimeoutError, RuntimeError) as error:
                last_error = error
                if attempt < 3:
                    time.sleep(2 ** attempt)
        raise RuntimeError(f"Turso pipeline failed after retries: {last_error}")

    def execute_batch(self, statements):
        self._pipeline(statements)

    def query(self, statement):
        result = self._pipeline([statement])
        for item in result.get("results", []):
            if item.get("type") != "ok":
                continue
            response = item.get("response", {})
            if response.get("type") != "execute":
                continue
            query_result = response.get("result", {})
            columns = [column.get("name") for column in query_result.get("cols", [])]
            rows = []
            for raw_row in query_result.get("rows", []):
                values = [
                    value.get("value") if isinstance(value, dict) else value
                    for value in raw_row
                ]
                rows.append(dict(zip(columns, values)))
            return rows
        return []

    def table_columns(self):
        return {
            row["name"]
            for row in self.query(f"PRAGMA table_info({quote_identifier(TABLE)});")
        }

    def close(self):
        return None


def add_missing_columns(database, columns, types):
    existing = database.table_columns()
    statements = []
    for column in columns:
        if column not in existing:
            statements.append(
                f"ALTER TABLE {quote_identifier(TABLE)} ADD COLUMN "
                f"{quote_identifier(column)} {types[column]};"
            )
    if statements:
        database.execute_batch(statements)


def scalar(database, statement):
    rows = database.query(statement)
    if not rows:
        return 0
    return next(iter(rows[0].values()), 0)


def validate_database(database, metrics):
    season = metrics["season"]
    stored_rows = int(float(scalar(
        database,
        f"SELECT COUNT(*) AS rows FROM {quote_identifier(TABLE)} WHERE season={season};",
    ) or 0))
    if stored_rows != metrics["row_count"]:
        raise RuntimeError(
            f"Stored row count {stored_rows} does not match source {metrics['row_count']}."
        )

    duplicate_groups = int(float(scalar(
        database,
        f"SELECT COUNT(*) FROM ("
        f"SELECT game_id, player_id, COUNT(*) AS n FROM {quote_identifier(TABLE)} "
        f"WHERE season={season} GROUP BY game_id, player_id HAVING n > 1);",
    ) or 0))
    if duplicate_groups:
        raise RuntimeError(f"Stored data contains {duplicate_groups} duplicate key groups.")

    for column in METRIC_COLUMNS:
        stored_total = float(scalar(
            database,
            f"SELECT COALESCE(SUM({quote_identifier(column)}),0) "
            f"FROM {quote_identifier(TABLE)} WHERE season={season};",
        ) or 0)
        if not math.isclose(stored_total, metrics[column], rel_tol=0, abs_tol=1e-6):
            raise RuntimeError(
                f"Stored {column} total {stored_total} does not match source "
                f"{metrics[column]}."
            )
    return stored_rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--backend", choices=("local", "turso"), default="local")
    parser.add_argument("--local-db", default="artifacts/nflreadr_validation.sqlite")
    parser.add_argument("--batch-size", type=int, default=50)
    parser.add_argument("--expected-rows", type=int)
    args = parser.parse_args()

    columns, types, rows = read_source(args.source)
    metrics = source_metrics(rows)
    if args.expected_rows is not None and metrics["row_count"] != args.expected_rows:
        raise RuntimeError(
            f"Expected {args.expected_rows} source rows, found {metrics['row_count']}."
        )
    if args.batch_size < 1 or args.batch_size > 100:
        raise RuntimeError("Batch size must be between 1 and 100.")

    run_id = datetime.now(timezone.utc).isoformat()
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
        local_path.parent.mkdir(parents=True, exist_ok=True)
        if local_path.exists():
            local_path.unlink()
        database = LocalDatabase(local_path)

    try:
        database.execute_batch([create_table_sql(columns, types), create_audit_sql()])
        add_missing_columns(database, columns, types)
        database.execute_batch(index_sql())

        statements = []
        for index, row in enumerate(rows, start=1):
            statements.append(insert_sql(row, columns, types, run_id))
            if len(statements) >= args.batch_size:
                database.execute_batch(statements)
                statements = []
                print(f"Upserted {index:,}/{len(rows):,} rows")
        if statements:
            database.execute_batch(statements)

        season = metrics["season"]
        database.execute_batch([
            f"DELETE FROM {quote_identifier(TABLE)} WHERE season={season} "
            f"AND source_updated_at <> {sql_text(run_id)};"
        ])
        stored_rows = validate_database(database, metrics)

        detail = json.dumps(
            {
                "columns": len(columns),
                "metrics": metrics,
                "backend": args.backend,
            },
            separators=(",", ":"),
        )
        audit = (
            f"INSERT INTO {quote_identifier(AUDIT_TABLE)} "
            "(source_id,season,source_rows,stored_rows,status,updated_at,detail_json) "
            f"VALUES ('nflreadr_player_weekly',{season},{len(rows)},{stored_rows},"
            f"'ok',{sql_text(run_id)},{sql_text(detail)});"
        )
        database.execute_batch([audit])
        print(
            f"Validation passed: backend={args.backend}, season={season}, "
            f"rows={stored_rows:,}, columns={len(columns)}"
        )
    finally:
        database.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"ERROR: {error}", file=sys.stderr)
        raise
