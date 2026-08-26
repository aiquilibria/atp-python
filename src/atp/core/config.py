"""
ATP Configuration Management

Handles configuration from environment variables and direct parameters.
"""

import logging
import os

from dotenv import load_dotenv

from atp.core.exceptions import ATPConfigError

# Load environment variables from .env file if present
load_dotenv()

logger = logging.getLogger(__name__)


class ATPConfig:
    """Configuration for ATP client."""

    def __init__(
        self,
        api_key: str | None = None,
        exchange_url: str | None = None,
        enable_logging: bool = False,
        commit_timeout: int = 30,
        retry_attempts: int = 3,
        max_verification_delay: float = 2.0,
        proof_ttl_seconds: int | None = None,
        proof_cleanup_interval: int | None = None,
        proof_storage_required: bool = True,
        proof_db_path: str | None = None,
    ):
        """
        Initialize ATP configuration.

        Args:
            api_key: AIquilibria API key (defaults to ATP_API_KEY env var)
            exchange_url: Exchange URL (defaults to ATP_EXCHANGE_URL env var)
            enable_logging: Enable detailed ATP logs
            commit_timeout: Timeout for commit API calls in seconds
            retry_attempts: Number of retry attempts for failed commits
            max_verification_delay: Max delay for verification of fresh responses (seconds)
            proof_ttl_seconds: Time to live for stored proofs (defaults to 7 days)
            proof_cleanup_interval: Cleanup interval in seconds (defaults to 1 hour)
            proof_storage_required: Require storage backend for ATP (default: True)
            proof_db_path: Filesystem path for the SQLite proof database used by
                SQLiteProofStore. Defaults to ATP_PROOF_DB_PATH env var or
                ~/.atp/proofs.db. The parent directory is created automatically.
        """
        # Load from environment variables if not provided
        self.api_key = api_key or os.getenv("ATP_API_KEY")
        self.exchange_url = exchange_url or os.getenv("ATP_EXCHANGE_URL", "http://localhost:8080")

        # Optional settings
        self.enable_logging = (
            enable_logging or os.getenv("ATP_ENABLE_LOGGING", "").lower() == "true"
        )
        self.commit_timeout = commit_timeout
        self.retry_attempts = retry_attempts

        # Verification delay for fresh responses (prevents race condition)
        # If response timestamp is within this many seconds, wait before verifying
        self.max_verification_delay = max_verification_delay

        # Proof storage configuration (Challenge-Response)
        self.proof_ttl_seconds = proof_ttl_seconds or int(
            os.getenv("ATP_PROOF_TTL_SECONDS", str(7 * 24 * 60 * 60))  # Default: 7 days
        )
        self.proof_cleanup_interval = proof_cleanup_interval or int(
            os.getenv("ATP_PROOF_CLEANUP_INTERVAL", str(60 * 60))  # Default: 1 hour
        )
        self.proof_storage_required = (
            proof_storage_required
            if proof_storage_required is not None
            else os.getenv("ATP_PROOF_STORAGE_REQUIRED", "true").lower() == "true"
        )
        # SQLiteProofStore database path.
        # Resolution order: constructor arg → ATP_PROOF_DB_PATH env var → ~/.atp/proofs.db
        self.proof_db_path: str = (
            proof_db_path or os.getenv("ATP_PROOF_DB_PATH") or "~/.atp/proofs.db"
        )

        # Validate required fields
        if not self.api_key:
            raise ATPConfigError(
                "ATP API key is required. Set ATP_API_KEY environment variable or pass api_key parameter."
            )

        # Configure logging
        if self.enable_logging:
            logging.basicConfig(
                level=logging.INFO,
                format="[ATP] %(asctime)s - %(name)s - %(levelname)s - %(message)s",
            )

        logger.info(f"ATP configured with exchange: {self.exchange_url}")
