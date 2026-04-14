"""
Cluster configuration and Kubernetes API client.

Each cluster is configured via environment variables:

  CLUSTER_1_NAME=openshift
  CLUSTER_1_URL=https://api.openshift2.lab.home:6443
  CLUSTER_1_TOKEN=<serviceaccount-token>
  CLUSTER_1_CA_CERT=/certs/openshift-ca.crt   # optional path to CA bundle

  CLUSTER_2_NAME=rke2
  CLUSTER_2_URL=https://192.168.1.99:6443
  CLUSTER_2_TOKEN=<serviceaccount-token>
  CLUSTER_2_CA_CERT=/certs/rke2-ca.crt

  CLUSTER_3_NAME=k3s
  CLUSTER_3_URL=https://192.168.1.105:6443
  CLUSTER_3_TOKEN=<serviceaccount-token>
  CLUSTER_3_INSECURE=true                      # skip TLS verify (self-signed)

If CLUSTER_N_CA_CERT is omitted and CLUSTER_N_INSECURE is not "true",
the system CA bundle is used.
"""

import logging
import os
import ssl
from dataclasses import dataclass

import aiohttp

log = logging.getLogger("clusters")

NAMESPACES_TO_SKIP = {
    os.environ.get("SKIP_NAMESPACES", "").split(",")
} if os.environ.get("SKIP_NAMESPACES") else set()

# Always skip these noisy system namespaces unless explicitly included
DEFAULT_SKIP = {"kube-system", "kube-public", "kube-node-lease"}
_skip_raw = os.environ.get("SKIP_NAMESPACES", "")
SKIP_NAMESPACES = DEFAULT_SKIP | {n.strip() for n in _skip_raw.split(",") if n.strip()}


@dataclass
class KubeCluster:
    name: str
    api_url: str
    token: str
    ca_cert_path: str | None
    insecure: bool

    def _ssl_context(self) -> ssl.SSLContext | bool:
        if self.insecure:
            return False  # aiohttp: disable verification
        ctx = ssl.create_default_context()
        if self.ca_cert_path:
            ctx.load_verify_locations(self.ca_cert_path)
        return ctx

    async def list_all_pods(self) -> list[dict]:
        """
        GET /api/v1/pods — returns all pods across all namespaces.
        Filters out namespaces in SKIP_NAMESPACES.
        """
        url = f"{self.api_url.rstrip('/')}/api/v1/pods"
        headers = {"Authorization": f"Bearer {self.token}"}
        ssl_ctx = self._ssl_context()

        connector = aiohttp.TCPConnector(ssl=ssl_ctx)
        async with aiohttp.ClientSession(connector=connector) as session:
            async with session.get(
                url,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                resp.raise_for_status()
                data = await resp.json()

        items = data.get("items", [])
        if SKIP_NAMESPACES:
            items = [
                p for p in items
                if p.get("metadata", {}).get("namespace") not in SKIP_NAMESPACES
            ]
        return items


def build_clusters() -> list[KubeCluster]:
    """Discover cluster configs from numbered CLUSTER_N_* env vars."""
    clusters = []
    n = 1
    while True:
        prefix = f"CLUSTER_{n}_"
        name = os.environ.get(f"{prefix}NAME")
        if not name:
            break
        url = os.environ.get(f"{prefix}URL")
        token = os.environ.get(f"{prefix}TOKEN")
        if not url or not token:
            log.warning("Cluster %d (%s): missing URL or TOKEN — skipping", n, name)
            n += 1
            continue

        ca_cert = os.environ.get(f"{prefix}CA_CERT")  # path
        insecure = os.environ.get(f"{prefix}INSECURE", "false").lower() == "true"

        clusters.append(KubeCluster(
            name=name,
            api_url=url,
            token=token,
            ca_cert_path=ca_cert,
            insecure=insecure,
        ))
        log.info("Registered cluster %d: %s → %s (insecure=%s)", n, name, url, insecure)
        n += 1

    return clusters
