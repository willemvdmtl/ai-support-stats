#!/usr/bin/env python3
"""Run end-to-end workflow from setup to fetch to chart generation."""

import argparse
import datetime as dt
import os
import subprocess
import sys
from typing import List


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
    parser.add_argument("--setup-capabilities", default="1,2,3,4", help="Capabilities to run in setup step")
    args = parser.parse_args()

    scripts_dir = os.path.dirname(os.path.abspath(__file__))
    py = sys.executable
    month_key = parse_month(args.month)

    preflight_checks(args, scripts_dir, month_key)

    if not args.skip_setup:
        setup_cmd = [py, os.path.join(scripts_dir, "setup.py"), "--capabilities", args.setup_capabilities]
        run_step("Setup", setup_cmd)

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
