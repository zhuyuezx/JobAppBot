"""Application provider identifiers shared by settings, storage, CLI and workers."""

CLAUDE = "claude-chrome"
CODEX = "codex-playwright"
APPLICATION_ENGINES = (CLAUDE, CODEX)
LEGACY_CODEX = "codex-chrome"  # Read only during migration; never accepted for new work.


def validate_engine(engine):
    if engine not in APPLICATION_ENGINES:
        raise ValueError(f"engine must be one of: {', '.join(APPLICATION_ENGINES)}")
    return engine


def migrate_engine(engine):
    return CODEX if engine == LEGACY_CODEX else engine


# Common levels supported by the installed Codex model catalog. Empty uses model default.
THINKING_LEVELS = ("", "low", "medium", "high", "xhigh")


def validate_thinking_level(value):
    if value not in THINKING_LEVELS:
        raise ValueError("GPT thinking level must be default, low, medium, high or xhigh")
    return value
