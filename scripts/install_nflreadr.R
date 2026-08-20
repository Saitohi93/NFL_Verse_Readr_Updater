options(repos = c(CRAN = "https://cloud.r-project.org"))

required_version <- package_version("1.5.1")

if (!requireNamespace("remotes", quietly = TRUE)) {
  install.packages("remotes")
}

installed_version <- if (requireNamespace("nflreadr", quietly = TRUE)) {
  packageVersion("nflreadr")
} else {
  package_version("0.0.0")
}

if (installed_version != required_version) {
  remotes::install_version(
    package = "nflreadr",
    version = as.character(required_version),
    repos = getOption("repos"),
    upgrade = "never",
    dependencies = TRUE
  )
}

actual_version <- packageVersion("nflreadr")
if (actual_version != required_version) {
  stop(
    "Expected nflreadr ", required_version,
    " but installed ", actual_version,
    call. = FALSE
  )
}

message("Installed nflreadr ", actual_version)
