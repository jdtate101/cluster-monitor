"""
Gmail alerter using SMTP with an App Password.

Required environment variables:
  GMAIL_USER      your Gmail address (e.g. you@gmail.com)
  GMAIL_APP_PASS  16-char App Password from Google Account → Security → App Passwords
  ALERT_TO        comma-separated recipient addresses
"""

import logging
import os
import smtplib
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

log = logging.getLogger("alerter")

GMAIL_USER = os.environ.get("GMAIL_USER", "")
GMAIL_APP_PASS = os.environ.get("GMAIL_APP_PASS", "")
ALERT_TO_RAW = os.environ.get("ALERT_TO", GMAIL_USER)
ALERT_TO = [a.strip() for a in ALERT_TO_RAW.split(",") if a.strip()]

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587


def _send(subject: str, html: str, text: str):
    if not GMAIL_USER or not GMAIL_APP_PASS:
        log.warning("Email not configured (GMAIL_USER / GMAIL_APP_PASS missing) — skipping send")
        log.info("Would have sent: %s", subject)
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = GMAIL_USER
    msg["To"] = ", ".join(ALERT_TO)
    msg.attach(MIMEText(text, "plain"))
    msg.attach(MIMEText(html, "html"))

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as server:
            server.ehlo()
            server.starttls()
            server.login(GMAIL_USER, GMAIL_APP_PASS)
            server.sendmail(GMAIL_USER, ALERT_TO, msg.as_string())
        log.info("Email sent: %s → %s", subject, ALERT_TO)
    except Exception as exc:
        log.error("Failed to send email '%s': %s", subject, exc)


def _container_rows(container_states: list) -> str:
    if not container_states:
        return "<tr><td colspan='4' style='color:#888'>No container info available</td></tr>"
    rows = ""
    for cs in container_states:
        ready_color = "#4caf50" if cs.get("ready") else "#f44336"
        ready = "✓" if cs.get("ready") else "✗"
        rows += f"""
        <tr>
          <td style="padding:6px 10px;border-bottom:1px solid #2a2a3a">{cs.get('name','?')}</td>
          <td style="padding:6px 10px;border-bottom:1px solid #2a2a3a;color:{ready_color};text-align:center">{ready}</td>
          <td style="padding:6px 10px;border-bottom:1px solid #2a2a3a;text-align:center">{cs.get('restarts',0)}</td>
          <td style="padding:6px 10px;border-bottom:1px solid #2a2a3a;font-family:monospace;font-size:12px">{cs.get('state','?')}</td>
        </tr>"""
        if cs.get("message"):
            rows += f"""
        <tr>
          <td colspan="4" style="padding:4px 10px 8px;border-bottom:1px solid #2a2a3a;font-size:11px;color:#aaa;font-family:monospace">{cs['message'][:300]}</td>
        </tr>"""
    return rows


def _alert_html(pod) -> str:
    duration = f"{pod.duration_minutes():.1f}"
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    first_seen = pod.first_seen.strftime("%Y-%m-%d %H:%M:%S UTC")
    container_rows = _container_rows(pod.container_states)

    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;background:#0d0d1a;font-family:'Segoe UI',Arial,sans-serif;color:#e0e0e0">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#0d0d1a;padding:30px 0">
    <tr><td align="center">
      <table width="600" cellpadding="0" cellspacing="0" style="background:#13131f;border-radius:8px;border:1px solid #2a2a3a;overflow:hidden">

        <!-- Header -->
        <tr>
          <td style="background:#1e1030;padding:24px 30px;border-bottom:3px solid #7c3aed">
            <span style="font-size:22px;font-weight:700;color:#fff">⚠️ Pod Failure Alert</span>
            <br>
            <span style="font-size:12px;color:#9d8ec7">{now_str}</span>
          </td>
        </tr>

        <!-- Key facts -->
        <tr>
          <td style="padding:24px 30px">
            <table width="100%" cellpadding="0" cellspacing="0">
              <tr>
                <td width="50%" style="padding-bottom:16px;vertical-align:top">
                  <div style="font-size:11px;color:#888;text-transform:uppercase;letter-spacing:1px;margin-bottom:4px">Cluster</div>
                  <div style="font-size:18px;font-weight:600;color:#7c3aed">{pod.cluster}</div>
                </td>
                <td width="50%" style="padding-bottom:16px;vertical-align:top">
                  <div style="font-size:11px;color:#888;text-transform:uppercase;letter-spacing:1px;margin-bottom:4px">Failing For</div>
                  <div style="font-size:18px;font-weight:600;color:#f44336">{duration} minutes</div>
                </td>
              </tr>
              <tr>
                <td style="padding-bottom:16px;vertical-align:top">
                  <div style="font-size:11px;color:#888;text-transform:uppercase;letter-spacing:1px;margin-bottom:4px">Namespace</div>
                  <div style="font-family:monospace;font-size:14px;color:#e0e0e0">{pod.namespace}</div>
                </td>
                <td style="padding-bottom:16px;vertical-align:top">
                  <div style="font-size:11px;color:#888;text-transform:uppercase;letter-spacing:1px;margin-bottom:4px">Pod Name</div>
                  <div style="font-family:monospace;font-size:14px;color:#e0e0e0">{pod.name}</div>
                </td>
              </tr>
              <tr>
                <td style="padding-bottom:16px;vertical-align:top">
                  <div style="font-size:11px;color:#888;text-transform:uppercase;letter-spacing:1px;margin-bottom:4px">Reason</div>
                  <div style="display:inline-block;background:#2a1a1a;border:1px solid #5c1a1a;border-radius:4px;padding:4px 10px;font-family:monospace;font-size:13px;color:#ff6b6b">{pod.reason}</div>
                </td>
                <td style="padding-bottom:16px;vertical-align:top">
                  <div style="font-size:11px;color:#888;text-transform:uppercase;letter-spacing:1px;margin-bottom:4px">Phase</div>
                  <div style="font-family:monospace;font-size:14px;color:#e0e0e0">{pod.phase}</div>
                </td>
              </tr>
              <tr>
                <td colspan="2">
                  <div style="font-size:11px;color:#888;text-transform:uppercase;letter-spacing:1px;margin-bottom:4px">First Seen Failing</div>
                  <div style="font-family:monospace;font-size:13px;color:#e0e0e0">{first_seen}</div>
                </td>
              </tr>
            </table>
          </td>
        </tr>

        <!-- Container states -->
        <tr>
          <td style="padding:0 30px 24px">
            <div style="font-size:11px;color:#888;text-transform:uppercase;letter-spacing:1px;margin-bottom:10px">Container Status</div>
            <table width="100%" cellpadding="0" cellspacing="0" style="background:#0d0d1a;border-radius:6px;border:1px solid #2a2a3a">
              <tr style="background:#1a1a2e">
                <th style="padding:8px 10px;text-align:left;font-size:11px;color:#888;font-weight:600">Container</th>
                <th style="padding:8px 10px;text-align:center;font-size:11px;color:#888;font-weight:600">Ready</th>
                <th style="padding:8px 10px;text-align:center;font-size:11px;color:#888;font-weight:600">Restarts</th>
                <th style="padding:8px 10px;text-align:left;font-size:11px;color:#888;font-weight:600">State</th>
              </tr>
              {container_rows}
            </table>
          </td>
        </tr>

        <!-- Quick commands -->
        <tr>
          <td style="padding:0 30px 24px">
            <div style="font-size:11px;color:#888;text-transform:uppercase;letter-spacing:1px;margin-bottom:10px">Quick Debug Commands</div>
            <div style="background:#0d0d1a;border-radius:6px;border:1px solid #2a2a3a;padding:14px 16px;font-family:monospace;font-size:12px;line-height:1.8;color:#a0a0c0">
              kubectl logs {pod.name} -n {pod.namespace} --previous<br>
              kubectl describe pod {pod.name} -n {pod.namespace}<br>
              kubectl get events -n {pod.namespace} --field-selector involvedObject.name={pod.name}
            </div>
          </td>
        </tr>

        <!-- Footer -->
        <tr>
          <td style="padding:16px 30px;border-top:1px solid #2a2a3a;background:#0d0d1a">
            <span style="font-size:11px;color:#555">K8s Pod Monitor • Alert sent after {pod.duration_minutes():.0f}+ minutes of failure</span>
          </td>
        </tr>

      </table>
    </td></tr>
  </table>
</body>
</html>"""


def _recovery_html(pod) -> str:
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    total_down = f"{pod.duration_minutes():.1f}"
    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;background:#0d0d1a;font-family:'Segoe UI',Arial,sans-serif;color:#e0e0e0">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#0d0d1a;padding:30px 0">
    <tr><td align="center">
      <table width="600" cellpadding="0" cellspacing="0" style="background:#13131f;border-radius:8px;border:1px solid #2a2a3a;overflow:hidden">
        <tr>
          <td style="background:#0f2a1a;padding:24px 30px;border-bottom:3px solid #4caf50">
            <span style="font-size:22px;font-weight:700;color:#fff">✅ Pod Recovered</span>
            <br>
            <span style="font-size:12px;color:#7abf8e">{now_str}</span>
          </td>
        </tr>
        <tr>
          <td style="padding:24px 30px">
            <table width="100%" cellpadding="0" cellspacing="0">
              <tr>
                <td width="50%" style="padding-bottom:16px;vertical-align:top">
                  <div style="font-size:11px;color:#888;text-transform:uppercase;letter-spacing:1px;margin-bottom:4px">Cluster</div>
                  <div style="font-size:18px;font-weight:600;color:#4caf50">{pod.cluster}</div>
                </td>
                <td width="50%" style="padding-bottom:16px;vertical-align:top">
                  <div style="font-size:11px;color:#888;text-transform:uppercase;letter-spacing:1px;margin-bottom:4px">Total Downtime</div>
                  <div style="font-size:18px;font-weight:600;color:#ff9800">{total_down} minutes</div>
                </td>
              </tr>
              <tr>
                <td>
                  <div style="font-size:11px;color:#888;text-transform:uppercase;letter-spacing:1px;margin-bottom:4px">Namespace / Pod</div>
                  <div style="font-family:monospace;font-size:14px;color:#e0e0e0">{pod.namespace} / {pod.name}</div>
                </td>
                <td>
                  <div style="font-size:11px;color:#888;text-transform:uppercase;letter-spacing:1px;margin-bottom:4px">Was Failing With</div>
                  <div style="font-family:monospace;font-size:14px;color:#e0e0e0">{pod.reason}</div>
                </td>
              </tr>
            </table>
          </td>
        </tr>
        <tr>
          <td style="padding:16px 30px;border-top:1px solid #2a2a3a;background:#0d0d1a">
            <span style="font-size:11px;color:#555">K8s Pod Monitor • Pod is now healthy or has been removed</span>
          </td>
        </tr>
      </table>
    </td></tr>
  </table>
</body>
</html>"""


class Alerter:
    async def send_alert(self, pod):
        subject = f"[K8s Alert] {pod.cluster} — {pod.namespace}/{pod.name} ({pod.reason})"
        html = _alert_html(pod)
        text = (
            f"POD FAILURE ALERT\n"
            f"Cluster:   {pod.cluster}\n"
            f"Namespace: {pod.namespace}\n"
            f"Pod:       {pod.name}\n"
            f"Reason:    {pod.reason}\n"
            f"Phase:     {pod.phase}\n"
            f"Duration:  {pod.duration_minutes():.1f} minutes\n"
            f"First seen: {pod.first_seen.strftime('%Y-%m-%d %H:%M:%S UTC')}\n\n"
            f"kubectl logs {pod.name} -n {pod.namespace} --previous\n"
            f"kubectl describe pod {pod.name} -n {pod.namespace}\n"
        )
        _send(subject, html, text)

    async def send_recovery(self, pod):
        subject = f"[K8s Recovered] {pod.cluster} — {pod.namespace}/{pod.name}"
        html = _recovery_html(pod)
        text = (
            f"POD RECOVERED\n"
            f"Cluster:   {pod.cluster}\n"
            f"Namespace: {pod.namespace}\n"
            f"Pod:       {pod.name}\n"
            f"Was:       {pod.reason}\n"
            f"Downtime:  {pod.duration_minutes():.1f} minutes\n"
        )
        _send(subject, html, text)
