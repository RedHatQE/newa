"""Report command for NEWA CLI."""

from typing import cast

import click

from newa import CLIContext, ExecuteJob, RoGTool
from newa.cli.execute_helpers import _create_jira_job_mapping
from newa.cli.initialization import (
    initialize_et_connection,
    initialize_rp_connection,
    )
from newa.cli.report_helpers import (
    _process_jira_id_reports,
    _update_all_tf_request_statuses,
    )
from newa.cli.utils import initialize_state_dir


@click.command(name='report')
@click.pass_obj
def cmd_report(ctx: CLIContext) -> None:
    """
    Report test execution results to Jira, ReportPortal, and Errata Tool.

    This command:
    1. Loads execute jobs and checks TF request status
    2. Groups jobs by Jira ID
    3. Finalizes ReportPortal launches
    4. Updates Jira issues with comments and transitions
    5. Updates Errata Tool with comments
    """
    ctx.enter_command('report')

    # Initialize state directory
    initialize_state_dir(ctx)

    # Load execute jobs
    all_execute_jobs = list(ctx.load_execute_jobs(filter_actions=True))
    if not all_execute_jobs:
        ctx.logger.warning('Warning: There are no previously executed jobs to report')
        return

    # Initialize connections
    rp = initialize_rp_connection(ctx) if ctx.settings.rp_url else None
    jira_connection = ctx.get_jira_connection().get_connection()
    et = initialize_et_connection(ctx) if ctx.settings.et_enable_comments else None
    rog = (RoGTool(token=ctx.settings.rog_token)
           if ctx.settings.rog_enable_comments and ctx.settings.rog_token
           else None)

    # Update TF request statuses for all jobs
    _update_all_tf_request_statuses(ctx, all_execute_jobs)

    # Group execute jobs by Jira ID using existing helper
    jira_execute_job_mapping = cast(
        dict[str, list[ExecuteJob]],
        _create_jira_job_mapping(all_execute_jobs))

    # Process reports for each Jira ID
    for jira_id, execute_jobs in jira_execute_job_mapping.items():
        _process_jira_id_reports(
            ctx, jira_id, execute_jobs, rp, jira_connection, et, rog)
