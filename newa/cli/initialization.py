"""Initialization functions for external service connections."""

from typing import Any

import jira

from newa import CLIContext, ErrataTool, ReportPortal


def initialize_jira_connection(ctx: CLIContext) -> Any:
    """Initialize Jira connection."""
    jira_url = ctx.settings.jira_url
    if not jira_url:
        raise Exception('Jira URL is not configured!')
    jira_token = ctx.settings.jira_token
    if not jira_token:
        raise Exception('Jira token is not configured!')
    return jira.JIRA(jira_url, token_auth=jira_token)


def initialize_rp_connection(ctx: CLIContext) -> ReportPortal:
    """Initialize ReportPortal connection."""
    rp_project = ctx.settings.rp_project
    rp_url = ctx.settings.rp_url
    rp = ReportPortal(url=rp_url,
                      token=ctx.settings.rp_token,
                      project=rp_project)
    rp.check_connection(rp_url, ctx.logger)
    return rp


def initialize_et_connection(ctx: CLIContext) -> ErrataTool:
    """Initialize ErrataTool connection."""
    et_url = ctx.settings.et_url
    if not et_url:
        raise Exception('Errata Tool URL is not configured!')

    et = ErrataTool(url=ctx.settings.et_url)
    et.check_connection(et_url, ctx.logger)
    return et


def issue_transition(connection: Any, transition: str, issue_id: str) -> None:
    """Transition a Jira issue to a new state."""
    try:
        # if the transition has a format status.resolution close with resolution
        if '.' in transition:
            status, resolution = transition.split('.', 1)
            connection.transition_issue(issue_id,
                                        transition=status,
                                        resolution={'name': resolution})
        # otherwise close just using the status
        else:
            connection.transition_issue(issue_id,
                                        transition=transition)
    except jira.JIRAError as e:
        raise Exception(f"Cannot transition issue {issue_id} into {transition}!") from e
