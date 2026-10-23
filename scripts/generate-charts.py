#!/usr/bin/env python3
"""Top-level chart generation entrypoint."""

import sys

from generate.github_charts import main as github_main
from generate.jira_backlog import main as jira_backlog_main
from generate.jira_charts import main as jira_main


def main() -> None:
    argv = sys.argv[1:]
    if argv and argv[0] == "jira":
        jira_main(argv[1:])
        return
    if argv and argv[0] == "github":
        github_main(argv[1:])
        return
    if argv and argv[0] == "jira-backlog":
        jira_backlog_main(argv[1:])
        return
    # No subcommand: run all standard charts
    github_main(argv)
    jira_main(argv)
    jira_backlog_main([])


if __name__ == "__main__":
    main()
