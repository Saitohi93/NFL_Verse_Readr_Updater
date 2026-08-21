import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class LoaderTest(unittest.TestCase):
    def test_local_ingestion_and_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "validation.sqlite"
            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "load_player_weekly.py"),
                    "--source",
                    str(ROOT / "tests" / "fixtures" / "player_weekly_sample.csv"),
                    "--backend",
                    "local",
                    "--local-db",
                    str(database_path),
                    "--expected-rows",
                    "2",
                    "--batch-size",
                    "1",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertIn("Validation passed", result.stdout)

            connection = sqlite3.connect(database_path)
            try:
                row_count = connection.execute(
                    "SELECT COUNT(*) FROM nflreadr_player_weekly"
                ).fetchone()[0]
                attempts = connection.execute(
                    "SELECT SUM(attempts) FROM nflreadr_player_weekly"
                ).fetchone()[0]
                audit_rows = connection.execute(
                    "SELECT COUNT(*) FROM nflreadr_update_log WHERE status='ok'"
                ).fetchone()[0]
            finally:
                connection.close()

            self.assertEqual(row_count, 2)
            self.assertEqual(attempts, 30)
            self.assertEqual(audit_rows, 1)


if __name__ == "__main__":
    unittest.main()
