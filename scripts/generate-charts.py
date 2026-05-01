#!/usr/bin/env python3
"""Top-level chart generation entrypoint."""

import sys

from generate.github_charts import main as github_main
from generate.jira_charts import main as jira_main


def main() -> None:
    argv = sys.argv[1:]
    if argv and argv[0] == "jira":
        jira_main(argv[1:])
        return
    if argv and argv[0] == "github":
        github_main(argv[1:])
        return
    # No subcommand: run both
    github_main(argv)
    jira_main(argv)


if __name__ == "__main__":
    main()
