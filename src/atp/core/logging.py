"""
Structured logging utility for ATP SDK using structlog.

Provides consistent, structured JSON logging across all ATP components.
"""

import logging
import sys
from typing import Any

import structlog

# Global structlog configuration
_configured = False


def configure_logging(level: str = "INFO", use_json: bool = True) -> None:
    """
    Configure structlog for ATP.

    Args:
        level: Log level ("DEBUG", "INFO", "WARNING", "ERROR")
        use_json: Use JSON renderer (default: True). Set False for development.
    """
    global _configured

    if _configured:
        return

    # Configure standard logging
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, level.upper()),
    )

    # Choose renderer based on environment
    renderer: Any
    if use_json:
        renderer = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=True)

    # Configure structlog
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.StackInfoRenderer(),
            structlog.dev.set_exc_info,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(getattr(logging, level.upper())),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=False,
    )

    _configured = True


def get_logger(name: str = "atp") -> Any:
    """
    Get ATP structured logger instance.

    Args:
        name: Logger name (default: "atp")

    Returns:
        Configured structlog instance

    Example:
        ```python
        logger = get_logger()
        logger.info("system_registered",
                    system_id="abc123",
                    system_name="my-agent",
                    system_type="agent")
        ```
    """
    if not _configured:
        configure_logging()

    return structlog.get_logger(name)


def set_log_level(level: str) -> None:
    """
    Set ATP log level.

    Args:
        level: Log level ("DEBUG", "INFO", "WARNING", "ERROR")
    """
    global _configured
    _configured = False  # Force reconfiguration
    configure_logging(level)


def disable_logging() -> None:
    """Disable ATP logging (set to CRITICAL level)."""
    set_log_level("CRITICAL")


def enable_logging() -> None:
    """Enable ATP logging (set to INFO level)."""
    set_log_level("INFO")
