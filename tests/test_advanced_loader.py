import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"


class AdvancedLoaderTest(unittest.TestCase):
    def test_advanced_sources_and_enriched_view(self):
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "validation.sqlite"
            subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "load_player_weekly.py"),
                    "--source",
                    str(FIXTURES / "player_weekly_sample.csv"),
                    "--backend",
                    "local",
                    "--local-db",
                    str(database_path),
                    "--expected-rows",
                    "2",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "load_advanced_weekly.py"),
                    "--ngs-source",
                    str(FIXTURES / "ngs_receiving_weekly_sample.csv"),
                    "--pfr-source",
                    str(FIXTURES / "pfr_passing_weekly_sample.csv"),
                    "--pfr-rush-source",
                    str(FIXTURES / "pfr_rushing_weekly_sample.csv"),
                    "--backend",
                    "local",
                    "--local-db",
                    str(database_path),
                    "--expected-ngs-rows",
                    "1",
                    "--expected-pfr-rows",
                    "1",
                    "--expected-pfr-rush-rows",
                    "1",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertIn("Enriched view validation passed", result.stdout)

            connection = sqlite3.connect(database_path)
            try:
                row_count = connection.execute(
                    "SELECT COUNT(*) FROM nflreadr_player_weekly_enriched"
                ).fetchone()[0]
                separation = connection.execute(
                    "SELECT receiver_avg_separation "
                    "FROM nflreadr_player_weekly_enriched WHERE player_id='00-0000002'"
                ).fetchone()[0]
                rates = connection.execute(
                    "SELECT qb_blitz_rate, qb_pressure_rate_calculated, qb_pressure_rate_pfr "
                    "FROM nflreadr_player_weekly_enriched WHERE player_id='00-0000001'"
                ).fetchone()
                rushing = connection.execute(
                    "SELECT pfr_rushing_yards_before_contact, "
                    "pfr_rushing_yards_after_contact, pfr_rushing_broken_tackles "
                    "FROM nflreadr_player_weekly_enriched WHERE player_id='00-0000002'"
                ).fetchone()
                advanced_audits = connection.execute(
                    "SELECT COUNT(*) FROM nflreadr_update_log "
                    "WHERE source_id IN "
                    "('nflreadr_ngs_receiving_weekly','nflreadr_pfr_passing_weekly',"
                    "'nflreadr_pfr_rushing_weekly')"
                ).fetchone()[0]
            finally:
                connection.close()

            self.assertEqual(row_count, 2)
            self.assertAlmostEqual(separation, 3.1)
            self.assertAlmostEqual(rates[0], 9 / 32)
            self.assertAlmostEqual(rates[1], 8 / 32)
            self.assertAlmostEqual(rates[2], 0.222)
            self.assertEqual(rushing, (40, 35, 3))
            self.assertEqual(advanced_audits, 3)


if __name__ == "__main__":
    unittest.main()
