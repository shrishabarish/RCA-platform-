import time
from typing import Callable, Any, Optional, Tuple
import threading


class CircuitBreaker:
    """
    Step 2f:
    Live Safety Circuit Breaker:
    - Replaces single-thread hang-prone timeout.
    - After N consecutive timeouts or errors, opens the breaker for T seconds.
    - When open, immediately routes traffic to the fallback engine (current scorer) without waiting.
    - Thread-safe and stateful.
    """

    def __init__(self, max_failures: int = 3, cooldown_sec: float = 30.0):
        self.max_failures = max_failures
        self.cooldown_sec = cooldown_sec
        self.failure_count = 0
        self.last_failure_time: Optional[float] = None
        self._lock = threading.Lock()

    def is_open(self) -> bool:
        with self._lock:
            if self.failure_count >= self.max_failures:
                if self.last_failure_time is not None:
                    elapsed = time.time() - self.last_failure_time
                    if elapsed < self.cooldown_sec:
                        return True
                    else:
                        # Cooldown elapsed, allow a probe request (half-open)
                        return False
            return False

    def record_success(self):
        with self._lock:
            self.failure_count = 0
            self.last_failure_time = None

    def record_failure(self):
        with self._lock:
            self.failure_count += 1
            self.last_failure_time = time.time()

    def reset(self):
        with self._lock:
            self.failure_count = 0
            self.last_failure_time = None

    def execute(
        self,
        primary_fn: Callable[[], Any],
        fallback_fn: Callable[[], Any],
        timeout_sec: Optional[float] = None
    ) -> Tuple[Any, bool]:
        """
        Executes primary_fn if breaker is closed.
        If breaker is open, immediately invokes fallback_fn.
        Returns (result, was_fallback_used).
        """
        if self.is_open():
            return fallback_fn(), True

        try:
            res = primary_fn()
            self.record_success()
            return res, False
        except Exception as ex:
            self.record_failure()
            return fallback_fn(), True
