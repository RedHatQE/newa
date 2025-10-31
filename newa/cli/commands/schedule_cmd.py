"""Schedule command for NEWA CLI."""

import sys

import click

from newa import SCHEDULE_FILE_PREFIX, CLIContext
from newa.cli.schedule_helpers import _process_jira_job
from newa.cli.utils import initialize_state_dir, test_file_presence


@click.command(name='schedule')
@click.option('--arch',
              default=[],
              multiple=True,
              help=('Restrics system architectures to use when scheduling. '
                    'Can be specified multiple times. Example: --arch x86_64'),
              )
@click.option('--fixture',
              'fixtures',
              default=[],
              multiple=True,
              help=('Sets a single fixture default on a cmdline. '
                    'Use with caution, hic sun leones. '
                    'Can be specified multiple times. '
                    'Example: --fixture testingfarm.cli_args="--repository-file URL"'),
              )
@click.option(
    '--no-reportportal',
    is_flag=True,
    default=False,
    help='Do not report test results to ReportPortal.',
    )
@click.pass_obj
def cmd_schedule(
        ctx: CLIContext,
        arch: list[str],
        fixtures: list[str],
        no_reportportal: bool) -> None:
    """
    Schedule subcommand - creates schedule jobs from jira jobs.

    This command processes jira jobs and generates schedule jobs by:
    1. Determining architectures to test
    2. Preparing configuration from recipe and command-line options
    3. Building test requests with Jinja template rendering
    4. Creating and saving schedule job YAML files
    """
    ctx.enter_command('schedule')

    # Ensure state dir is present and initialized
    initialize_state_dir(ctx)

    if ctx.settings.newa_clear_on_subcommand:
        ctx.remove_job_files(SCHEDULE_FILE_PREFIX)

    if test_file_presence(ctx.state_dirpath, SCHEDULE_FILE_PREFIX) and not ctx.force:
        ctx.logger.error(
            f'"{SCHEDULE_FILE_PREFIX}" files already exist in state-dir {ctx.state_dirpath}, '
            'use --force to override')
        sys.exit(1)

    jira_jobs = list(ctx.load_jira_jobs(filter_actions=True))

    if not jira_jobs:
        ctx.logger.warning('Warning: There are no jira jobs to schedule')
        return

    # Process each jira job
    for jira_job in jira_jobs:
        _process_jira_job(ctx, jira_job, arch, fixtures, no_reportportal)
