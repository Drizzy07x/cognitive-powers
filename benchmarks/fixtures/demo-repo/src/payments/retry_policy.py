"""Retry policy for transient payment gateway failures."""

from dataclasses import dataclass


@dataclass(frozen=True)
class RetryPolicy:
    """Bound exponential backoff by a maximum number of attempts."""

    maximum_attempts: int = 4
    base_delay_seconds: float = 0.25
    maximum_delay_seconds: float = 4.0

    def delay_for_attempt(self, attempt: int) -> float:
        if attempt < 1:
            raise ValueError("attempt must be positive")
        if attempt > self.maximum_attempts:
            raise ValueError("attempt exceeds maximum attempts")
        exponential_delay = self.base_delay_seconds * (2 ** (attempt - 1))
        return min(exponential_delay, self.maximum_delay_seconds)

    def should_retry(self, status_code: int, attempt: int) -> bool:
        transient_failure = status_code in {408, 429, 500, 502, 503, 504}
        return transient_failure and attempt < self.maximum_attempts
