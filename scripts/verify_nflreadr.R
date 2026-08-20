options(
  repos = c(CRAN = "https://cloud.r-project.org"),
  nflreadr.cache = "filesystem",
  nflreadr.prefer = "csv",
  nflreadr.verbose = TRUE
)

library(nflreadr)

expected_version <- package_version("1.5.1")
actual_version <- packageVersion("nflreadr")
stopifnot(actual_version == expected_version)

required_functions <- c(
  "load_pbp",
  "load_player_stats",
  "load_team_stats",
  "load_schedules",
  "load_players",
  "load_rosters_weekly",
  "load_snap_counts",
  "load_pfr_advstats",
  "load_injuries",
  "load_depth_charts",
  "load_nextgen_stats",
  "get_current_season",
  "get_current_week"
)

missing_functions <- required_functions[
  !vapply(required_functions, exists, logical(1), where = asNamespace("nflreadr"))
]
if (length(missing_functions) > 0) {
  stop(
    "Missing required nflreadr functions: ",
    paste(missing_functions, collapse = ", "),
    call. = FALSE
  )
}

teams <- load_teams(current = TRUE, file_type = "csv")
if (nrow(teams) < 32 || length(unique(teams$team_abbr)) < 32) {
  stop("NFL team-data smoke test returned fewer than 32 current teams.", call. = FALSE)
}

stats_2025 <- load_player_stats(
  seasons = 2025,
  summary_level = "week",
  file_type = "csv"
)
required_columns <- c(
  "player_id", "season", "week", "team", "opponent_team",
  "attempts", "carries", "targets"
)
missing_columns <- setdiff(required_columns, names(stats_2025))
if (length(missing_columns) > 0) {
  stop(
    "2025 player-stat data is missing columns: ",
    paste(missing_columns, collapse = ", "),
    call. = FALSE
  )
}
if (nrow(stats_2025) < 1000 || any(stats_2025$season != 2025, na.rm = TRUE)) {
  stop("2025 player-stat smoke test failed row-count or season validation.", call. = FALSE)
}

message(
  "nflreadr verification passed: version=", actual_version,
  ", current_season=", get_current_season(),
  ", current_week=", get_current_week(),
  ", current_teams=", nrow(teams),
  ", player_week_rows_2025=", nrow(stats_2025)
)
