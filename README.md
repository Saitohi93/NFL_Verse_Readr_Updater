# NFL Verse Readr Updater

Reproducible nflverse data-access project using the official [`nflreadr`](https://nflreadr.nflverse.com/) R package.

## Installed package

- Package: `nflreadr`
- Version: `1.5.1`
- R version in GitHub Actions: `4.4.3`
- Source: stable CRAN release documented by nflverse

The version is pinned so an upstream package release cannot silently change the updater.

## GitHub Actions verification

The `Verify nflreadr` workflow:

1. Sets up R.
2. Installs the pinned `nflreadr` version.
3. Confirms the required loading functions exist.
4. Downloads the official current-team dataset.
5. Downloads 2025 weekly player statistics.
6. Validates team counts, row counts, season values and required columns.

Run it manually from the Actions tab. It also runs for pull requests and pushes to `main`.

## Local verification

```bash
Rscript scripts/install_nflreadr.R
Rscript scripts/verify_nflreadr.R
```

## Files

- `scripts/install_nflreadr.R` — pinned package installer
- `scripts/verify_nflreadr.R` — package and data-access smoke tests
- `.github/workflows/verify-nflreadr.yml` — reproducible GitHub Actions environment

This setup verifies nflverse access only. Turso schemas and upload jobs should be added separately after the source datasets and retention rules are selected.
