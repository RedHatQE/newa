"""Jira command for NEWA CLI."""

import os
import sys

import click

from newa import (
    JIRA_FILE_PREFIX,
    CLIContext,
    IssueConfig,
    IssueHandler,
    short_sleep,
    )
from newa.cli.initialization import initialize_et_connection
from newa.cli.jira_helpers import (
    _create_jira_fake_id_generator,
    _create_simple_jira_job,
    _parse_issue_mapping,
    _process_issue_config,
    )
from newa.cli.utils import initialize_state_dir, test_file_presence


@click.command(name='jira')
@click.option(
    '--issue-config',
    help='Specifies path to a Jira issue configuration file.',
    )
@click.option(
    '--map-issue',
    default=[],
    multiple=True,
    help=('Map issue id from the issue-config file to an existing Jira issue. '
          'Example: --map-issue jira_epic=RHEL-123456'),
    )
@click.option(
    '--no-newa-id',
    is_flag=True,
    default=False,
    help='Do not update issue with newa identifier and ignore any existing ones.',
    )
@click.option(
    '--recreate',
    is_flag=True,
    default=False,
    help='Instructs newa to ignore closed isseus and created new ones.',
    )
@click.option(
    '--issue',
    help='Specifies Jira issue ID to be used.',
    )
@click.option(
    '--prev-issue',
    is_flag=True,
    default=False,
    help='Use the (only) issue from the previous NEWA state-dir.',
    )
@click.option(
    '--job-recipe',
    help='Specifies job recipe file or URL to be used.',
    )
@click.option(
    '--assignee', 'assignee',
    help='Overrides Jira assignee from the issue config file.',
    default=None,
    )
@click.option(
    '--unassigned',
    is_flag=True,
    default=False,
    help='Create unassigned Jira issues, overriding values from the issue config file.',
    )
@click.pass_obj
def cmd_jira(
        ctx: CLIContext,
        issue_config: str,
        map_issue: list[str],
        no_newa_id: bool,
        recreate: bool,
        issue: str,
        prev_issue: bool,
        job_recipe: str,
        assignee: str,
        unassigned: bool) -> None:
    """
    Process Jira subcommand to create/update Jira issues and associated jobs.

    This command supports two modes:
    1. Using issue-config file: Complex workflow with multiple actions,
       iterations, and dependencies
    2. Simple mode: Direct issue and recipe specification
    """
    ctx.enter_command('jira')

    # Initialize state directory
    initialize_state_dir(ctx)

    # Handle --clear option
    if ctx.settings.newa_clear_on_subcommand:
        ctx.remove_job_files(JIRA_FILE_PREFIX)

    # Check for existing files unless --force is used
    if test_file_presence(ctx.state_dirpath, JIRA_FILE_PREFIX) and not ctx.force:
        ctx.logger.error(
            f'"{JIRA_FILE_PREFIX}" files already exist in state-dir {ctx.state_dirpath}, '
            'use --force to override')
        sys.exit(1)

    # Validate Jira configuration
    jira_url = ctx.settings.jira_url
    if not jira_url:
        raise Exception('Jira URL is not configured!')

    jira_token = ctx.settings.jira_token
    if not jira_token:
        raise Exception('Jira token is not configured!')

    # Validate assignee options
    if assignee and unassigned:
        raise Exception('Options --assignee and --unassigned cannot be used together')

    # Initialize Errata Tool connection
    et = None
    if ctx.settings.et_enable_comments:
        et = initialize_et_connection(ctx)

    # Initialize fake Jira ID generator
    jira_none_id = _create_jira_fake_id_generator()

    # Load artifact jobs
    artifact_jobs = ctx.load_artifact_jobs()

    # Process each artifact job
    for artifact_job in artifact_jobs:
        if issue_config:
            # Mode 1: Using issue-config file
            # we are reading the issue config again for each artifact
            # because later we modify some objects
            config = IssueConfig.read_file(os.path.expandvars(issue_config))
            issue_mapping = _parse_issue_mapping(map_issue, config)

            # Initialize Jira handler
            jira_handler = IssueHandler(
                artifact_job,
                jira_url,
                jira_token,
                config.project,
                config.transitions,
                board=config.board,
                group=getattr(config, 'group', None))
            ctx.logger.info("Initialized Jira handler")
            short_sleep()

            # Process all issue actions from config
            _process_issue_config(
                ctx, artifact_job, config, issue_mapping,
                no_newa_id, recreate, assignee, unassigned,
                jira_handler, et)
        else:
            # Mode 2: Simple mode with --issue and --job-recipe
            _create_simple_jira_job(
                ctx, artifact_job, issue, prev_issue,
                job_recipe, jira_none_id)
