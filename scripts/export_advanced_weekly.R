options(
  repos = c(CRAN = "https://cloud.r-project.org"),
  nflreadr.cache = "filesystem",
  nflreadr.prefer = "csv",
  nflreadr.verbose = TRUE
)

library(nflreadr)

season <- as.integer(Sys.getenv("NFLREADR_SEASON", "2025"))
output_dir <- Sys.getenv("NFLREADR_OUTPUT_DIR", "artifacts")
dir.create(output_dir, recursive = TRUE, showWarnings = FALSE)

expected_rows <- function(name, fallback) {
  text <- Sys.getenv(name, if (season == 2025) as.character(fallback) else "")
  if (nzchar(text)) as.integer(text) else NA_integer_
}

validate_rows <- function(data, expected, label) {
  if (nrow(data) == 0) {
    stop(label, " returned zero rows.", call. = FALSE)
  }
  if (!is.na(expected) && nrow(data) != expected) {
    stop(
      label, " expected ", expected, " rows for ", season,
      " but returned ", nrow(data), ".",
      call. = FALSE
    )
  }
}

require_columns <- function(data, required, label) {
  missing <- setdiff(required, names(data))
  if (length(missing) > 0) {
    stop(label, " is missing columns: ", paste(missing, collapse = ", "), call. = FALSE)
  }
}

assert_unique <- function(data, columns, label) {
  key <- do.call(paste, c(data[columns], sep = "|"))
  if (anyDuplicated(key)) {
    stop(label, " contains duplicate keys.", call. = FALSE)
  }
}

ngs_source <- load_nextgen_stats(
  seasons = season,
  stat_type = "receiving",
  file_type = "rds"
)
require_columns(
  ngs_source,
  c(
    "season", "season_type", "week", "player_gsis_id", "player_display_name",
    "team_abbr", "avg_separation", "avg_cushion", "avg_intended_air_yards"
  ),
  "NGS receiving source"
)
ngs <- ngs_source[
  ngs_source$season == season & !is.na(ngs_source$week) & ngs_source$week > 0,
  ,
  drop = FALSE
]
ngs$player_id <- ngs$player_gsis_id
# NGS labels the Super Bowl as week 23; nflverse player stats label it week 22.
# Preserve the source week and add the normalized join week explicitly.
ngs$player_stats_week <- ifelse(
  ngs$season_type == "POST" & ngs$week == 23,
  22,
  ngs$week
)
identified_ngs <- !is.na(ngs$player_id) & ngs$player_id != ""
excluded_ngs <- sum(!identified_ngs)
ngs <- as.data.frame(ngs[identified_ngs, , drop = FALSE])
validate_rows(
  ngs,
  expected_rows("NFLREADR_EXPECTED_NGS_ROWS", 1282),
  "NGS receiving weekly source"
)
if (any(is.na(ngs$avg_separation))) {
  stop("NGS receiving weekly source contains missing avg_separation values.", call. = FALSE)
}
assert_unique(ngs, c("season", "season_type", "week", "player_id"), "NGS receiving")
ngs <- ngs[order(ngs$season_type, ngs$week, ngs$team_abbr, ngs$player_id), , drop = FALSE]

pfr_source <- load_pfr_advstats(
  seasons = season,
  stat_type = "pass",
  summary_level = "week",
  file_type = "csv"
)
require_columns(
  pfr_source,
  c(
    "game_id", "season", "week", "game_type", "team", "opponent",
    "pfr_player_id", "pfr_player_name", "times_sacked", "times_blitzed",
    "times_hurried", "times_hit", "times_pressured", "times_pressured_pct"
  ),
  "PFR advanced passing source"
)

players <- as.data.frame(load_players(file_type = "csv"))
require_columns(players, c("gsis_id", "pfr_id"), "nflverse players source")
player_map <- unique(players[c("pfr_id", "gsis_id")])
pfr <- merge(
  pfr_source,
  player_map,
  by.x = "pfr_player_id",
  by.y = "pfr_id",
  all.x = TRUE,
  sort = FALSE
)
pfr$player_id <- pfr$gsis_id
identified_pfr <- !is.na(pfr$player_id) & pfr$player_id != ""
excluded_pfr <- sum(!identified_pfr)
pfr <- as.data.frame(pfr[identified_pfr, , drop = FALSE])
validate_rows(
  pfr,
  expected_rows("NFLREADR_EXPECTED_PFR_ROWS", 684),
  "PFR advanced passing weekly source"
)
if (any(is.na(pfr$times_blitzed)) || any(is.na(pfr$times_pressured_pct))) {
  stop("PFR advanced passing source contains missing pressure fields.", call. = FALSE)
}
assert_unique(pfr, c("game_id", "player_id"), "PFR advanced passing")
pfr <- pfr[order(pfr$game_type, pfr$week, pfr$game_id, pfr$player_id), , drop = FALSE]

ngs_path <- file.path(output_dir, paste0("nflreadr_ngs_receiving_weekly_", season, ".csv"))
pfr_path <- file.path(output_dir, paste0("nflreadr_pfr_passing_weekly_", season, ".csv"))
write.csv(ngs, ngs_path, row.names = FALSE, na = "")
write.csv(pfr, pfr_path, row.names = FALSE, na = "")

validation <- data.frame(
  metric = c(
    "season",
    "ngs_receiving_weekly_rows",
    "ngs_excluded_missing_player_id",
    "ngs_avg_separation_non_missing",
    "pfr_passing_weekly_rows",
    "pfr_excluded_missing_player_id",
    "pfr_times_blitzed_non_missing",
    "pfr_pressure_pct_non_missing"
  ),
  value = c(
    season,
    nrow(ngs),
    excluded_ngs,
    sum(!is.na(ngs$avg_separation)),
    nrow(pfr),
    excluded_pfr,
    sum(!is.na(pfr$times_blitzed)),
    sum(!is.na(pfr$times_pressured_pct))
  ),
  stringsAsFactors = FALSE
)
validation_path <- file.path(
  output_dir,
  paste0("nflreadr_advanced_weekly_", season, "_validation.csv")
)
write.csv(validation, validation_path, row.names = FALSE, na = "")

message(
  "Exported advanced season=", season,
  ", ngs_rows=", nrow(ngs),
  ", ngs_excluded_missing_player_id=", excluded_ngs,
  ", pfr_rows=", nrow(pfr),
  ", pfr_excluded_missing_player_id=", excluded_pfr
)
