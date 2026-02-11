"""Execute command for NEWA CLI."""

from typing import cast

import click

from newa import EXECUTE_FILE_PREFIX, CLIContext, RoGTool, ScheduleJob, check_tf_cli_version
from newa.cli.execute_helpers import (
    _check_execute_file_conflicts,
    _create_jira_job_mapping,
    _execute_worker_pool,
    _finalize_rp_launches,
    _initialize_execute_environment,
    _process_rp_launches_and_jira_updates,
    _validate_execute_parameters,
    )
from newa.cli.initialization import initialize_et_connection, initialize_rp_connection
from newa.cli.utils import initialize_state_dir


@click.command(name='execute')
@click.option(
    '--workers',
    default=0,
    help='Limits the number of requests executed in parallel (default = 0, unlimited).',
    )
@click.option(
    '--continue',
    '-C',
    '_continue',
    is_flag=True,
    default=False,
    help='Continue with the previous execution, expects --state-dir usage.',
    )
@click.option('--restart-request',
              '-R',
              default=[],
              multiple=True,
              help=('Restart NEWA request with the given request ID. '
                    'Can be specified multiple times. Implies --continue. '
                    'Example: --restart-request REQ-1.2.1'),
              )
@click.option('--restart-result',
              '-r',
              default=[],
              multiple=True,
              help=('Restart finished TF jobs having the specified result. '
                    'Can be specified multiple times. Implies --continue. '
                    'Example: --restart-result error'),
              )
@click.option(
    '--no-wait',
    is_flag=True,
    default=False,
    help='Do not wait for TF requests to finish.',
    )
@click.pass_obj
def cmd_execute(
        ctx: CLIContext,
        workers: int,
        _continue: bool,
        no_wait: bool,
        restart_request: list[str],
        restart_result: list[str]) -> None:
    """
    Execute scheduled test jobs.

    This command processes scheduled jobs and executes them using Testing Farm or TMT.
    It handles ReportPortal launch creation, Jira updates, and parallel execution.
    """
    ctx.enter_command('execute')

    # Initialize state directory
    initialize_state_dir(ctx)

    # Set no_wait flag
    ctx.no_wait = no_wait

    # Validate parameters and configure execution mode
    _validate_execute_parameters(ctx, _continue, restart_request, restart_result)

    # Handle --clear option
    if ctx.settings.newa_clear_on_subcommand:
        ctx.remove_job_files(EXECUTE_FILE_PREFIX)

    # Check for execute file conflicts
    _check_execute_file_conflicts(ctx)

    # Verify TF CLI version
    check_tf_cli_version(ctx)

    # Load schedule jobs and validate there are jobs to execute
    schedule_job_list = list(ctx.load_schedule_jobs(filter_actions=True))
    if not schedule_job_list:
        ctx.logger.warning('Warning: There are no previously scheduled jobs to execute')
        return

    # Initialize ReportPortal connection
    rp = initialize_rp_connection(ctx)

    # Initialize Errata Tool connection if needed
    et = initialize_et_connection(ctx) if ctx.settings.et_enable_comments else None

    # Initialize RoG connection if needed
    rog = (RoGTool(token=ctx.settings.rog_token)
           if ctx.settings.rog_enable_comments and ctx.settings.rog_token
           else None)

    # Initialize environment (timestamp and TF token)
    _initialize_execute_environment(ctx)

    # Group schedule jobs by Jira ID
    jira_schedule_job_mapping = cast(
        dict[str, list[ScheduleJob]],
        _create_jira_job_mapping(schedule_job_list))

    # Process RP launches and update Jira issues
    launch_list = _process_rp_launches_and_jira_updates(
        ctx, rp, et, rog, jira_schedule_job_mapping)

    # Execute worker pool to process all schedule jobs
    try:
        _execute_worker_pool(ctx, schedule_job_list, workers)
    except KeyboardInterrupt:
        # Let _execute_worker_pool handle user-facing logging; just translate to Click's Abort.
        raise click.Abort from None

    # Finalize RP launches after execution
    _finalize_rp_launches(ctx, rp, launch_list)
