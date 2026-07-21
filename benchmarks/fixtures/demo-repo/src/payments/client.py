"""Small payment gateway client used by the context benchmark."""

from .retry_policy import RetryPolicy


class PaymentClient:
    def __init__(self, retry_policy: RetryPolicy | None = None) -> None:
        self.retry_policy = retry_policy or RetryPolicy()

    def retry_delay(self, status_code: int, attempt: int) -> float | None:
        if not self.retry_policy.should_retry(status_code, attempt):
            return None
        return self.retry_policy.delay_for_attempt(attempt)
