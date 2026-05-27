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
from newa.cli.color_utils import (
    Colors,
    colorize_result,
    colorize_state,
    colorize_text,
    init_colors,
    )
from newa.cli.report_helpers import _update_all_tf_request_statuses


@click.command(name='list')
@click.option(
    '--last',
    default=10,
    help='Print details of recent newa executions.',
    show_default=True,
    )
@click.option(
    '--all', '-a',
    'list_all',
    is_flag=True,
    default=False,
    help='List all newa state directories (overrides --last).',
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
@click.option(
    '--refresh',
    is_flag=True,
    default=False,
    help='Refresh Testing Farm request statuses before listing (only incomplete requests). '
         'Requires -D/--state-dir, -P/--prev-state-dir, or --event-filter.',
    )
@click.option(
    '--refresh-all',
    is_flag=True,
    default=False,
    help='Refresh all Testing Farm request statuses before listing (overrides --refresh). '
         'Requires -D/--state-dir, -P/--prev-state-dir, or --event-filter.',
    )
@click.pass_obj
def cmd_list(
        ctx: CLIContext,
        last: int,
        list_all: bool,
        events: bool,
        issues: bool,
        refresh: bool,
        refresh_all: bool) -> None:
    """List NEWA execution details from state directories."""
    ctx.enter_command('list')
    # Initialize colors based on environment, terminal capabilities, and config
    init_colors(ctx.settings.newa_color_config)
    # Ensure --events and --issues are not used together
    if events and issues:
        raise click.UsageError('--events and --issues cannot be used together')
    # Validate --last parameter
    if last < 1:
        raise click.UsageError("'--last' must be >= 1")
    # Ensure --refresh and --refresh-all are only used with a specific state-dir or event filter
    if ((refresh or refresh_all) and not ctx.state_dirpath.is_dir() and
            not ctx.event_filter_pattern):
        raise click.UsageError(
            '--refresh and --refresh-all require a specific state directory or --event-filter. '
            'Use -D/--state-dir, -P/--prev-state-dir, or --event-filter '
            'to specify what to refresh.')
    # save current logger level and statedir
    saved_logger_level = ctx.logger.level
    saved_state_dir = ctx.state_dirpath
    # when not in DEBUG, decrese log verbosity so it won't be too noisy
    # when loading individual YAML files
    if ctx.logger.level != logging.DEBUG:
        ctx.logger.setLevel(logging.WARN)
    # when existing state-dir has been provided, use it
    specific_state_dir = saved_state_dir.is_dir()
    if specific_state_dir:
        state_dirs = [ctx.state_dirpath]
    # otherwise choose last N dirs or all dirs
    else:
        try:
            entries = os.scandir(ctx.settings.newa_statedir_topdir)
        except FileNotFoundError as e:
            raise Exception(f'{ctx.settings.newa_statedir_topdir} does not exist') from e
        sorted_entries = sorted(entries, key=lambda entry: os.path.getmtime(Path(entry)))
        if list_all:
            state_dirs = [Path(e.path) for e in sorted_entries]
        else:
            state_dirs = [Path(e.path) for e in sorted_entries[-last:]]

    def _print(indent: int, s: str, end: str = '\n') -> None:
        print(f'{" " * indent}{s}', end=end)

    for state_dir in state_dirs:
        ctx.state_dirpath = state_dir
        event_jobs = list(ctx.load_artifact_jobs(filter_events=True))
        # Skip this state dir if no events match the filter, but only when listing
        # multiple directories. Always show explicitly specified state dir.
        if not event_jobs and not specific_state_dir:
            continue
        # Print state dir header only if there are events to show
        state_dir_color = Colors.STATE_DIR or Colors.ORANGE
        print(f'{colorize_text(str(state_dir), state_dir_color)}:')
        event_color = Colors.EVENT or Colors.RED
        for event_job in event_jobs:
            if event_job.erratum:
                _print(2, colorize_text(
                    f'event {event_job.id} - {event_job.erratum.summary}', event_color))
                _print(2, event_job.erratum.url)
            elif event_job.rog:
                _print(2, colorize_text(
                    f'event {event_job.id} - {event_job.rog.title}', event_color))
            elif event_job.jira_issue:
                _print(2, colorize_text(
                    f'event {event_job.id} - {event_job.jira_issue.summary}', event_color))
                _print(2, event_job.jira_issue.url)
                people_info = []
                if event_job.jira_issue.assignee:
                    people_info.append(f'assignee: {event_job.jira_issue.assignee}')
                if event_job.jira_issue.reporter:
                    people_info.append(f'reporter: {event_job.jira_issue.reporter}')
                if people_info:
                    _print(2, ', '.join(people_info))
            else:
                _print(2, colorize_text(f'event {event_job.id}', event_color))
            # Skip Jira issues and other details if --events flag is set
            if events:
                continue
            jira_file_prefix = f'{JIRA_FILE_PREFIX}{event_job.event.short_id}-{event_job.short_id}'
            jira_jobs = list(ctx.load_jira_jobs(jira_file_prefix, filter_actions=True))
            issue_color = Colors.ISSUE or Colors.BLUE
            for jira_job in jira_jobs:
                jira_summary = f'- {jira_job.jira.summary}' if jira_job.jira.summary else ''
                jira_action_id = jira_job.jira.action_id or 'no action id'
                issue_line = f'issue {jira_job.jira.id} ({jira_action_id}) {jira_summary}'
                _print(4, colorize_text(issue_line, issue_color))
                if jira_job.jira.url:
                    _print(4, jira_job.jira.url)
                if jira_job.jira.action_tags:
                    tags_str = ', '.join(jira_job.jira.action_tags)
                    _print(4, f'tags: {tags_str}')
                if jira_job.recipe and jira_job.recipe.url:
                    # Show indicator only when auto_schedule is false
                    auto_schedule = getattr(jira_job.recipe, 'auto_schedule', True)
                    auto_schedule_indicator = ' [no-auto-schedule]' if not auto_schedule else ''
                    _print(6, f'recipe: {jira_job.recipe.url}{auto_schedule_indicator}')
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
                reportportal_color = Colors.REPORTPORTAL or Colors.PURPLE
                if schedule_jobs and schedule_jobs[0].request.reportportal:
                    launch_name = schedule_jobs[0].request.reportportal.get('launch_name', None)
                    if launch_name:
                        _print(
                            6,
                            colorize_text(
                                f'ReportPortal launch: {launch_name}',
                                reportportal_color))
                        launch_url = schedule_jobs[0].request.reportportal.get('launch_url', None)
                        if launch_url:
                            _print(6, launch_url)
                request_id_color = Colors.REQUEST_ID or Colors.GREEN
                for schedule_job in schedule_jobs:
                    _print(6, colorize_text(schedule_job.request.id, request_id_color), end='')
                    execute_file_prefix = (f'{EXECUTE_FILE_PREFIX}{event_job.event.short_id}-'
                                           f'{event_job.short_id}-{jira_job.jira.id}-'
                                           f'{schedule_job.request.id}')
                    execute_jobs = list(
                        ctx.load_execute_jobs(
                            execute_file_prefix,
                            filter_actions=True))
                    # Refresh TF request statuses if --refresh or --refresh-all flag is set
                    if (refresh or refresh_all) and execute_jobs:
                        _update_all_tf_request_statuses(
                            ctx, execute_jobs, force_refresh=refresh_all)
                        # Reload execute jobs to get refreshed data
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
                                # Apply color formatting to state and result
                                colored_state = colorize_state(state)
                                colored_result = colorize_result(result.value)
                                print(
                                    f' - state: {colored_state}, '
                                    f'result: {colored_result}, '
                                    f'artifacts: {url}')
                    else:
                        # Apply color formatting to 'not executed' state
                        print(f' - {colorize_state("not executed")}')
        print()
    # restore logger level and statedir
    ctx.logger.setLevel(saved_logger_level)
    ctx.state_dirpath = saved_state_dir
