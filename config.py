from dataclasses import dataclass


# Session and cache configuration
SESSION_TTL_SECONDS = 30 * 60  # 30 minutes idle timeout
SESSION_MAX_SIZE = 128  # max concurrent sessions cached in memory


# Rate limiting (max_calls, period_seconds)
SAVE_RATE_LIMIT = (3, 30)
LOAD_RATE_LIMIT = (3, 30)
AUTO_HUNT_RATE_LIMIT = (3, 20)
MAP_RATE_LIMIT = (5, 15)


# Auto-hunt and battle pacing
DEFAULT_AUTO_HUNT_DELAY = 0.6


# Persistence
BACKUP_SUFFIX = ".bak"
TMP_SUFFIX = ".tmp"


# Environment
TOKEN_ENV_KEY = "BOT_TOKEN"


@dataclass
class BotConfig:
    """Container for runtime configuration values."""

    session_ttl: int = SESSION_TTL_SECONDS
    session_max_size: int = SESSION_MAX_SIZE
    auto_hunt_delay: float = DEFAULT_AUTO_HUNT_DELAY
    backup_suffix: str = BACKUP_SUFFIX
    tmp_suffix: str = TMP_SUFFIX


CONFIG = BotConfig()
