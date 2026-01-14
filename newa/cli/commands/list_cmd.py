"""List command for NEWA CLI."""

import logging
import os
from pathlib import Path

import click

from newa import (
    EXECUTE_FILE_PREFIX,
    JIRA_FILE_PREFIX,
    SCHEDULE_FILE_PREFIX,
    CLIContext,
    )


@click.command(name='list')
@click.option(
    '--last',
    default=10,
    help='Print details of recent newa executions.',
    show_default=True,
    )
@click.option(
    '--events',
    is_flag=True,
    default=False,
    help='List details only up to the event level.',
    )
@click.option(
    '--issues',
    is_flag=True,
    default=False,
    help='List details only up to the Jira issue level.',
    )
@click.pass_obj
def cmd_list(ctx: CLIContext, last: int, events: bool, issues: bool) -> None:
    """List NEWA execution details from state directories."""
    ctx.enter_command('list')
    # Ensure --events and --issues are not used together
    if events and issues:
        raise click.UsageError('--events and --issues cannot be used together')
    # save current logger level and statedir
    saved_logger_level = ctx.logger.level
    saved_state_dir = ctx.state_dirpath
    # when not in DEBUG, decrese log verbosity so it won't be too noisy
    # when loading individual YAML files
    if ctx.logger.level != logging.DEBUG:
        ctx.logger.setLevel(logging.WARN)
    # when existing state-dir has been provided, use it
    if ctx.state_dirpath.is_dir():
        state_dirs = [ctx.state_dirpath]
    # otherwise choose last N dirs
    else:
        try:
            entries = os.scandir(ctx.settings.newa_statedir_topdir)
        except FileNotFoundError as e:
            raise Exception(f'{ctx.settings.newa_statedir_topdir} does not exist') from e
        sorted_entries = sorted(entries, key=lambda entry: os.path.getmtime(Path(entry)))
        state_dirs = [Path(e.path) for e in sorted_entries[-last:]]

    def _print(indent: int, s: str, end: str = '\n') -> None:
        print(f'{" " * indent}{s}', end=end)

    for state_dir in state_dirs:
        print(f'{state_dir}:')
        ctx.state_dirpath = state_dir
        event_jobs = list(ctx.load_artifact_jobs())
        for event_job in event_jobs:
            if event_job.erratum:
                _print(2, f'event {event_job.id} - {event_job.erratum.summary}')
                _print(2, event_job.erratum.url)
            elif event_job.rog:
                _print(2, f'event {event_job.id} - {event_job.rog.title}')
            else:
                _print(2, f'event {event_job.id}')
            # Skip Jira issues and other details if --events flag is set
            if events:
                continue
            jira_file_prefix = f'{JIRA_FILE_PREFIX}{event_job.event.short_id}-{event_job.short_id}'
            jira_jobs = list(ctx.load_jira_jobs(jira_file_prefix, filter_actions=True))
            for jira_job in jira_jobs:
                jira_summary = f'- {jira_job.jira.summary}' if jira_job.jira.summary else ''
                jira_action_id = jira_job.jira.action_id or 'no action id'
                _print(4, f'issue {jira_job.jira.id} ({jira_action_id}) {jira_summary}')
                if jira_job.jira.url:
                    _print(4, jira_job.jira.url)
                if jira_job.recipe.url:
                    _print(6, f'recipe: {jira_job.recipe.url}')
                # Skip schedule/execute details if --issues flag is set
                if issues:
                    continue
                schedule_file_prefix = (f'{SCHEDULE_FILE_PREFIX}{event_job.event.short_id}-'
                                        f'{event_job.short_id}-{jira_job.jira.id}')
                schedule_jobs = list(
                    ctx.load_schedule_jobs(
                        schedule_file_prefix,
                        filter_actions=True))
                # print RP launch URL, should be common for all execute jobs
                if schedule_jobs and schedule_jobs[0].request.reportportal:
                    launch_name = schedule_jobs[0].request.reportportal.get('launch_name', None)
                    if launch_name:
                        _print(6, f'ReportPortal launch: {launch_name}')
                        launch_url = schedule_jobs[0].request.reportportal.get('launch_url', None)
                        if launch_url:
                            _print(6, launch_url)
                for schedule_job in schedule_jobs:
                    _print(6, f'{schedule_job.request.id}', end='')
                    execute_file_prefix = (f'{EXECUTE_FILE_PREFIX}{event_job.event.short_id}-'
                                           f'{event_job.short_id}-{jira_job.jira.id}-'
                                           f'{schedule_job.request.id}')
                    execute_jobs = list(
                        ctx.load_execute_jobs(
                            execute_file_prefix,
                            filter_actions=True))
                    if execute_jobs:
                        for execute_job in execute_jobs:
                            if hasattr(execute_job, 'execution'):
                                from newa import RequestResult
                                state = getattr(execute_job.execution, "state", "unknown")
                                # if state was None check of request_uuid
                                if (not state) and getattr(
                                        execute_job.execution, "request_uuid", None):
                                    state = 'executed, not reported'
                                result = getattr(
                                    execute_job.execution, "result", RequestResult.NONE)
                                url = getattr(
                                    execute_job.execution, "artifacts_url", "not available")
                                print(
                                    f' - state: {state}, result: {result.value}, artifacts: {url}')
                    else:
                        print(' - not executed')
        print()
    # restore logger level and statedir
    ctx.logger.setLevel(saved_logger_level)
    ctx.state_dirpath = saved_state_dir
