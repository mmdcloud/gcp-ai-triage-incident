"""
AI Incident Triage Agent
Built with Claude claude-sonnet-4-6 + tool_use API

Usage:
    python triage_agent.py --service payment-api

Output:
    Prints live tool call trace to terminal
    Writes RCA report to rca_report.md
"""

import os
import json
import argparse
from datetime import datetime, timezone
import anthropic

from tools import query_recent_errors, get_service_status, check_error_pattern

# ── Tool registry ────────────────────────────────────────────
# Maps tool name → Python function
TOOL_REGISTRY = {
    "query_recent_errors": query_recent_errors,
    "get_service_status": get_service_status,
    "check_error_pattern": check_error_pattern,
}

# ── Tool definitions (sent to Claude) ───────────────────────
TOOL_DEFINITIONS = [
    {
        "name": "query_recent_errors",
        "description": (
            "Query Cloud Logging for recent ERROR-level logs from a "
            "Cloud Run service. Returns log entries and error count."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "service_name": {
                    "type": "string",
                    "description": "The Cloud Run service name to query.",
                },
                "minutes": {
                    "type": "integer",
                    "description": "How many minutes back to look. Defaults to 10.",
                    "default": 10,
                },
            },
            "required": ["service_name"],
        },
    },
    {
        "name": "get_service_status",
        "description": (
            "Get current status and revision info for a Cloud Run service. "
            "Returns URL, latest revision, and readiness status."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "service_name": {
                    "type": "string",
                    "description": "The Cloud Run service name.",
                },
            },
            "required": ["service_name"],
        },
    },
    {
        "name": "check_error_pattern",
        "description": (
            "Analyse a list of log entry dicts to identify the dominant "
            "error pattern and return remediation recommendations."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "log_entries": {
                    "type": "array",
                    "description": (
                        "List of log entry dicts from query_recent_errors. "
                        "Each dict has 'timestamp', 'severity', 'message'."
                    ),
                    "items": {"type": "object"},
                },
            },
            "required": ["log_entries"],
        },
    },
]

SYSTEM_PROMPT = """You are an expert SRE Incident Triage Agent.

When given an incident alert, follow this exact playbook:

STEP 1 — get_service_status
Check the service is deployed. Note the revision name and ready status.

STEP 2 — query_recent_errors
Pull error logs for the last 10 minutes. Note the error count and messages.

STEP 3 — check_error_pattern
Pass the log_entries list from Step 2. Get the dominant error type and recommendations.

STEP 4 — Write RCA Report
Produce a structured markdown report using EXACTLY this template:

---
# 🚨 Incident Triage Report

| Field | Value |
|---|---|
| **Service** | <service name> |
| **Severity** | <P1 / P2 / P3> |
| **Status** | ACTIVE |
| **Triage Time** | <current UTC time> |

## Root Cause
<1-2 sentence plain-English summary of what is failing and why>

## Evidence
- **Errors in last 10 min:** <count>
- **Latest revision:** <revision name>
- **Dominant error type:** <error type>
- **Key log signal:** `<one representative log line, max 120 chars>`

## Blast Radius
<Which users or transactions are affected based on the service and error type>

## Recommended Actions
<Paste the numbered recommendations from check_error_pattern>

## Escalate If
- Error rate does not drop within 15 minutes of applying the fix
- Multiple services show the same pattern
- P1 incident — page on-call lead immediately
---

Be direct. No filler. An engineer is reading this at 2 AM.

Severity guide: P1 = data loss / full outage, P2 = partial outage / degraded,
P3 = elevated errors but service functional."""


def run_tool(tool_name: str, tool_input: dict) -> str:
    """Execute a tool and return the result as a JSON string."""
    fn = TOOL_REGISTRY.get(tool_name)
    if not fn:
        return json.dumps({"error": f"Unknown tool: {tool_name}"})
    result = fn(**tool_input)
    return json.dumps(result, default=str)


def triage(service_name: str) -> str:
    """
    Run the full triage loop for a given service.
    Returns the final RCA markdown string.
    """
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    messages = [
        {
            "role": "user",
            "content": (
                f"Incident alert: the `{service_name}` Cloud Run service "
                f"is returning 500 errors. Triage it now."
            ),
        }
    ]

    print(f"\n{'━'*55}")
    print(f"  AI Incident Triage Agent")
    print(f"  Service : {service_name}")
    print(f"  Model   : claude-sonnet-4-6")
    print(f"{'━'*55}\n")

    rca_report = ""

    # ── Agentic loop ─────────────────────────────────────────
    while True:
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2048,
            system=SYSTEM_PROMPT,
            tools=TOOL_DEFINITIONS,
            messages=messages,
        )

        # Append assistant turn to history
        messages.append({"role": "assistant", "content": response.content})

        # ── End condition ─────────────────────────────────────
        if response.stop_reason == "end_turn":
            for block in response.content:
                if hasattr(block, "text"):
                    rca_report = block.text
            break

        # ── Tool use ──────────────────────────────────────────
        if response.stop_reason == "tool_use":
            tool_results = []

            for block in response.content:
                if block.type == "tool_use":
                    print(f"▶ Calling tool : {block.name}")
                    print(f"  Input        : {json.dumps(block.input, indent=2)}")

                    result_str = run_tool(block.name, block.input)
                    result_data = json.loads(result_str)

                    # Print a short summary, not the full dump
                    if block.name == "query_recent_errors":
                        count = result_data.get("error_count", 0)
                        print(f"  Result       : {count} error(s) found\n")
                    elif block.name == "get_service_status":
                        rev = result_data.get("latest_revision", "unknown")
                        ready = result_data.get("ready_status", "unknown")
                        print(f"  Result       : revision={rev} ready={ready}\n")
                    elif block.name == "check_error_pattern":
                        pattern = result_data.get("dominant_error", "unknown")
                        print(f"  Result       : dominant_error={pattern}\n")
                    else:
                        print(f"  Result       : {result_str[:120]}\n")

                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result_str,
                    })

            # Feed results back into the loop
            messages.append({"role": "user", "content": tool_results})

    return rca_report


def save_report(report: str, service_name: str) -> str:
    """Write the RCA markdown to a file and return the filename."""
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    filename = f"rca_{service_name}_{timestamp}.md"
    with open(filename, "w") as f:
        f.write(report)
    return filename


def main():
    parser = argparse.ArgumentParser(description="AI Incident Triage Agent")
    parser.add_argument(
        "--service",
        default="payment-api",
        help="Cloud Run service name to triage (default: payment-api)",
    )
    args = parser.parse_args()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY environment variable not set.")
        raise SystemExit(1)

    if not os.environ.get("GOOGLE_CLOUD_PROJECT"):
        print("ERROR: GOOGLE_CLOUD_PROJECT environment variable not set.")
        raise SystemExit(1)

    report = triage(args.service)

    print("━" * 55)
    print("  RCA REPORT")
    print("━" * 55)
    print(report)

    filename = save_report(report, args.service)
    print(f"\n✓ Report saved → {filename}")


if __name__ == "__main__":
    main()