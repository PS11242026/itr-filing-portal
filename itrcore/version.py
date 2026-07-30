"""Application identity and version constants."""

APP_NAME = "ITR Filing Portal"
APP_SHORT = "itr-portal"
__version__ = "1.1.0"

# Bumped whenever computation logic changes in a way that can alter results;
# stored on every computation snapshot for reproducibility.
# 0.2: agricultural-income partial integration (both regimes) can raise the
#      slab tax when net agri income exceeds the Finance-Act threshold.
ENGINE_VERSION = "2026_27.0.2"
