"""Summarize command for NEWA CLI."""

from typing import Any

import click
from jira import JIRA

from newa import CLIContext, ExecuteJob
from newa.cli.initialization import initialize_jira_connection, initialize_rp_connection
from newa.cli.summarize_helpers import (
    collect_launch_details,
    format_jira_issue_details,
    )
from newa.cli.utils import initialize_state_dir
from newa.services.ai_service import AIService


def fetch_jira_issues_bulk(jira_client: JIRA, issue_keys: list[str]) -> dict[str, dict[str, Any]]:
    """Fetch detailed information for multiple Jira issues in a single query.

    Args:
        jira_client: Authenticated Jira client
        issue_keys: List of Jira issue keys (e.g., ['RHEL-12345', 'RHEL-67890'])

    Returns:
        Dictionary mapping issue key to issue details
    """
    if not issue_keys:
        return {}

    # Remove '???' entries if present
    valid_keys = [k for k in issue_keys if k != '???']
    if not valid_keys:
        return {}

    try:
        # Build JQL query to fetch all issues at once
        jql = f"key in ({','.join(valid_keys)})"
        issues = jira_client.search_issues(jql, maxResults=len(valid_keys))

        result = {}
        for issue in issues:
            # Extract component names
            components = [
                c.name for c in issue.fields.components] if issue.fields.components else []

            # Extract version names
            affects_versions = [
                v.name for v in issue.fields.versions] if issue.fields.versions else []
            fix_versions = [
                v.name for v in issue.fields.fixVersions] if issue.fields.fixVersions else []

            result[issue.key] = {
                'key': issue.key,
                'summary': issue.fields.summary,
                'status': issue.fields.status.name,
                'components': components,
                'affects_versions': affects_versions,
                'fix_versions': fix_versions,
                }

        return result
    except Exception as e:
        raise Exception(f'Failed to fetch Jira issues: {e}') from e


def process_execute_job_for_summary(
        ctx: CLIContext,
        execute_job: ExecuteJob,
        rp: Any,
        jira_client: JIRA,
        ai_service: AIService) -> None:
    """Process a single execute job and add AI summary to its Jira issue.

    Args:
        ctx: CLI context
        execute_job: The execute job to process
        rp: ReportPortal service instance
        jira_client: Authenticated Jira client
        ai_service: AI service instance
    """
    jira_id = execute_job.jira.id

    # Check if the execute job has ReportPortal launch metadata
    if not execute_job.request.reportportal:
        ctx.logger.debug(
            f'Skipping {jira_id}: No ReportPortal launch metadata in execute job')
        return

    launch_uuid = execute_job.request.reportportal.get('launch_uuid')
    if not launch_uuid:
        ctx.logger.debug(f'Skipping {jira_id}: No launch_uuid in ReportPortal metadata')
        return

    ctx.logger.info(f'Processing {jira_id} with RP launch {launch_uuid}')

    # Get launch info to convert UUID to ID
    try:
        launch_info = rp.get_launch_info(launch_uuid)
        if not launch_info:
            ctx.logger.warning(
                f'Could not find launch {launch_uuid} in ReportPortal project '
                f'{ctx.settings.rp_project}')
            return
        launch_id = launch_info['id']
    except Exception as e:
        ctx.logger.error(f'Error getting launch info for {launch_uuid}: {e}')
        return

    # Collect launch details
    ctx.logger.info(f'Collecting data from RP launch {launch_id}')
    output_lines, all_jira_issues = collect_launch_details(
        rp, ctx.settings.rp_url, ctx.settings.rp_project, launch_id, ctx.logger)

    # Fetch and format Jira issue details
    if all_jira_issues:
        ctx.logger.info(f'Fetching details for {len(all_jira_issues)} Jira issues')
        jira_issues_data = fetch_jira_issues_bulk(jira_client, list(all_jira_issues))
        output_lines.extend(format_jira_issue_details(jira_issues_data))

    # Combine all output into a single string for AI processing
    user_message = '\n'.join(output_lines)

    # Query AI model
    ctx.logger.info(f'Querying AI model for summary of RP launch {launch_uuid}')
    try:
        ai_summary = ai_service.query_ai_model(user_message)
    except Exception as e:
        ctx.logger.error(f'Error querying AI model: {e}')
        return

    # Add comment to Jira issue
    ctx.logger.info(f'Adding AI summary comment to {jira_id}')
    try:
        comment = f"NEWA AI-generated ReportPortal launch summary:\n\n{ai_summary}"
        jira_client.add_comment(
            jira_id,
            comment,
            visibility={
                'type': 'group',
                'value': execute_job.jira.group}
            if execute_job.jira.group else None)
        ctx.logger.info(f'Successfully added AI summary to {jira_id}')
    except Exception as e:
        ctx.logger.error(f'Error adding comment to Jira issue {jira_id}: {e}')


@click.command(name='summarize')
@click.pass_obj
def cmd_summarize(ctx: CLIContext) -> None:
    """
    Generate AI summaries of ReportPortal launches and update Jira issues.

    This command:
    1. Loads execute jobs from state directory
    2. For each job with ReportPortal launch metadata:
       - Collects test execution data from ReportPortal
       - Generates AI summary using configured AI service
       - Updates the corresponding Jira issue with the summary
    """
    ctx.enter_command('summarize')

    # Initialize state directory
    initialize_state_dir(ctx)

    # Load execute jobs
    all_execute_jobs = list(ctx.load_execute_jobs(filter_actions=True))
    if not all_execute_jobs:
        ctx.logger.warning('Warning: There are no execute jobs to summarize')
        return

    # Check AI configuration
    if not ctx.settings.ai_api_url or not ctx.settings.ai_api_token:
        ctx.logger.error(
            'AI API URL and token must be configured in newa.conf [ai] section or '
            'via NEWA_AI_API_URL and NEWA_AI_API_TOKEN environment variables')
        return

    # Initialize services
    rp = initialize_rp_connection(ctx) if ctx.settings.rp_url else None
    if not rp:
        ctx.logger.error('ReportPortal URL must be configured to use summarize command')
        return

    jira_connection = initialize_jira_connection(ctx)

    ai_service = AIService(
        api_url=ctx.settings.ai_api_url,
        api_token=ctx.settings.ai_api_token,
        model=ctx.settings.ai_api_model)

    # Track processed launch UUIDs to avoid duplicate summaries
    processed_launches: set[str] = set()

    # Process each execute job
    for execute_job in all_execute_jobs:
        try:
            # Check if launch has already been processed
            if execute_job.request.reportportal:
                launch_uuid = execute_job.request.reportportal.get('launch_uuid')
                if launch_uuid and launch_uuid in processed_launches:
                    ctx.logger.debug(
                        f'Skipping {execute_job.jira.id}: RP launch {launch_uuid} '
                        'already processed')
                    continue

            process_execute_job_for_summary(
                ctx, execute_job, rp, jira_connection, ai_service)

            # Mark launch as processed
            if execute_job.request.reportportal:
                launch_uuid = execute_job.request.reportportal.get('launch_uuid')
                if launch_uuid:
                    processed_launches.add(launch_uuid)

        except Exception as e:
            ctx.logger.error(
                f'Error processing execute job {execute_job.id}: {e}')
            continue

    ctx.logger.info('Summarize command completed')
