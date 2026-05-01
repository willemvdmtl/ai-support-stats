#!/usr/bin/env python3
"""Run end-to-end workflow from setup to fetch to chart generation."""

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
from typing import Dict, List

from common.setup_utils import check_python_requirements


CAPABILITY_ID_TO_KEY: Dict[str, str] = {
    "1": "github_minimal",
    "2": "github_internal_team",
    "3": "jira_minimal",
    "4": "jira_org_structure",
        "5": "jira_service_normalization",
}


def run_step(label: str, cmd: List[str]) -> None:
    print(f"\n=== {label} ===")
    print("$ " + " ".join(cmd))
    result = subprocess.run(cmd, check=False)
    if result.returncode != 0:
        print(f"ERROR: {label} failed with exit code {result.returncode}.")
        sys.exit(result.returncode)


def parse_month(raw: str) -> str:
    if not raw:
        today = dt.date.today()
        return f"{today.year}-{today.month:02d}"
    try:
        parsed = dt.datetime.strptime(raw, "%Y-%m")
    except ValueError:
        print(f"ERROR: Invalid --month value '{raw}'. Use YYYY-MM")
        print("Fix: python3 scripts/run-workflow.py --month 2026-04")
        sys.exit(1)
    return f"{parsed.year}-{parsed.month:02d}"


def ensure_file_exists(path: str, reason: str, fix_cmd: str) -> None:
    if os.path.exists(path):
        return
    print("ERROR: Missing prerequisite file:")
    print(f"  {path}")
    print(f"Reason: {reason}")
    print("Fix:")
    print(f"  {fix_cmd}")
    sys.exit(1)


def resolve_with_legacy(path: str, legacy: str) -> str:
    if os.path.exists(path):
        return path
    if os.path.exists(legacy):
        return legacy
    return path


def read_json(path: str) -> Dict:
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def parse_capability_ids(raw: str) -> List[str]:
    normalized = raw.strip().lower()
    if not normalized or normalized == "all":
        return list(CAPABILITY_ID_TO_KEY.keys())

    selected: List[str] = []
    seen = set()
    for token in (item.strip() for item in raw.split(",")):
        if not token:
            continue
        if token not in CAPABILITY_ID_TO_KEY:
            print(f"ERROR: Unknown setup capability '{token}'.")
            print("Valid options: " + ", ".join(CAPABILITY_ID_TO_KEY))
            sys.exit(1)
        if token not in seen:
            selected.append(token)
            seen.add(token)
    if not selected:
        return list(CAPABILITY_ID_TO_KEY.keys())
    return selected


def capability_is_configured(capability_key: str, payload: Dict) -> bool:
    capabilities = payload.get("capabilities") or {}
    entry = capabilities.get(capability_key) or {}
    return bool(entry.get("configured"))


def pending_setup_capabilities(raw: str, capabilities_path: str = "config/capabilities.json") -> List[str]:
    selected = parse_capability_ids(raw)
    payload = read_json(capabilities_path)
    return [cap_id for cap_id in selected if not capability_is_configured(CAPABILITY_ID_TO_KEY[cap_id], payload)]


def preflight_checks(args: argparse.Namespace, scripts_dir: str, month_key: str) -> None:
    """Fail fast with actionable guidance when required inputs are missing."""
    will_setup = not args.skip_setup
    will_fetch = not args.skip_fetch
    will_generate = not args.skip_generate

    if will_fetch and not will_setup:
        repos_file = resolve_with_legacy("config/owned-repositories.json", "data/config/owned-repositories.json")
        ensure_file_exists(
            path=repos_file,
            reason="fetch step requires discovered repositories from setup capability 1",
            fix_cmd="python3 scripts/setup.py --capabilities 1",
        )

    if will_generate and not will_fetch:
        ensure_file_exists(
            path=f"cache/github/prs-{month_key}.json",
            reason="generate step requires consolidated PR cache for the target month",
            fix_cmd=f"python3 scripts/fetch-data.py github --month {month_key}",
        )

    if will_generate and not will_setup:
        # Optional chart can run without this, but warn users how to enable split.
        internal_team_path = resolve_with_legacy("config/github-internal-team.json", "data/config/github-internal-team.json")
        if not os.path.exists(internal_team_path):
            print("WARNING: Internal/external split config is missing:")
            print(f"  {internal_team_path}")
            print("The split chart will be skipped unless you configure capability 2.")
            print("Optional fix:")
            print("  python3 scripts/setup.py --capabilities 2")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run setup + fetch + generate workflow.")
    parser.add_argument("--month", default="", help="Target month (YYYY-MM). Defaults to current month.")
    parser.add_argument("--skip-setup", action="store_true", help="Skip setup step")
    parser.add_argument("--skip-fetch", action="store_true", help="Skip fetch step")
    parser.add_argument("--skip-generate", action="store_true", help="Skip chart generation step")
    parser.add_argument("--force-fetch", action="store_true", help="Pass --force to fetch step")
    parser.add_argument("--setup-capabilities", default="1,2,3,4,5", help="Capabilities to run in setup step")
    args = parser.parse_args()

    scripts_dir = os.path.dirname(os.path.abspath(__file__))
    requirements_path = os.path.join(os.path.dirname(scripts_dir), "requirements.txt")
    check_python_requirements(requirements_path)

    py = sys.executable
    month_key = parse_month(args.month)
    setup_capabilities = pending_setup_capabilities(args.setup_capabilities)

    preflight_checks(args, scripts_dir, month_key)

    if not args.skip_setup:
        if setup_capabilities:
            setup_cmd = [
                py,
                os.path.join(scripts_dir, "setup.py"),
                "--capabilities",
                ",".join(setup_capabilities),
            ]
            run_step("Setup", setup_cmd)
        else:
            print("\n=== Setup ===")
            print("All selected setup capabilities are already configured. Skipping setup.")

    if not args.skip_fetch:
        fetch_cmd = [py, os.path.join(scripts_dir, "fetch-data.py"), "github", "--month", month_key]
        if args.force_fetch:
            fetch_cmd.append("--force")
        run_step("Fetch PR data", fetch_cmd)

        fetch_cmd = [py, os.path.join(scripts_dir, "fetch-data.py"), "jira", "--month", month_key]
        if args.force_fetch:
            fetch_cmd.append("--force")
        run_step("Fetch Jira data", fetch_cmd)

    if not args.skip_generate:
        generate_cmd = [py, os.path.join(scripts_dir, "generate-charts.py")]
        generate_cmd.extend(["--month", month_key])
        run_step("Generate charts", generate_cmd)

        report_cmd = [py, os.path.join(scripts_dir, "generate-report.py")]
        report_cmd.extend(["--month", month_key])
        run_step("Generate markdown report", report_cmd)

    print("\nWorkflow complete.")


if __name__ == "__main__":
    main()
