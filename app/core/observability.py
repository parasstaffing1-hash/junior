from __future__ import annotations

from collections import Counter, defaultdict
from threading import Lock
from time import monotonic


class MetricsRegistry:
    def __init__(self):
        self._lock = Lock()
        self.requests = Counter()
        self.request_seconds = defaultdict(float)
        self.jobs = Counter()
        self.started_at = monotonic()

    def record_request(self, method: str, path: str, status: int, duration_seconds: float) -> None:
        key = f'{method}|{path}|{status}'
        with self._lock:
            self.requests[key] += 1
            self.request_seconds[f'{method}|{path}'] += max(0.0, duration_seconds)

    def render_prometheus(self) -> str:
        lines = ["# HELP jda_http_requests_total HTTP requests by method, route, and status", "# TYPE jda_http_requests_total counter"]
        with self._lock:
            for key, value in sorted(self.requests.items()):
                method, path, status = key.split("|", 2)
                lines.append(f'jda_http_requests_total{{method="{method}",path="{path}",status="{status}"}} {value}')
            lines.extend(["# HELP jda_http_request_duration_seconds_total HTTP request duration", "# TYPE jda_http_request_duration_seconds_total counter"])
            for key, value in sorted(self.request_seconds.items()):
                method, path = key.split("|", 1)
                lines.append(f'jda_http_request_duration_seconds_total{{method="{method}",path="{path}"}} {value:.6f}')
        lines.append(f"jda_process_uptime_seconds {monotonic() - self.started_at:.6f}")
        return "\n".join(lines) + "\n"
