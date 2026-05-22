#!/usr/bin/env python3
"""Configure minimal Jira setup for basic ticket statistics."""

import argparse
import datetime as dt
import sys

from common.setup_utils import (
    JIRA_CREDENTIAL_SERVICE,
    check_dependencies,
    check_jira_auth,
    credential_delete,
    credential_set,
    ensure_jira_credentials,
    prompt_for_secret,
    prompt_if_missing,
    update_capability_status,
    write_json,
)


DEFAULT_OUTPUT = "config/jira-minimal.json"
DEFAULT_METRIC_GROUPS_OUTPUT = "config/jira-metric-groups.json"


def parse_issue_types(raw: str):
    values = []
    seen = set()
    for part in raw.split(","):
        issue_type = part.strip()
        if not issue_type:
            continue
        key = issue_type.lower()
        if key in seen:
            continue
        values.append(issue_type)
        seen.add(key)
    return values


def parse_csv_values(raw: str):
    values = []
    seen = set()
    for part in raw.split(","):
        value = part.strip()
        if not value:
            continue
        key = value.lower()
        if key in seen:
            continue
        values.append(value)
        seen.add(key)
    return values


def build_metric_groups_config(vertical_issue_types, vertical_tags):
    return {
        "version": 1,
        "defaults": {
            "event_anchor": "resolved_at",
            "window_policy": {
                "mode": "auto",
                "rolling_days_if_month_data_lt_days": 14,
                "month_window_type_if_ready": "month_to_date",
            },
            "validity": {
                "require_created": True,
                "require_resolved": True,
                "exclude_negative_durations": True,
            },
        },
        "groups": [
            {
                "id": "vertical_support",
                "label": "Vertical Support",
                "filter": {
                    "include_any": {
                        "issue_types": vertical_issue_types,
                        "labels": vertical_tags,
                    }
                },
            },
            {
                "id": "ktlo",
                "label": "KTLO",
                "filter": {
                    "exclude_any": {
                        "issue_types": vertical_issue_types,
                        "labels": [],
                    },
                    "force_include_any": {
                        "issue_types": [],
                        "labels": ["ktlo"],
                    },
                },
            },
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--jira-site-service", default="ai-support-stats.jira.site")
    parser.add_argument("--jira-email-service", default="ai-support-stats.jira.email")
    parser.add_argument("--jira-token-service", default="ai-support-stats.jira.api-token")
    parser.add_argument("--project", default="")
    parser.add_argument("--vertical-support-issue-types", default="")
    parser.add_argument("--vertical-support-tags", default="")
    parser.add_argument("--issue-types", default="")
    parser.add_argument("--metric-groups-output", default=DEFAULT_METRIC_GROUPS_OUTPUT)
    args = parser.parse_args()

    print("Step 1/2: Checking dependencies...")
    check_dependencies(require_keyring=True)
    print("OK: dependencies available.")

    print("\nStep 2/2: Validating Jira auth via the system credential store...")
    jira_site, jira_email, jira_token = ensure_jira_credentials(
        args.jira_site_service,
        args.jira_email_service,
        args.jira_token_service,
    )

    jira_ok, jira_payload, jira_error = check_jira_auth(jira_site, jira_email, jira_token)
    if not jira_ok:
        print(f"\nJira auth failed ({jira_error}).")
        print("The stored credentials will be cleared and you will be prompted to re-enter them.")
        credential_delete(JIRA_CREDENTIAL_SERVICE, args.jira_site_service)
        credential_delete(JIRA_CREDENTIAL_SERVICE, args.jira_email_service)
        credential_delete(JIRA_CREDENTIAL_SERVICE, args.jira_token_service)

        print("\nCreate or regenerate your Jira API token at:")
        print("  https://id.atlassian.com/manage-profile/security/api-tokens")
        print()
        jira_site = input("Jira site [trainline.atlassian.net]: ").strip() or "trainline.atlassian.net"
        jira_email = input("Jira email: ").strip()
        jira_token = prompt_for_secret("Jira API token: ")

        if not jira_site or not jira_email or not jira_token:
            print("ERROR: All three values are required.")
            sys.exit(1)

        credential_set(JIRA_CREDENTIAL_SERVICE, args.jira_site_service, jira_site)
        credential_set(JIRA_CREDENTIAL_SERVICE, args.jira_email_service, jira_email)
        credential_set(JIRA_CREDENTIAL_SERVICE, args.jira_token_service, jira_token)

        jira_ok, jira_payload, jira_error = check_jira_auth(jira_site, jira_email, jira_token)
        if not jira_ok:
            print(f"ERROR: Jira auth still failed: {jira_error}")
            print("Check that the site, email, and token are correct and try again.")
            sys.exit(1)

    print(f"OK: Jira auth works as user: {jira_payload.get('displayName', 'unknown')}")

    project = prompt_if_missing(args.project.strip(), "Jira project key [ECOM]: ", default="ECOM")
    issue_types_input = args.vertical_support_issue_types.strip() or args.issue_types.strip()
    issue_types_raw = prompt_if_missing(
        issue_types_input,
        "Vertical Support issue types (comma-separated) [PR Request,ExternalRequest]: ",
        default="PR Request,ExternalRequest",
    )
    tags_raw = prompt_if_missing(
        args.vertical_support_tags.strip(),
        "Vertical Support tags/labels (comma-separated) [none]: ",
        default="",
    )
    issue_types = parse_issue_types(issue_types_raw)
    tags = parse_csv_values(tags_raw)

    if not project:
        print("ERROR: project is required.")
        sys.exit(1)

    if not issue_types and not tags:
        print("ERROR: configure at least one Vertical Support issue type or tag.")
        sys.exit(1)

    payload = {
        "configured_at": dt.datetime.utcnow().isoformat() + "Z",
        "site": jira_site,
        "email": jira_email,
        "credential_service": JIRA_CREDENTIAL_SERVICE,
        "credential_usernames": {
            "site": args.jira_site_service,
            "email": args.jira_email_service,
            "api_token": args.jira_token_service,
        },
        "project": project,
        "vertical_support": {
            "issue_types": issue_types,
            "tags": tags,
        },
        # Backward compatibility for existing consumers.
        "issue_types": issue_types,
    }
    write_json(args.output, payload)

    metric_groups_payload = build_metric_groups_config(issue_types, tags)
    write_json(args.metric_groups_output, metric_groups_payload)

    update_capability_status(
        "jira_minimal",
        True,
        {
            "site": jira_site,
            "project": project,
            "vertical_support_issue_type_count": len(issue_types),
            "vertical_support_tag_count": len(tags),
            "config_file": args.output,
            "metric_groups_file": args.metric_groups_output,
        },
    )

    print("\nJira minimal configuration saved.")
    print(f"Output file: {args.output}")
    print(f"Metric groups file: {args.metric_groups_output}")


if __name__ == "__main__":
    main()
