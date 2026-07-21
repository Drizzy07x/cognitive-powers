"""Behavioral checks for payment retry exponential backoff and maximum attempts."""

import unittest

from src.payments.retry_policy import RetryPolicy


class RetryPolicyTests(unittest.TestCase):
    def test_exponential_backoff_is_bounded(self) -> None:
        policy = RetryPolicy(maximum_attempts=5, maximum_delay_seconds=1.0)
        self.assertEqual(policy.delay_for_attempt(1), 0.25)
        self.assertEqual(policy.delay_for_attempt(4), 1.0)

    def test_payment_retry_stops_at_maximum_attempts(self) -> None:
        policy = RetryPolicy(maximum_attempts=3)
        self.assertTrue(policy.should_retry(503, 2))
        self.assertFalse(policy.should_retry(503, 3))
