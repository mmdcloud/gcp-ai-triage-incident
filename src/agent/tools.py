"""
Tool implementations for the Incident Triage Agent.
These are called by the Claude API tool_use loop.
"""

import os
import json
import subprocess
from datetime import datetime, timedelta, timezone
from google.cloud import logging as cloud_logging


def query_recent_errors(service_name: str, minutes: int = 10) -> dict:
    """Pull recent ERROR logs from Cloud Logging for a Cloud Run service."""
    try:
        client = cloud_logging.Client()
        since = datetime.now(timezone.utc) - timedelta(minutes=minutes)
        timestamp_filter = since.strftime("%Y-%m-%dT%H:%M:%SZ")

        log_filter = (
            f'resource.type="cloud_run_revision" '
            f'resource.labels.service_name="{service_name}" '
            f'severity>=ERROR '
            f'timestamp>="{timestamp_filter}"'
        )

        entries = []
        for entry in client.list_entries(filter_=log_filter, max_results=20):
            payload = (
                entry.payload if isinstance(entry.payload, str)
                else str(entry.payload)
            )
            entries.append({
                "timestamp": entry.timestamp.isoformat() if entry.timestamp else "unknown",
                "severity": str(entry.severity),
                "message": payload[:500],
            })

        return {
            "service_name": service_name,
            "window_minutes": minutes,
            "error_count": len(entries),
            "log_entries": entries,
        }

    except Exception as e:
        return {
            "service_name": service_name,
            "error": f"Failed to query logs: {str(e)}",
            "log_entries": [],
            "error_count": 0,
        }


def get_service_status(service_name: str) -> dict:
    """Get current Cloud Run service status and revision info."""
    try:
        project_id = os.environ.get("GOOGLE_CLOUD_PROJECT", "")
        region = os.environ.get("CLOUD_RUN_REGION", "us-central1")

        result = subprocess.run(
            [
                "gcloud", "run", "services", "describe", service_name,
                "--region", region,
                "--project", project_id,
                "--format",
                "json(status.url,status.conditions,"
                "status.latestCreatedRevisionName,"
                "status.latestReadyRevisionName)"
            ],
            capture_output=True, text=True, timeout=15
        )

        if result.returncode != 0:
            return {"service_name": service_name, "error": result.stderr.strip()}

        data = json.loads(result.stdout)
        conditions = data.get("status", {}).get("conditions", [])
        ready = next((c for c in conditions if c.get("type") == "Ready"), {})

        return {
            "service_name": service_name,
            "url": data.get("status", {}).get("url", "unknown"),
            "latest_revision": data.get("status", {}).get(
                "latestCreatedRevisionName", "unknown"
            ),
            "ready_revision": data.get("status", {}).get(
                "latestReadyRevisionName", "unknown"
            ),
            "ready_status": ready.get("status", "unknown"),
            "ready_message": ready.get("message", ""),
        }

    except Exception as e:
        return {"service_name": service_name, "error": str(e)}


def check_error_pattern(log_entries: list) -> dict:
    """Identify the dominant error pattern from log entries."""
    if not log_entries:
        return {
            "dominant_error": "none",
            "pattern_summary": "No errors found in the provided log entries.",
            "recommendation": "Service appears healthy. Monitor for recurrence.",
        }

    full_text = " ".join(
        e.get("message", "") for e in log_entries
    ).lower()

    if "connection pool" in full_text or "max_connections" in full_text:
        return {
            "dominant_error": "DatabaseConnectionPoolExhausted",
            "pattern_summary": (
                f"{len(log_entries)} errors detected. All point to DB connection "
                "pool exhaustion. Pattern is consistent — not intermittent."
            ),
            "recommendation": (
                "1. Check recent deployments for N+1 query regressions.\n"
                "2. Increase DB connection pool size (max_connections).\n"
                "3. Consider adding a connection pooler (e.g. PgBouncer).\n"
                "4. Roll back last deployment if query count spike is confirmed."
            ),
        }

    if "timeout" in full_text or "deadline exceeded" in full_text:
        return {
            "dominant_error": "RequestTimeout",
            "pattern_summary": (
                f"{len(log_entries)} timeout errors. Likely downstream latency."
            ),
            "recommendation": (
                "1. Check upstream dependency latency (DB, external APIs).\n"
                "2. Review Cloud Run CPU/memory allocation.\n"
                "3. Check for traffic spike causing resource contention."
            ),
        }

    if "memory" in full_text or "oom" in full_text:
        return {
            "dominant_error": "MemoryPressure",
            "pattern_summary": f"{len(log_entries)} memory-related errors.",
            "recommendation": (
                "1. Increase Cloud Run memory limit.\n"
                "2. Profile for memory leaks in recent code changes.\n"
                "3. Check if a new dependency increased baseline memory usage."
            ),
        }

    return {
        "dominant_error": "UnclassifiedError",
        "pattern_summary": (
            f"{len(log_entries)} errors. Pattern unclear from log content."
        ),
        "recommendation": "Manual investigation required. Review full logs in Cloud Logging.",
    }