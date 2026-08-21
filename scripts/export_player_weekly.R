options(
  repos = c(CRAN = "https://cloud.r-project.org"),
  nflreadr.cache = "filesystem",
  nflreadr.prefer = "csv",
  nflreadr.verbose = TRUE
)

library(nflreadr)

season <- as.integer(Sys.getenv("NFLREADR_SEASON", "2025"))
expected_source_rows_text <- Sys.getenv(
  "NFLREADR_EXPECTED_SOURCE_ROWS",
  if (season == 2025) "19422" else ""
)
expected_source_rows <- if (nzchar(expected_source_rows_text)) {
  as.integer(expected_source_rows_text)
} else {
  NA_integer_
}
output_dir <- Sys.getenv("NFLREADR_OUTPUT_DIR", "artifacts")
dir.create(output_dir, recursive = TRUE, showWarnings = FALSE)

source_stats <- load_player_stats(
  seasons = season,
  summary_level = "week",
  file_type = "csv"
)

required_columns <- c(
  "game_id", "player_id", "season", "week", "season_type",
  "player_display_name", "position", "team", "opponent_team",
  "attempts", "carries", "targets"
)
missing_columns <- setdiff(required_columns, names(source_stats))
if (length(missing_columns) > 0) {
  stop(
    "Player-week source is missing columns: ",
    paste(missing_columns, collapse = ", "),
    call. = FALSE
  )
}

if (nrow(source_stats) == 0) {
  stop("Player-week source returned zero rows.", call. = FALSE)
}
if (any(is.na(source_stats$season)) || any(source_stats$season != season)) {
  stop("Player-week source contains an unexpected season.", call. = FALSE)
}
if (any(is.na(source_stats$game_id)) || any(source_stats$game_id == "")) {
  stop("Player-week source contains a missing game_id.", call. = FALSE)
}
if (!is.na(expected_source_rows) && nrow(source_stats) != expected_source_rows) {
  stop(
    "Expected ", expected_source_rows, " source rows for ", season,
    " but nflreadr returned ", nrow(source_stats), ".",
    call. = FALSE
  )
}

identified <- !is.na(source_stats$player_id) & source_stats$player_id != ""
excluded_missing_player_id <- sum(!identified)
stats <- source_stats[identified, , drop = FALSE]
if (nrow(stats) == 0) {
  stop("No identified player rows remain after filtering.", call. = FALSE)
}

key <- paste(stats$game_id, stats$player_id, sep = "|")
duplicate_keys <- duplicated(key) | duplicated(key, fromLast = TRUE)
if (any(duplicate_keys)) {
  stop(
    "Player-week source contains ", sum(duplicate_keys),
    " rows involved in duplicate (game_id, player_id) keys.",
    call. = FALSE
  )
}

order_columns <- intersect(
  c("season_type", "week", "game_id", "team", "player_id"),
  names(stats)
)
stats <- stats[do.call(order, stats[order_columns]), , drop = FALSE]

output_path <- file.path(
  output_dir,
  paste0("nflreadr_player_weekly_", season, ".csv")
)
write.csv(stats, output_path, row.names = FALSE, na = "")

numeric_total <- function(column) {
  if (!column %in% names(stats)) {
    return(NA_real_)
  }
  sum(as.numeric(stats[[column]]), na.rm = TRUE)
}

validation <- data.frame(
  metric = c(
    "season", "source_row_count", "excluded_missing_player_id",
    "exported_row_count", "unique_player_keys", "column_count",
    "attempts_total", "carries_total", "targets_total",
    "passing_yards_total", "rushing_yards_total", "receiving_yards_total"
  ),
  value = c(
    season,
    nrow(source_stats),
    excluded_missing_player_id,
    nrow(stats),
    length(unique(key)),
    ncol(stats),
    numeric_total("attempts"),
    numeric_total("carries"),
    numeric_total("targets"),
    numeric_total("passing_yards"),
    numeric_total("rushing_yards"),
    numeric_total("receiving_yards")
  ),
  stringsAsFactors = FALSE
)
validation_path <- file.path(
  output_dir,
  paste0("nflreadr_player_weekly_", season, "_validation.csv")
)
write.csv(validation, validation_path, row.names = FALSE, na = "")

message(
  "Exported season=", season,
  ", source_rows=", nrow(source_stats),
  ", excluded_missing_player_id=", excluded_missing_player_id,
  ", exported_rows=", nrow(stats),
  ", columns=", ncol(stats),
  ", output=", output_path
)
