#!/usr/bin/env python3
"""
K8s Pod Monitor
Polls multiple Kubernetes clusters for failing pods and sends Gmail alerts
when a pod has been in a failed state for more than ALERT_THRESHOLD_MINUTES.
"""

import asyncio
import logging
import os
import signal
import sys
from datetime import datetime, timezone

from clusters import build_clusters
from alerter import Alerter
from state import MonitorState

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger("monitor")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
POLL_INTERVAL_SECONDS = int(os.environ.get("POLL_INTERVAL_SECONDS", "60"))
ALERT_THRESHOLD_MINUTES = int(os.environ.get("ALERT_THRESHOLD_MINUTES", "10"))

# Pod phases / reasons that count as "failing"
FAILING_PHASES = {"Failed", "Unknown"}
FAILING_REASONS = {
    "CrashLoopBackOff",
    "OOMKilled",
    "Error",
    "ImagePullBackOff",
    "ErrImagePull",
    "CreateContainerConfigError",
    "CreateContainerError",
    "InvalidImageName",
    "RunContainerError",
    "PostStartHookError",
    "PreStopHookError",
    "ContainerCannotRun",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def extract_failure_reason(pod: dict) -> str | None:
    """
    Return the failure reason for a pod, or None if the pod looks healthy.
    Checks phase, container statuses (waiting/terminated), and init containers.
    """
    phase = pod.get("status", {}).get("phase", "Unknown")

    if phase in FAILING_PHASES:
        return phase

    if phase == "Pending":
        # Check init containers first
        for cs in pod.get("status", {}).get("initContainerStatuses", []):
            reason = _container_failure_reason(cs)
            if reason:
                return f"Init:{reason}"
        # Then regular containers
        for cs in pod.get("status", {}).get("containerStatuses", []):
            reason = _container_failure_reason(cs)
            if reason:
                return reason

    if phase == "Running":
        for cs in pod.get("status", {}).get("containerStatuses", []):
            reason = _container_failure_reason(cs)
            if reason:
                return reason

    return None


def _container_failure_reason(cs: dict) -> str | None:
    state = cs.get("state", {})

    waiting = state.get("waiting", {})
    if waiting:
        reason = waiting.get("reason", "")
        if reason in FAILING_REASONS:
            return reason

    terminated = state.get("terminated", {})
    if terminated:
        reason = terminated.get("reason", "")
        exit_code = terminated.get("exitCode", 0)
        if reason in FAILING_REASONS or exit_code != 0:
            return reason or f"Terminated(exit={exit_code})"

    return None


def get_container_states(pod: dict) -> list[dict]:
    """Return a summary of all container states for the alert email."""
    result = []
    for cs in pod.get("status", {}).get("containerStatuses", []):
        state = cs.get("state", {})
        entry = {"name": cs.get("name", "?"), "ready": cs.get("ready", False), "restarts": cs.get("restartCount", 0)}
        if "waiting" in state:
            entry["state"] = f"Waiting: {state['waiting'].get('reason', '?')}"
            entry["message"] = state["waiting"].get("message", "")
        elif "terminated" in state:
            entry["state"] = f"Terminated: exit={state['terminated'].get('exitCode', '?')} reason={state['terminated'].get('reason', '?')}"
            entry["message"] = state["terminated"].get("message", "")
        elif "running" in state:
            entry["state"] = "Running"
            entry["message"] = ""
        result.append(entry)
    return result


# ---------------------------------------------------------------------------
# Core poll loop
# ---------------------------------------------------------------------------
async def poll_cluster(cluster, state: MonitorState, alerter: Alerter):
    """Fetch all pods from one cluster and update state."""
    log.debug("Polling cluster: %s", cluster.name)
    try:
        pods = await cluster.list_all_pods()
    except Exception as exc:
        log.error("Cluster %s unreachable: %s", cluster.name, exc)
        state.mark_cluster_error(cluster.name, str(exc))
        return

    now = datetime.now(timezone.utc)
    active_keys: set[str] = set()

    for pod in pods:
        meta = pod.get("metadata", {})
        name = meta.get("name", "?")
        namespace = meta.get("namespace", "?")

        # Skip completed jobs — exit 0 is fine
        if pod.get("status", {}).get("phase") == "Succeeded":
            continue

        reason = extract_failure_reason(pod)
        if not reason:
            continue

        key = f"{cluster.name}/{namespace}/{name}"
        active_keys.add(key)
        container_states = get_container_states(pod)

        fp = state.upsert_failing_pod(
            cluster=cluster.name,
            namespace=namespace,
            name=name,
            reason=reason,
            phase=pod.get("status", {}).get("phase", "Unknown"),
            now=now,
            container_states=container_states,
        )

        duration_mins = fp.duration_minutes()
        log.debug(
            "[%s] %s/%s failing (%s) for %.1f min — alert_sent=%s",
            cluster.name, namespace, name, reason, duration_mins, fp.alert_sent,
        )

        if duration_mins >= ALERT_THRESHOLD_MINUTES and not fp.alert_sent:
            log.info(
                "ALERT: [%s] %s/%s (%s) failing for %.1f min",
                cluster.name, namespace, name, reason, duration_mins,
            )
            await alerter.send_alert(fp)
            state.mark_alert_sent(key)

    # Remove pods that have recovered
    recovered = state.remove_recovered_pods(cluster.name, active_keys)
    for r in recovered:
        log.info("RECOVERED: [%s] %s/%s", r.cluster, r.namespace, r.name)
        if r.alert_sent:
            await alerter.send_recovery(r)

    state.mark_cluster_ok(cluster.name, total_pods=len(pods))


async def run_monitor():
    clusters = build_clusters()
    if not clusters:
        log.error("No clusters configured. Set CLUSTER_* environment variables.")
        sys.exit(1)

    alerter = Alerter()
    state = MonitorState()

    log.info(
        "Starting pod monitor | clusters=%d | poll=%ds | threshold=%dmin",
        len(clusters), POLL_INTERVAL_SECONDS, ALERT_THRESHOLD_MINUTES,
    )
    for c in clusters:
        log.info("  → %s  (%s)", c.name, c.api_url)

    while True:
        tasks = [poll_cluster(c, state, alerter) for c in clusters]
        await asyncio.gather(*tasks, return_exceptions=True)
        log.debug("Poll complete — sleeping %ds", POLL_INTERVAL_SECONDS)
        await asyncio.sleep(POLL_INTERVAL_SECONDS)


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------
def _handle_signal(signum, frame):
    log.info("Signal %s received — shutting down", signum)
    sys.exit(0)


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)
    asyncio.run(run_monitor())
