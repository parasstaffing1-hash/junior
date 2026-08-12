from __future__ import annotations

from collections import Counter, defaultdict, deque
from threading import Lock
from time import monotonic


class MetricsRegistry:
    def __init__(self):
        self._lock = Lock()
        self.requests = Counter()
        self.request_seconds = defaultdict(float)
        self.request_samples = defaultdict(lambda: deque(maxlen=5_000))
        self.jobs = Counter()
        self.started_at = monotonic()

    def record_request(self, method: str, path: str, status: int, duration_seconds: float) -> None:
        key = f'{method}|{path}|{status}'
        with self._lock:
            self.requests[key] += 1
            self.request_seconds[f'{method}|{path}'] += max(0.0, duration_seconds)
            self.request_samples[f'{method}|{path}'].append(max(0.0, duration_seconds))

    @staticmethod
    def _percentile(values, percentile: float) -> float:
        if not values:
            return 0.0
        ordered = sorted(values)
        index = min(len(ordered) - 1, max(0, int(round((percentile / 100) * (len(ordered) - 1)))))
        return round(float(ordered[index]), 6)

    def snapshot(self) -> dict:
        """Return bounded route-level SLO evidence for operators."""
        with self._lock:
            routes = {}
            for route, samples in self.request_samples.items():
                method, path = route.split("|", 1)
                total = sum(value for key, value in self.requests.items() if key.startswith(f"{method}|{path}|"))
                errors = sum(value for key, value in self.requests.items() if key.startswith(f"{method}|{path}|") and int(key.rsplit("|", 1)[-1]) >= 500)
                values = list(samples)
                routes[route] = {"method": method, "path": path, "request_count": int(total), "error_count": int(errors), "error_rate": round(errors / total, 6) if total else 0.0, "p50_seconds": self._percentile(values, 50), "p95_seconds": self._percentile(values, 95), "p99_seconds": self._percentile(values, 99)}
            return {"started_seconds_ago": round(monotonic() - self.started_at, 3), "route_count": len(routes), "routes": sorted(routes.values(), key=lambda item: (item["path"], item["method"]))}

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
