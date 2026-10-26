import time
import threading
import logging
from typing import Optional, List
from datetime import datetime, timezone

from app.security.ephemeral_gateway import ephemeral_gateway
from app.security.audit_logger import audit_logger

logger = logging.getLogger("geosmin.security.reaper")


class SessionReaper:
    """
    Automated Consultation Session Reaper Background Task (DevOps & Security Hardening).
    Continuously monitors active zero-knowledge ephemeral consultation sessions.
    Strictly terminates sessions exceeding the 300-second TTL window and immediately
    purges transient cryptographic session keys, authorization scopes, and decrypted
    memory buffers to prevent cold-boot and heap inspection attacks.
    """

    def __init__(self, interval_seconds: float = 2.0, gateway=None):
        self.interval = interval_seconds
        self.gateway = gateway or ephemeral_gateway
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._reaped_count = 0

    def start(self):
        """Start the background reaper daemon thread."""
        if self._running:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop,
            name="SessionReaperDaemon",
            daemon=True
        )
        self._running = True
        self._thread.start()
        logger.info("Session Reaper background daemon started (polling interval: %ss).", self.interval)

    def stop(self):
        """Stop the background reaper daemon thread."""
        if not self._running:
            return
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3.0)
        self._running = False
        logger.info("Session Reaper background daemon stopped.")

    def reap_now(self, current_time: Optional[float] = None) -> List[str]:
        """
        Execute an immediate sweep to terminate expired consultations
        and clear transient memory buffers.
        """
        purged = self.gateway.purge_expired_sessions(current_time=current_time)
        if purged:
            self._reaped_count += len(purged)
            logger.info("Session Reaper swept and terminated %d consultation session(s): %s", len(purged), purged)
        return purged

    def _run_loop(self):
        while not self._stop_event.is_set():
            try:
                self.reap_now()
            except Exception as e:
                logger.error("Error in Session Reaper loop: %s", e)
            self._stop_event.wait(self.interval)

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def total_reaped(self) -> int:
        return self._reaped_count


# Global singleton instance
session_reaper = SessionReaper()
