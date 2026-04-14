# K8s Pod Monitor

Lightweight Docker daemon that polls multiple Kubernetes clusters every 60 seconds and sends a Gmail alert when any pod has been in a failing state for more than 10 minutes. Sends a recovery email when the pod comes good again.

---

## How it works

Every 60 seconds the monitor hits `/api/v1/pods` directly on each cluster's API server using a ServiceAccount bearer token. It tracks how long each pod has been in a failing state in memory. Once a pod crosses the 10 minute threshold an alert email is sent. When the pod recovers (or is deleted) a recovery email follows.

State is in-memory only — a container restart resets the failure timers.

---

## What counts as failing

| Trigger | Detail |
|---|---|
| Pod phase | `Failed`, `Unknown` |
| Container waiting | `CrashLoopBackOff`, `OOMKilled`, `ImagePullBackOff`, `ErrImagePull`, `CreateContainerConfigError`, `CreateContainerError`, `InvalidImageName`, `RunContainerError`, and others |
| Container terminated non-zero | Any exit code ≠ 0 |
| Init container failures | Same reasons, prefixed with `Init:` |
| Succeeded pods | **Ignored** — completed jobs are not errors |

Skips `kube-system`, `kube-public`, and `kube-node-lease` by default.

---

## Prerequisites

- Docker + Docker Compose on the host running the monitor
- `kubectl` access to each cluster for the initial RBAC setup
- A Gmail account with 2FA enabled (required for App Passwords)
- Network line-of-sight from the Docker host to each cluster's API server (`port 6443`)

---

## Step 1 — Create ServiceAccounts on each cluster

Apply the RBAC manifest to all three clusters. The manifest creates:
- A `pod-monitor` namespace
- A `pod-monitor` ServiceAccount
- A `pod-monitor-reader` ClusterRole with read-only access to pods
- A long-lived token Secret

```bash
# OpenShift
oc apply -f rbac.yaml

# RKE2
kubectl apply -f rbac.yaml --kubeconfig ~/.kube/rke2.yaml

# k3s
kubectl apply -f rbac.yaml
```

### RKE2 — Rancher webhook workaround

If your RKE2 cluster has Rancher installed but the webhook service is missing, namespace creation will be blocked. Patch the failing webhooks to `Ignore` first:

```bash
kubectl patch validatingwebhookconfiguration rancher.cattle.io \
  --kubeconfig ~/.kube/rke2.yaml \
  --type=json \
  -p='[
    {"op":"replace","path":"/webhooks/5/failurePolicy","value":"Ignore"},
    {"op":"replace","path":"/webhooks/6/failurePolicy","value":"Ignore"},
    {"op":"replace","path":"/webhooks/7/failurePolicy","value":"Ignore"}
  ]'
```

Then apply the RBAC manifest as normal.

---

## Step 2 — Extract tokens

Run this on each cluster and copy the output — this is the decoded bearer token to paste into your compose file.

```bash
# OpenShift
oc get secret pod-monitor-token -n pod-monitor \
  -o jsonpath='{.data.token}' | base64 -d

# RKE2
kubectl get secret pod-monitor-token -n pod-monitor \
  --kubeconfig ~/.kube/rke2.yaml \
  -o jsonpath='{.data.token}' | base64 -d

# k3s
kubectl get secret pod-monitor-token -n pod-monitor \
  -o jsonpath='{.data.token}' | base64 -d
```

> **Important:** The `| base64 -d` is required. The raw Secret data is base64-encoded — pasting it without decoding will give a 401 Unauthorized.

---

## Step 3 — Gmail App Password

1. Go to **myaccount.google.com → Security → 2-Step Verification → App passwords**
2. Create a new App Password (name it "K8s Monitor")
3. Copy the 16-character password — you won't see it again

---

## Step 4 — Configure docker-compose.yml

```yaml
GMAIL_USER: "you@gmail.com"
GMAIL_APP_PASS: "abcd efgh ijkl mnop"
ALERT_TO: "you@gmail.com"        # comma-separate for multiple recipients

CLUSTER_1_TOKEN: "eyJhbGci..."   # decoded token from Step 2
CLUSTER_2_TOKEN: "eyJhbGci..."
CLUSTER_3_TOKEN: "eyJhbGci..."
```

### TLS / certificate notes

All three clusters use self-signed certificates on their API servers. Set `INSECURE: "true"` for each:

```yaml
CLUSTER_1_INSECURE: "true"
CLUSTER_2_INSECURE: "true"
CLUSTER_3_INSECURE: "true"
```

If you have a CA bundle you want to verify against instead, supply the path:

```yaml
CLUSTER_1_CA_CERT: "/certs/my-ca.crt"
# and mount it:
volumes:
  - ~/certs/my-ca.crt:/certs/my-ca.crt:ro
```

---

## Step 5 — Build and run

```bash
cd k8s-pod-monitor
docker compose up --build -d
docker compose logs -f
```

Expected startup output:

```
[INFO] clusters: Registered cluster 1: openshift → https://api.openshift2.lab.home:6443 (insecure=True)
[INFO] clusters: Registered cluster 2: rke2 → https://192.168.1.99:6443 (insecure=True)
[INFO] clusters: Registered cluster 3: k3s → https://192.168.1.105:6443 (insecure=True)
[INFO] monitor: Starting pod monitor | clusters=3 | poll=60s | threshold=10min
```

---

## Testing

The easiest way to trigger an alert is to point a deployment at a non-existent image tag:

```bash
kubectl set image deployment/<name> -n <namespace> <container>=nginx:thistagdoesnotexist999
```

This produces `ImagePullBackOff` immediately. With `LOG_LEVEL: "DEBUG"` you'll see the failure timer counting up each poll:

```
[DEBUG] monitor: [k3s] retro-game/arcadians-api-xxx failing (ImagePullBackOff) for 1.0 min — alert_sent=False
[DEBUG] monitor: [k3s] retro-game/arcadians-api-xxx failing (ImagePullBackOff) for 2.0 min — alert_sent=False
...
[INFO]  monitor: ALERT: [k3s] retro-game/arcadians-api-xxx (ImagePullBackOff) failing for 10.0 min
```

To restore the deployment afterwards:

```bash
kubectl rollout undo deployment/<name> -n <namespace>
```

---

## Tuning

| Variable | Default | Description |
|---|---|---|
| `POLL_INTERVAL_SECONDS` | `60` | How often to poll all clusters |
| `ALERT_THRESHOLD_MINUTES` | `10` | Minutes before alert fires |
| `SKIP_NAMESPACES` | _(empty)_ | Extra namespaces to skip, comma-separated |
| `LOG_LEVEL` | `INFO` | Set to `DEBUG` for per-pod poll logging |

---

## Adding a cluster

Add a new numbered block to `docker-compose.yml` — no code changes needed:

```yaml
CLUSTER_4_NAME: "my-cluster"
CLUSTER_4_URL: "https://192.168.1.x:6443"
CLUSTER_4_TOKEN: "eyJhbGci..."
CLUSTER_4_INSECURE: "true"
```

---

## Networking

`network_mode: host` is used so the container can reach LAN addresses (`192.168.1.x`) and internal DNS (`api.openshift2.lab.home`) without any extra routing config. The monitor makes outbound HTTPS calls to port 6443 only — no inbound ports are exposed.
