"""
In-memory state for the pod monitor.
Thread-safe (asyncio single-threaded, but using a lock for safety).
"""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


@dataclass
class FailingPod:
    cluster: str
    namespace: str
    name: str
    reason: str
    phase: str
    first_seen: datetime
    last_seen: datetime
    alert_sent: bool = False
    alert_sent_at: Optional[datetime] = None
    container_states: list = field(default_factory=list)

    def duration_minutes(self) -> float:
        return (self.last_seen - self.first_seen).total_seconds() / 60

    def key(self) -> str:
        return f"{self.cluster}/{self.namespace}/{self.name}"

    def __str__(self):
        return (
            f"[{self.cluster}] {self.namespace}/{self.name} "
            f"({self.reason}, {self.duration_minutes():.1f}min)"
        )


@dataclass
class ClusterHealth:
    name: str
    ok: bool = False
    error: Optional[str] = None
    total_pods: int = 0
    last_check: Optional[datetime] = None


class MonitorState:
    def __init__(self):
        self._lock = asyncio.Lock()
        self._failing: dict[str, FailingPod] = {}
        self._clusters: dict[str, ClusterHealth] = {}

    async def upsert_failing_pod(
        self,
        cluster: str,
        namespace: str,
        name: str,
        reason: str,
        phase: str,
        now: datetime,
        container_states: list,
    ) -> FailingPod:
        async with self._lock:
            key = f"{cluster}/{namespace}/{name}"
            if key in self._failing:
                fp = self._failing[key]
                fp.last_seen = now
                fp.reason = reason
                fp.phase = phase
                fp.container_states = container_states
            else:
                fp = FailingPod(
                    cluster=cluster,
                    namespace=namespace,
                    name=name,
                    reason=reason,
                    phase=phase,
                    first_seen=now,
                    last_seen=now,
                    container_states=container_states,
                )
                self._failing[key] = fp
            return fp

    # Sync wrapper for use outside async context
    def upsert_failing_pod_sync(self, **kwargs) -> FailingPod:
        key = f"{kwargs['cluster']}/{kwargs['namespace']}/{kwargs['name']}"
        now = kwargs["now"]
        if key in self._failing:
            fp = self._failing[key]
            fp.last_seen = now
            fp.reason = kwargs["reason"]
            fp.phase = kwargs["phase"]
            fp.container_states = kwargs.get("container_states", [])
        else:
            fp = FailingPod(
                cluster=kwargs["cluster"],
                namespace=kwargs["namespace"],
                name=kwargs["name"],
                reason=kwargs["reason"],
                phase=kwargs["phase"],
                first_seen=now,
                last_seen=now,
                container_states=kwargs.get("container_states", []),
            )
            self._failing[key] = fp
        return fp

    def upsert_failing_pod(self, **kwargs) -> FailingPod:  # noqa: F811 — override as sync
        return self.upsert_failing_pod_sync(**kwargs)

    def mark_alert_sent(self, key: str):
        if key in self._failing:
            self._failing[key].alert_sent = True
            self._failing[key].alert_sent_at = datetime.now(timezone.utc)

    def remove_recovered_pods(self, cluster: str, active_keys: set) -> list[FailingPod]:
        """Remove pods that are no longer failing. Returns list of recovered pods."""
        recovered = []
        to_remove = [
            k for k, p in self._failing.items()
            if p.cluster == cluster and k not in active_keys
        ]
        for k in to_remove:
            recovered.append(self._failing.pop(k))
        return recovered

    def mark_cluster_ok(self, name: str, total_pods: int):
        self._clusters[name] = ClusterHealth(
            name=name,
            ok=True,
            total_pods=total_pods,
            last_check=datetime.now(timezone.utc),
        )

    def mark_cluster_error(self, name: str, error: str):
        existing = self._clusters.get(name)
        self._clusters[name] = ClusterHealth(
            name=name,
            ok=False,
            error=error,
            total_pods=existing.total_pods if existing else 0,
            last_check=datetime.now(timezone.utc),
        )
