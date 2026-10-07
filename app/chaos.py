"""Chaos state: probabilistic error and latency injection, tunable live."""
import os
import random
import threading
import time


def _env_float(name, default):
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


class Chaos:
    def __init__(self):
        self._lock = threading.Lock()
        self.fail_rate = _env_float("FAIL_RATE", 0.0)      # probability of a 500
        self.slow_rate = _env_float("SLOW_RATE", 0.0)      # probability of a slow request
        self.slow_seconds = _env_float("SLOW_SECONDS", 2.0)

    def state(self):
        with self._lock:
            return {
                "fail_rate": self.fail_rate,
                "slow_rate": self.slow_rate,
                "slow_seconds": self.slow_seconds,
            }

    def set_fail_rate(self, value):
        with self._lock:
            self.fail_rate = _clamp(value)

    def set_latency(self, seconds, rate=1.0):
        with self._lock:
            self.slow_seconds = max(0.0, float(seconds))
            self.slow_rate = _clamp(rate)

    def reset(self):
        with self._lock:
            self.fail_rate = 0.0
            self.slow_rate = 0.0

    def apply(self):
        """Sleep and/or return True when this request should fail."""
        s = self.state()
        if random.random() < s["slow_rate"]:
            time.sleep(s["slow_seconds"])
        return random.random() < s["fail_rate"]


def _clamp(v):
    return min(1.0, max(0.0, float(v)))
