import copy
import datetime
import io
import logging
import multiprocessing
import os
import re
import sys
import tarfile
import time
import urllib
from collections.abc import Generator
from functools import partial
from pathlib import Path
from typing import Any, Optional

import click
import jira

from . import (
    EVENT_FILE_PREFIX,
    EXECUTE_FILE_PREFIX,
    JIRA_FILE_PREFIX,
    SCHEDULE_FILE_PREFIX,
    TF_REQUEST_FINISHED_STATES,
    Arch,
    ArtifactJob,
    CLIContext,
    Compose,
    ErrataTool,
    ErratumCommentTrigger,
    ErratumContentType,
    Event,
    EventType,
    ExecuteHow,
    ExecuteJob,
    Execution,
    Issue,
    IssueAction,
    IssueConfig,
    IssueHandler,
    JiraJob,
    NVRParser,
    OnRespinAction,
    RawRecipeConfigDimension,
    RawRecipeReportPortalConfigDimension,
    Recipe,
    RecipeConfig,
    ReportPortal,
    Request,
    RequestResult,
    RoGTool,
    ScheduleJob,
    Settings,
    TFRequest,
    check_tf_cli_version,
    eval_test,
    get_url_basename,
    render_template,
    short_sleep,
    yaml_parser,
    )

JIRA_NONE_ID = '_NO_ISSUE'
STATEDIR_NAME_PATTERN = r'^run-([0-9]+)$'
RP_LAUNCH_DESCR_CHARS_LIMIT = 1024
NEWA_DEFAULT_CONFIG = '$HOME/.newa'

logging.basicConfig(
    format='%(asctime)s %(message)s',
    datefmt='%m/%d/%Y %I:%M:%S %p',
    level=logging.INFO)


def get_state_dir(topdir: Path, use_ppid: bool = False) -> Path:
    """ When not using ppid returns the first unused directory
        matching /var/tmp/newa/run-[0-9]+, starting with run-1
        When using ppid searches for the most recent state-dir directory
        containing file $PPID.ppid
    """
    counter = 0
    last_dir = None
    ppid_filename = f'{os.getppid()}.ppid'
    try:
        obj = os.scandir(topdir)
    except FileNotFoundError as e:
        if use_ppid:
            raise Exception(f'{topdir} does not exist') from e
        # return initial value run-1
        return topdir / f'run-{counter + 1}'
    dirs = sorted([d for d in obj if d.is_dir()],
                  key=lambda d: os.path.getmtime(d))
    for statedir in dirs:
        # when using ppid find the most recent (using getmtime) matching dir
        if use_ppid:
            ppid_file = Path(statedir.path) / ppid_filename
            if ppid_file.exists():
                last_dir = statedir
        # otherwise find the lowest unsused value for counter
        else:
            r = re.match(STATEDIR_NAME_PATTERN, statedir.name)
            if r:
                c = int(r.group(1))
                counter = max(c, counter)
    if use_ppid:
        if last_dir:
            return Path(last_dir.path)
        raise Exception(f'File {ppid_filename} not found under {topdir}')
    # otherwise return the first unused value
    return topdir / f'run-{counter + 1}'


def initialize_state_dir(ctx: CLIContext) -> None:
    if not ctx.state_dirpath.exists():
        ctx.new_state_dir = True
        ctx.logger.debug(f'State directory {ctx.state_dirpath} does not exist, creating...')
        ctx.state_dirpath.mkdir(parents=True)
    # create empty ppid file
    with open(os.path.join(ctx.state_dirpath, f'{os.getppid()}.ppid'), 'w'):
        pass


def initialize_jira_connection(ctx: CLIContext) -> Any:
    jira_url = ctx.settings.jira_url
    if not jira_url:
        raise Exception('Jira URL is not configured!')
    jira_token = ctx.settings.jira_token
    if not jira_token:
        raise Exception('Jira token is not configured!')
    return jira.JIRA(jira_url, token_auth=jira_token)


def initialize_rp_connection(ctx: CLIContext) -> ReportPortal:
    rp_project = ctx.settings.rp_project
    rp_url = ctx.settings.rp_url
    rp = ReportPortal(url=rp_url,
                      token=ctx.settings.rp_token,
                      project=rp_project)
    rp.check_connection(rp_url, ctx.logger)
    return rp


def initialize_et_connection(ctx: CLIContext) -> ErrataTool:
    et_url = ctx.settings.et_url
    if not et_url:
        raise Exception('Errata Tool URL is not configured!')

    et = ErrataTool(url=ctx.settings.et_url)
    et.check_connection(et_url, ctx.logger)
    return et


def issue_transition(connection: Any, transition: str, issue_id: str) -> None:
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


@click.group(chain=True)
@click.option(
    '--state-dir',
    '-D',
    default='',
    help='Specify state directory.',
    )
@click.option(
    '--prev-state-dir',
    '-P',
    is_flag=True,
    default=False,
    help='Use the latest state-dir used previously within this shell session',
    )
@click.option(
    '--conf-file',
    default='',
    help='Path to newa configuration file.',
    )
@click.option(
    '--clear',
    is_flag=True,
    default=False,
    help='Each subcommand will remove existing YAML files before proceeding',
    )
@click.option(
    '--debug',
    is_flag=True,
    default=False,
    help='Enable debug logging',
    )
@click.option(
    '-e', '--environment', 'envvars',
    default=[],
    multiple=True,
    help='Specify custom environment variable, e.g. "-e FOO=BAR".',
    )
@click.option(
    '-c', '--context', 'contexts',
    default=[],
    multiple=True,
    help='Specify custom tmt context, e.g. "-c foo=bar".',
    )
@click.option(
    '--extract-state-dir',
    '-E',
    default='',
    help='Extract YAML files from the specified archive to state-dir (implies --force).',
    )
@click.option(
    '--force',
    is_flag=True,
    default=False,
    help='Force rewrite of existing YAML files.',
    )
@click.option(
    '--action-id-filter',
    default='',
    help='Regular expression matching issue-config action ids to process (only).',
    )
@click.pass_context
def main(click_context: click.Context,
         state_dir: str,
         prev_state_dir: bool,
         conf_file: str,
         clear: bool,
         debug: bool,
         envvars: list[str],
         contexts: list[str],
         extract_state_dir: str,
         force: bool,
         action_id_filter: str) -> None:

    # when user has specified config file, check its presence
    if conf_file:
        if not Path(conf_file).exists():
            raise FileNotFoundError(f"Configuration file '{conf_file}' does not exist.")
    else:
        conf_file = NEWA_DEFAULT_CONFIG
    # load settings
    settings = Settings.load(Path(os.path.expandvars(conf_file)))
    # try to identify prev_state_dirpath just in case we need it
    # we won't fail on errors
    try:
        prev_state_dirpath = prev_state_dirpath = get_state_dir(
            settings.newa_statedir_topdir, use_ppid=True)
    except Exception:
        prev_state_dirpath = None

    # handle state_dir settings
    if prev_state_dir and state_dir:
        raise Exception('Use either --state-dir or --prev-state-dir')
    if prev_state_dir:
        state_dir = str(get_state_dir(settings.newa_statedir_topdir, use_ppid=True))
    elif not state_dir:
        state_dir = str(get_state_dir(settings.newa_statedir_topdir))

    # handle --clear param
    settings.newa_clear_on_subcommand = clear

    try:
        pattern = re.compile(action_id_filter) if action_id_filter else None
    except re.error as e:
        raise Exception(
            f'Cannot compile --action-id-filter regular expression. {e!r}') from e

    ctx = CLIContext(
        settings=settings,
        logger=logging.getLogger(),
        state_dirpath=Path(os.path.expandvars(state_dir)),
        cli_environment={},
        cli_context={},
        prev_state_dirpath=prev_state_dirpath,
        force=force,
        action_id_filter_pattern=pattern,
        )
    click_context.obj = ctx

    # In case of '--help' we are going to end here
    if '--help' in sys.argv:
        return

    if debug:
        ctx.logger.setLevel(logging.DEBUG)

    ctx.logger.info(f'Using --state-dir={ctx.state_dirpath}')
    ctx.logger.debug(f'prev_state_dirpath={ctx.prev_state_dirpath}')

    if ctx.settings.newa_clear_on_subcommand:
        ctx.logger.debug('NEWA subcommands will remove existing YAML files.')

    # extract YAML files from the given archive to state-dir
    if extract_state_dir:
        ctx.new_state_dir = False
        # enforce --force
        ctx.force = True
        tar_open_kwargs: dict[str, Any] = {
            'mode': 'r:*',
            }
        if re.match('^https?://', extract_state_dir):
            data = urllib.request.urlopen(extract_state_dir).read()
            tar_open_kwargs['fileobj'] = io.BytesIO(data)
        else:
            tar_open_kwargs['name'] = Path(extract_state_dir)
        with tarfile.open(**tar_open_kwargs) as tf:
            for item in tf.getmembers():
                if item.name.endswith('.yaml'):
                    item.name = os.path.basename(item.name)
                    tf.extract(item, path=ctx.state_dirpath, filter='data')
        initialize_state_dir(ctx)

    def _split(s: str) -> tuple[str, str]:
        """ split key='some value' into a tuple (key, value) """
        r = re.match(r"""^\s*([a-zA-Z0-9_][a-zA-Z0-9_\-]*)=["']?(.*?)["']?\s*$""", s)
        if not r:
            raise Exception(
                f'Option value {s} has invalid format, key=value format expected!')
        k, v = r.groups()
        return (k, v)

    # store environment variables and context provided on a cmdline
    ctx.cli_environment.update(dict(_split(s) for s in envvars))
    ctx.cli_context.update(dict(_split(s) for s in contexts))


@main.command(name='list')
@click.option(
    '--last',
    default=10,
    help='Print details of recent newa executions.',
    show_default=True,
    )
@click.pass_obj
def cmd_list(ctx: CLIContext, last: int) -> None:
    ctx.enter_command('list')
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
                                state = getattr(execute_job.execution, "state", "unknown")
                                # if state was None check of request_uuid
                                if (not state) and getattr(
                                        execute_job.execution, "request_uuid", None):
                                    state = 'executed, not reported'
                                result = getattr(
                                    execute_job.execution, "result", RequestResult.NONE)
                                url = getattr(
                                    execute_job.execution, "artifacts_url", "not available")
                                print(f' - state: {state}, result: {result}, artifacts: {url}')
                    else:
                        print(' - not executed')
        print()
    # restore logger level and statedir
    ctx.logger.setLevel(saved_logger_level)
    ctx.state_dirpath = saved_state_dir


def apply_release_mapping(string: str,
                          mapping: Optional[list[str]] = None,
                          regexp: bool = True,
                          logger: Optional[logging.Logger] = None) -> str:
    # define default mapping
    if not mapping:
        mapping = [
            r'\.GA$=',
            r'\.Z\.?(MAIN)?(\+)?(AUS|E.S|TUS)?$=',
            r'^rhel-=RHEL-',
            r'RHEL-10\.0\.BETA=RHEL-10-Beta',
            r'-candidate$=',
            r'-draft$=',
            r'-z$=',
            r'$=-Nightly',
            # ugly hack to narrow weird TF compose naming for RHEL-7
            r'RHEL-7-ELS-Nightly=RHEL-7.9-ZStream',
            ]
    new_string = string
    for m in mapping:
        r = re.fullmatch(r'([^\s=]+)=([^=]*)', m)
        if not r:
            raise Exception(f"Mapping {m} does not having expected format 'patten=value'")
        pattern, value = r.groups()
        # for regexp=True apply each matching regexp
        if regexp and re.search(pattern, new_string):
            new_string = re.sub(pattern, value, new_string)
            if logger:
                logger.debug(
                    f'Found match in {new_string} for mapping {m}, new value {new_string}')
        # for string matching return the first match
        if (not regexp) and new_string == pattern:
            if logger:
                logger.debug(
                    f'Found match in {new_string} for mapping {m}, new value {new_string}')
            return value
    return new_string


def derive_compose(release: str,
                   mapping: Optional[list[str]] = None,
                   logger: Optional[logging.Logger] = None) -> str:
    """ Derive RHEL compose from the provided errata release or brew/koji build target """
    # when compose_mapping is provided, apply it with regexp disabled
    if mapping:
        compose = apply_release_mapping(
            release, mapping, regexp=False, logger=logger)
    # otherwise use the built-in default mapping
    else:
        compose = apply_release_mapping(release, logger=logger)
    return compose


def test_file_presence(statedir: Path, prefix: str) -> bool:
    return any(child.name.startswith(prefix) for child in statedir.iterdir())


def copy_events_from_previous_statedir(ctx: CLIContext) -> None:
    """Copy event files from the previous state directory to the current one."""
    if not ctx.new_state_dir:
        raise Exception("Do not use 'newa -P' or 'newa -D' together with 'event --prev-event'")
    if not ctx.prev_state_dirpath:
        raise Exception('Could not identify the previous state-dir')

    ctx_prev = copy.deepcopy(ctx)
    ctx_prev.state_dirpath = ctx.prev_state_dirpath

    artifact_jobs = list(ctx_prev.load_artifact_jobs())
    if not artifact_jobs:
        raise Exception(f'No {EVENT_FILE_PREFIX} YAML files found in {ctx_prev.state_dirpath}')

    for artifact_job in artifact_jobs:
        ctx.save_artifact_job(artifact_job)


def load_event_ids_from_init_files(
        ctx: CLIContext) -> tuple[list[str], list[str], list[str], list[str]]:
    """
    Load event IDs from init files and return as tuple
    (errata_ids, compose_ids, rog_urls, jira_keys).
    """
    errata_ids: list[str] = []
    compose_ids: list[str] = []
    rog_urls: list[str] = []
    jira_keys: list[str] = []

    events = [e.event for e in ctx.load_initial_errata()]
    for event in events:
        if event.type_ is EventType.ERRATUM:
            errata_ids.append(event.id)
        if event.type_ is EventType.COMPOSE:
            compose_ids.append(event.id)
        if event.type_ is EventType.ROG:
            rog_urls.append(event.id)
        if event.type_ is EventType.JIRA:
            jira_keys.append(event.id)

    return errata_ids, compose_ids, rog_urls, jira_keys


def process_event_errata(
        ctx: CLIContext,
        errata_ids: list[str],
        compose_mapping: list[str]) -> None:
    """Process erratum IDs and create corresponding artifact jobs."""
    if not errata_ids:
        return

    et = initialize_et_connection(ctx)

    for erratum_id in errata_ids:
        event = Event(type_=EventType.ERRATUM, id=erratum_id)
        errata = et.get_errata(event)

        for erratum in errata:
            release = erratum.release.strip()
            compose = derive_compose(release, compose_mapping, ctx.logger)

            # skip compose if it has been transformed to an empty compose
            if not compose:
                ctx.logger.info(
                    f"""Erratum release {release} transformed to an empty string, skipping""")
                continue

            ctx.logger.info(
                f"""Erratum release {release} transformed to a compose {compose}""")

            if erratum.content_type in (ErratumContentType.RPM, ErratumContentType.MODULE):
                artifact_job = ArtifactJob(
                    event=event,
                    erratum=erratum,
                    compose=Compose(id=compose),
                    rog=None)
                ctx.save_artifact_job(artifact_job)

            # for docker content type we create ArtifactJob per build
            if erratum.content_type == ErratumContentType.DOCKER:
                erratum_clone = erratum.clone()
                for build in erratum.builds:
                    erratum_clone.builds = [build]
                    erratum_clone.components = [NVRParser(build).name]
                    artifact_job = ArtifactJob(
                        event=event,
                        erratum=erratum_clone,
                        compose=Compose(id=compose),
                        rog=None)
                    ctx.save_artifact_job(artifact_job)


def process_event_composes(ctx: CLIContext, compose_ids: list[str]) -> None:
    """Process compose IDs and create corresponding artifact jobs."""
    for compose_id in compose_ids:
        event = Event(type_=EventType.COMPOSE, id=compose_id)
        artifact_job = ArtifactJob(
            event=event,
            erratum=None,
            compose=Compose(id=compose_id),
            rog=None)
        ctx.save_artifact_job(artifact_job)


def process_event_rog_urls(
        ctx: CLIContext,
        rog_urls: list[str],
        compose_mapping: list[str]) -> None:
    """Process RoG merge request URLs and create corresponding artifact jobs."""
    if not rog_urls:
        return

    if not ctx.settings.rog_token:
        raise Exception('RoG token is not configured!')

    rog_tool = RoGTool(token=ctx.settings.rog_token)
    for url in rog_urls:
        mr = rog_tool.get_mr(url)
        compose_id = derive_compose(mr.build_target, compose_mapping, ctx.logger)
        event = Event(type_=EventType.ROG, id=url)
        artifact_job = ArtifactJob(
            event=event,
            erratum=None,
            compose=Compose(id=compose_id),
            rog=mr)
        ctx.save_artifact_job(artifact_job)


def process_event_jira_keys(ctx: CLIContext, jira_keys: list[str]) -> None:
    """Process Jira issue keys and create corresponding artifact jobs."""
    for jira_key in jira_keys:
        event = Event(type_=EventType.JIRA, id=jira_key)
        artifact_job = ArtifactJob(
            event=event,
            erratum=None,
            compose=None,
            rog=None)
        ctx.save_artifact_job(artifact_job)


@main.command(name='event')
@click.option(
    '-e', '--erratum', 'errata_ids',
    default=[],
    multiple=True,
    help='Specifies erratum-type event for a given advisory ID.',
    )
@click.option(
    '-c', '--compose', 'compose_ids',
    default=[],
    multiple=True,
    help='Specifies compose-type event for a given compose.',
    )
@click.option(
    '--jira-issue', 'jira_keys',
    default=[],
    multiple=True,
    help='Specifies Jira event for a given issue key.',
    )
@click.option(
    '--rog-mr', 'rog_urls',
    default=[],
    multiple=True,
    help='Specifies RoG merge-request URL.',
    )
@click.option(
    '--compose-mapping', 'compose_mapping',
    default=[],
    multiple=True,
    help=('Custom Erratum release to Testing Farm compose mapping in the form '
          '"RELEASE=COMPOSE". For example, '
          '"--compose-mapping RHEL-9.4.0.Z.MAIN+EUS=RHEL-9.4.0-Nightly". '
          'Can be specified multiple times, the 1st match is used'
          ),
    )
@click.option(
    '--prev-event',
    is_flag=True,
    default=False,
    help='Copy events from the previous NEWA state-dir',
    )
@click.pass_obj
def cmd_event(
        ctx: CLIContext,
        errata_ids: list[str],
        compose_ids: list[str],
        jira_keys: list[str],
        rog_urls: list[str],
        compose_mapping: list[str],
        prev_event: bool) -> None:
    ctx.enter_command('event')

    # ensure state dir is present and initialized
    initialize_state_dir(ctx)

    if ctx.settings.newa_clear_on_subcommand:
        ctx.remove_job_files(EVENT_FILE_PREFIX)

    if test_file_presence(ctx.state_dirpath, EVENT_FILE_PREFIX) and not ctx.force:
        ctx.logger.error(
            f'"{EVENT_FILE_PREFIX}" files already exist in state-dir {ctx.state_dirpath}, '
            'use --force to override')
        sys.exit(1)

    # copy events from the previous statedir
    if prev_event:
        copy_events_from_previous_statedir(ctx)

    # Load event IDs from init files if not provided via command line
    if not errata_ids and not compose_ids and not rog_urls and not jira_keys:
        errata_ids, compose_ids, rog_urls, jira_keys = load_event_ids_from_init_files(ctx)

    # Validate that at least one event source is provided
    if not errata_ids and not compose_ids and not rog_urls and not jira_keys and not prev_event:
        raise Exception('Missing event IDs!')

    # Process different event types
    process_event_errata(ctx, errata_ids, compose_mapping)
    process_event_composes(ctx, compose_ids)
    process_event_rog_urls(ctx, rog_urls, compose_mapping)
    process_event_jira_keys(ctx, jira_keys)


def _parse_issue_mapping(map_issue: list[str], config: IssueConfig) -> dict[str, str]:
    """Parse and validate issue mapping from command line arguments."""
    issue_mapping: dict[str, str] = {}

    # Parse --map-issue keys and values into a dictionary
    for m in map_issue:
        r = re.fullmatch(r'([^\s=]+)=([^=]*)', m)
        if not r:
            raise Exception(f"Mapping {m} does not having expected format 'key=value'")
        key, value = r.groups()
        issue_mapping[key] = value

    # Gather ids from the config file
    ids = [getattr(action, "id", None) for action in config.issues[:]]

    # Check for keys not present in a config file
    for key in issue_mapping:
        if key not in ids:
            raise Exception(f"Key '{key}' from mapping '{m}' doesn't match issue item id "
                            f"from the config file. Typo?")

    return issue_mapping


def _create_jira_fake_id_generator() -> Generator[str, int, None]:
    """Generate fake Jira IDs for jobs without actual Jira issues."""
    n = 1
    while True:
        yield f'{JIRA_NONE_ID}_{n}'
        n += 1


def _get_jira_event_fields(
        ctx: CLIContext,
        artifact_job: ArtifactJob,
        jira_handler: IssueHandler) -> Any:
    """Get Jira event fields for Jinja template usage."""
    if artifact_job.event.type_ is EventType.JIRA:
        jira_event_fields = jira_handler.get_details(Issue(artifact_job.event.id)).fields
        jira_event_fields.id = artifact_job.event.id
        short_sleep()
    else:
        jira_event_fields = {}
    return jira_event_fields


def _render_action_value(
        value: str,
        artifact_job: ArtifactJob,
        action: IssueAction,
        jira_event_fields: dict[str, Any]) -> str:
    """Render a single value as Jinja template."""
    return render_template(
        value,
        EVENT=artifact_job.event,
        ERRATUM=artifact_job.erratum,
        COMPOSE=artifact_job.compose,
        JIRA=jira_event_fields,
        ROG=artifact_job.rog,
        CONTEXT=action.context,
        ENVIRONMENT=action.environment)


def _render_action_fields(
        action: IssueAction,
        artifact_job: ArtifactJob,
        jira_event_fields: dict[str, Any],
        assignee: Optional[str],
        unassigned: bool) -> tuple[str, str, Optional[str], dict[str, Any], dict[str, list[str]]]:
    """Render all action fields using Jinja templates."""
    rendered_summary = _render_action_value(
        action.summary or '', artifact_job, action, jira_event_fields)
    rendered_description = _render_action_value(
        action.description or '', artifact_job, action, jira_event_fields)

    # Determine assignee
    if assignee:
        rendered_assignee = assignee
    elif action.assignee:
        rendered_assignee = _render_action_value(
            action.assignee or '', artifact_job, action, jira_event_fields)
    else:  # covers unassigned as well
        rendered_assignee = None

    # Render newa_id if present
    # NOTE: This mutation is intentional and necessary. IssueHandler.newa_id() uses
    # action.newa_id directly without rendering (see __init__.py:1831-1832), so the
    # rendered value must be stored back in the action object. While actions can be
    # re-queued (line 1200), each action is only processed once, and the mutation only
    # happens during that single processing pass. Actions come from a shallow copy of
    # config.issues[:] (line 1154).
    if action.newa_id:
        action.newa_id = _render_action_value(
            action.newa_id, artifact_job, action, jira_event_fields)

    # Render custom fields
    rendered_fields: dict[str, Any] = copy.deepcopy(action.fields) if action.fields else {}
    if rendered_fields:
        for key, value in rendered_fields.items():
            if isinstance(value, str):
                rendered_fields[key] = _render_action_value(
                    value, artifact_job, action, jira_event_fields)
            elif isinstance(value, list):
                rendered_fields[key] = [_render_action_value(
                    v, artifact_job, action, jira_event_fields) for v in value]

    # Render links
    rendered_links: dict[str, list[str]] = {}
    if action.links:
        for relation in action.links:
            rendered_links[relation] = []
            for linked_key in action.links[relation]:
                if isinstance(linked_key, str):
                    rendered_links[relation].append(_render_action_value(
                        linked_key, artifact_job, action, jira_event_fields))
                else:
                    raise Exception(
                        f"Linked issue key '{linked_key}' must be a string")

    return (rendered_summary, rendered_description, rendered_assignee,
            rendered_fields, rendered_links)


def _find_or_create_issue(
        ctx: CLIContext,
        action: IssueAction,
        jira_handler: IssueHandler,
        config: IssueConfig,
        issue_mapping: dict[str, str],
        no_newa_id: bool,
        recreate: bool,
        rendered_summary: str,
        rendered_description: str,
        rendered_assignee: Optional[str],
        rendered_fields: dict[str, Any],
        rendered_links: dict[str, list[str]],
        processed_actions: dict[str, Issue],
        created_action_ids: list[str]) -> tuple[Optional[Issue], list[Issue], bool]:
    """
    Find existing issue or create a new one.
    Returns (issue, old_issues, trigger_erratum_comment).
    Returns (None, [], False) if action should be skipped.
    """
    new_issues: list[Issue] = []
    old_issues: list[Issue] = []

    # Get transition settings
    transition_passed = None
    transition_processed = None
    if action.auto_transition:
        if jira_handler.transitions.passed:
            transition_passed = jira_handler.transitions.passed[0]
        if jira_handler.transitions.processed:
            transition_processed = jira_handler.transitions.processed[0]

    # First check if we have a match in issue_mapping
    if action.id and action.id in issue_mapping and issue_mapping[action.id].strip():
        mapped_issue = Issue(
            issue_mapping[action.id].strip(),
            group=config.group,
            transition_passed=transition_passed,
            transition_processed=transition_processed)
        jira_issue = jira_handler.get_details(mapped_issue)
        mapped_issue.closed = jira_issue.get_field(
            "status").name in jira_handler.transitions.closed
        new_issues.append(mapped_issue)

    # Otherwise search for the issue in Jira
    elif not no_newa_id:
        short_sleep()
        if recreate:
            search_result = jira_handler.get_related_issues(
                action, all_respins=True, closed=False)
        else:
            search_result = jira_handler.get_related_issues(
                action, all_respins=True, closed=True)

        for jira_issue_key, jira_issue in search_result.items():
            ctx.logger.info(f"Checking {jira_issue_key}")

            is_new = False
            if jira_handler.newa_id(action) in jira_issue["description"] \
                and (not action.parent_id
                     or action.parent_id not in created_action_ids):
                is_new = True

            if is_new:
                new_issues.append(
                    Issue(
                        jira_issue_key,
                        group=config.group,
                        closed=jira_issue["status"] == "closed",
                        transition_passed=transition_passed,
                        transition_processed=transition_processed))
            elif jira_issue["status"] == "opened":
                old_issues.append(
                    Issue(
                        jira_issue_key,
                        group=config.group,
                        closed=False,
                        transition_passed=transition_passed,
                        transition_processed=transition_processed))

    # Old opened issue(s) can be re-used for the current respin
    if old_issues and action.on_respin == OnRespinAction.KEEP:
        new_issues.extend(old_issues)
        old_issues = []

    # Unless we want recreate closed issues we would stop processing
    if new_issues and (not recreate):
        opened_issues = [i for i in new_issues if not i.closed]
        closed_issues = [i for i in new_issues if i.closed]
        if not opened_issues:
            closed_ids = ', '.join([i.id for i in closed_issues])
            ctx.logger.info(
                f"Relevant issues {closed_ids} found but already closed")
            return None, [], False  # Signal to skip this action
        new_issues = opened_issues

    # Create new issue or reuse existing
    trigger_erratum_comment = False
    if not new_issues:
        parent = None
        if action.parent_id:
            parent = processed_actions.get(action.parent_id)

        short_sleep()
        new_issue = jira_handler.create_issue(
            action=action,
            summary=rendered_summary,
            description=rendered_description,
            use_newa_id=not no_newa_id,
            assignee_email=rendered_assignee,
            parent=parent,
            group=config.group,
            transition_passed=transition_passed,
            transition_processed=transition_processed,
            fields=rendered_fields,
            links=rendered_links)

        # action.id is guaranteed to be non-None due to validation in _process_issue_config
        assert action.id is not None
        processed_actions[action.id] = new_issue
        created_action_ids.append(action.id)
        ctx.logger.info(f"New issue {new_issue.id} created")
        trigger_erratum_comment = True

    elif len(new_issues) == 1:
        new_issue = new_issues[0]
        assert action.id is not None
        processed_actions[action.id] = new_issue
        short_sleep()
        trigger_erratum_comment = jira_handler.refresh_issue(action, new_issue)
        ctx.logger.info(f"Issue {new_issue} re-used")

    else:
        raise Exception(f"More than one new {action.id} found ({new_issues})!")

    return new_issue, old_issues, trigger_erratum_comment


def _handle_erratum_comment_for_jira(
        ctx: CLIContext,
        et: ErrataTool,
        artifact_job: ArtifactJob,
        action: IssueAction,
        new_issue: Issue,
        rendered_summary: str,
        trigger_erratum_comment: bool) -> None:
    """Add comment to Errata Tool if required."""
    if (ctx.settings.et_enable_comments and
            trigger_erratum_comment and
            action.erratum_comment_triggers and
            ErratumCommentTrigger.JIRA in action.erratum_comment_triggers and
            artifact_job.erratum):
        issue_url = urllib.parse.urljoin(
            ctx.settings.jira_url, f"/browse/{new_issue.id}")
        et.add_comment(
            artifact_job.erratum.id,
            'New Errata Workflow Automation (NEWA) prepared '
            'a Jira tracker for this advisory.\n'
            f'{new_issue.id} - {rendered_summary}\n'
            f'{issue_url}')
        ctx.logger.info(
            f"Erratum {artifact_job.erratum.id} was updated "
            f"with a comment about {new_issue.id}")


def _create_jira_job_from_action(
        ctx: CLIContext,
        action: IssueAction,
        artifact_job: ArtifactJob,
        jira_event_fields: dict[str, Any],
        new_issue: Issue) -> None:
    """Create and save JiraJob if action has job_recipe."""
    if action.job_recipe:
        recipe_url = render_template(
            action.job_recipe,
            EVENT=artifact_job.event,
            ERRATUM=artifact_job.erratum,
            COMPOSE=artifact_job.compose,
            JIRA=jira_event_fields,
            ROG=artifact_job.rog,
            CONTEXT=action.context,
            ENVIRONMENT=action.environment)
        if action.erratum_comment_triggers:
            new_issue.erratum_comment_triggers = action.erratum_comment_triggers
        new_issue.action_id = action.id
        jira_job = JiraJob(
            event=artifact_job.event,
            erratum=artifact_job.erratum,
            compose=artifact_job.compose,
            rog=artifact_job.rog,
            jira=new_issue,
            recipe=Recipe(
                url=recipe_url,
                context=action.context,
                environment=action.environment))
        ctx.save_jira_job(jira_job)


def _close_old_issues(
        ctx: CLIContext,
        old_issues: list[Issue],
        action: IssueAction,
        jira_handler: IssueHandler,
        processed_actions: dict[str, Issue]) -> None:
    """Close old issues that have been replaced."""
    if old_issues:
        if action.on_respin != OnRespinAction.CLOSE:
            raise Exception(
                f"Invalid respin action {action.on_respin} for {old_issues}!")
        # action.id is guaranteed to be non-None due to validation in _process_issue_config
        assert action.id is not None
        for old_issue in old_issues:
            short_sleep()
            jira_handler.drop_obsoleted_issue(
                old_issue, obsoleted_by=processed_actions[action.id])
            ctx.logger.info(f"Old issue {old_issue} closed")


def _expand_action_iterations(
        ctx: CLIContext,
        action: IssueAction,
        issue_actions: list[IssueAction]) -> bool:
    """Expand action iterations if defined. Returns True if iterations were created."""
    if not action.iterate:
        return False

    # For each value prepare a separate action
    for i, iter_vars in enumerate(action.iterate):
        ctx.logger.debug(f"Processing iteration: {iter_vars}")
        new_action = copy.deepcopy(action)
        new_action.iterate = None
        if not new_action.environment:
            new_action.environment = copy.deepcopy(iter_vars)
        else:
            new_action.environment = copy.deepcopy(
                {**new_action.environment, **iter_vars})
        new_action.id = f"{new_action.id}.iter{i + 1}"
        ctx.logger.debug(f"Created issue config action: {new_action}")
        issue_actions.insert(i, new_action)
    ctx.logger.info(f"Created {i} iterations of action {action.id}")
    return True


def _update_action_context_and_environment(
        ctx: CLIContext,
        action: IssueAction) -> None:
    """Update action context and environment with CLI values."""
    if action.context:
        action.context = copy.deepcopy(
            {**action.context, **ctx.cli_context})
    else:
        action.context = copy.deepcopy(ctx.cli_context)
    if action.environment:
        action.environment = copy.deepcopy(
            {**action.environment, **ctx.cli_environment})
    else:
        action.environment = copy.deepcopy(ctx.cli_environment)


def _should_skip_action(
        ctx: CLIContext,
        action: IssueAction,
        artifact_job: ArtifactJob,
        jira_event_fields: dict[str, Any]) -> bool:
    """Check if action should be skipped based on 'when' condition."""
    if action.when and not eval_test(action.when,
                                     JOB=artifact_job,
                                     EVENT=artifact_job.event,
                                     ERRATUM=artifact_job.erratum,
                                     COMPOSE=artifact_job.compose,
                                     JIRA=jira_event_fields,
                                     ROG=artifact_job.rog,
                                     CONTEXT=action.context,
                                     ENVIRONMENT=action.environment):
        ctx.logger.info(f"Skipped, issue action is irrelevant ({action.when})")
        return True
    return False


def _process_issue_action(
        ctx: CLIContext,
        action: IssueAction,
        artifact_job: ArtifactJob,
        jira_handler: IssueHandler,
        config: IssueConfig,
        jira_event_fields: dict[str, Any],
        issue_mapping: dict[str, str],
        no_newa_id: bool,
        recreate: bool,
        assignee: Optional[str],
        unassigned: bool,
        processed_actions: dict[str, Issue],
        created_action_ids: list[str],
        et: Optional[ErrataTool]) -> tuple[Optional[Issue], list[Issue]]:
    """
    Process a single issue action.
    Returns (new_issue, old_issues) or (None, []) if action should be skipped.
    """
    ctx.logger.info(f"Processing {action.id}")

    # Validate action
    if not action.summary:
        raise Exception(f"Action {action} does not have a 'summary' defined.")
    if not action.description:
        raise Exception(f"Action {action} does not have a 'description' defined.")

    # Render all fields
    (rendered_summary, rendered_description, rendered_assignee,
     rendered_fields, rendered_links) = _render_action_fields(
        action, artifact_job, jira_event_fields, assignee, unassigned)

    # Find or create issue
    new_issue, old_issues, trigger_erratum_comment = _find_or_create_issue(
        ctx, action, jira_handler, config, issue_mapping,
        no_newa_id, recreate, rendered_summary, rendered_description,
        rendered_assignee, rendered_fields, rendered_links,
        processed_actions, created_action_ids)

    if new_issue is None:
        # Signal to skip this action (closed issues found)
        return None, []

    # Handle erratum comment
    if et:
        _handle_erratum_comment_for_jira(
            ctx, et, artifact_job, action, new_issue,
            rendered_summary, trigger_erratum_comment)

    # Create jira job if needed
    _create_jira_job_from_action(
        ctx, action, artifact_job, jira_event_fields, new_issue)

    # Return issue and old_issues for further processing
    return new_issue, old_issues


def _process_issue_config(
        ctx: CLIContext,
        artifact_job: ArtifactJob,
        config: IssueConfig,
        issue_mapping: dict[str, str],
        no_newa_id: bool,
        recreate: bool,
        assignee: Optional[str],
        unassigned: bool,
        jira_handler: IssueHandler,
        et: Optional[ErrataTool]) -> None:
    """Process issue configuration and create/update Jira issues."""
    # All issue actions from the configuration
    issue_actions = config.issues[:]

    # Processed actions (action.id : issue)
    processed_actions: dict[str, Issue] = {}

    # action_ids for which new Issues have been created
    created_action_ids: list[str] = []

    # Length of the queue the last time issue action was processed
    endless_loop_check: dict[str, int] = {}

    # Get Jira event fields for Jinja template usage
    jira_event_fields = _get_jira_event_fields(ctx, artifact_job, jira_handler)

    # Iterate over issue actions
    while issue_actions:
        action = issue_actions.pop(0)

        if not action.id:
            raise Exception(f"Action {action} does not have 'id' assigned")

        # Handle iterations
        if _expand_action_iterations(ctx, action, issue_actions):
            continue

        # Check if action.id matches filtered items
        if ctx.skip_action(action.id):
            continue

        # Update context and environment
        _update_action_context_and_environment(ctx, action)

        # Check 'when' condition
        if _should_skip_action(ctx, action, artifact_job, jira_event_fields):
            continue

        # Check parent availability
        if action.parent_id and action.parent_id not in processed_actions:
            queue_length = len(issue_actions)
            last_queue_length = endless_loop_check.get(action.id, 0)
            if last_queue_length == queue_length:
                raise Exception(f"Parent {action.parent_id} for {action.id} not found!"
                                "It does not exists or is closed.")

            endless_loop_check[action.id] = queue_length
            ctx.logger.info(f"Skipped for now (parent {action.parent_id} not yet found)")
            issue_actions.append(action)
            continue

        # Process the action
        new_issue, old_issues = _process_issue_action(
            ctx, action, artifact_job, jira_handler, config,
            jira_event_fields, issue_mapping, no_newa_id, recreate,
            assignee, unassigned, processed_actions, created_action_ids, et)

        # Skip if issue was closed
        if new_issue is None:
            continue

        # Close old issues if needed
        _close_old_issues(ctx, old_issues, action, jira_handler, processed_actions)


def _get_prev_issue_id(ctx: CLIContext) -> str:
    """Get issue ID from previous state directory."""
    if not ctx.new_state_dir:
        raise Exception(
            "Do not use 'newa -P' or 'newa -D' together with 'jira --prev-issue'")
    if not ctx.prev_state_dirpath:
        raise Exception('Could not identify the previous state-dir')

    ctx_prev = copy.deepcopy(ctx)
    ctx_prev.state_dirpath = ctx.prev_state_dirpath

    jira_jobs = ctx_prev.load_jira_jobs()
    jira_keys = [
        job.jira.id for job in jira_jobs if not job.jira.id.startswith(JIRA_NONE_ID)]

    if len(jira_keys) == 1:
        return jira_keys[0]
    raise Exception(
        f'Expecting a single Jira issue key in {ctx_prev.state_dirpath}, '
        f'found {len(jira_keys)}')


def _create_simple_jira_job(
        ctx: CLIContext,
        artifact_job: ArtifactJob,
        issue: Optional[str],
        prev_issue: bool,
        job_recipe: str,
        jira_none_id: Generator[str, int, None]) -> None:
    """Create a simple JiraJob without using issue-config."""
    if not job_recipe:
        raise Exception("Option --job-recipe is mandatory when --issue-config is not set")

    # Handle prev-issue option
    if prev_issue:
        issue = _get_prev_issue_id(ctx)

    # Handle issue option
    if issue:
        jira_connection = initialize_jira_connection(ctx)
        jira_issue = jira_connection.issue(issue)
        ctx.logger.info(f"Using issue {issue}")
        new_issue = Issue(issue,
                          summary=jira_issue.fields.summary,
                          url=urllib.parse.urljoin(
                              ctx.settings.jira_url, f'/browse/{jira_issue.key}'))
    else:
        # Use an empty string as ID so we skip Jira reporting later
        new_issue = Issue(next(jira_none_id))

    jira_job = JiraJob(event=artifact_job.event,
                       erratum=artifact_job.erratum,
                       compose=artifact_job.compose,
                       rog=artifact_job.rog,
                       jira=new_issue,
                       recipe=Recipe(
                           url=job_recipe,
                           context=ctx.cli_context,
                           environment=ctx.cli_environment))
    ctx.save_jira_job(jira_job)


@main.command(name='jira')
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


def _determine_architectures(
        ctx: CLIContext,
        arch_options: list[str],
        jira_job: JiraJob,
        compose: Optional[str]) -> list[Arch]:
    """Determine which architectures to use for scheduling."""
    if arch_options:
        return Arch.architectures([Arch(a.strip()) for a in arch_options])

    if jira_job.erratum and jira_job.erratum.archs:
        return jira_job.erratum.archs

    return Arch.architectures(compose=compose)


def _prepare_initial_config(
        ctx: CLIContext,
        jira_job: JiraJob,
        compose: Optional[str]) -> RawRecipeConfigDimension:
    """Prepare initial configuration from jira_job."""
    initial_config = RawRecipeConfigDimension(
        compose=compose,
        environment=jira_job.recipe.environment or {},
        context=jira_job.recipe.context or {})

    # Add erratum context if testing erratum
    if jira_job.erratum:
        initial_config['context'].update({'erratum': str(jira_job.erratum.id)})

    ctx.logger.debug(f'Initial config: {initial_config})')
    return initial_config


def _process_fixtures(
        ctx: CLIContext,
        fixtures: list[str],
        cli_config: RawRecipeConfigDimension) -> None:
    """Process command-line fixtures and update cli_config in place."""
    if not fixtures:
        return

    for fixture in fixtures:
        r = re.fullmatch(r'([^\s=]+)=([^=]*)', fixture)
        if not r:
            raise Exception(
                f"Fixture {fixture} does not having expected format 'name=value'")

        fixture_name, fixture_value = r.groups()
        fixture_config = cli_config

        # Descend through keys to the lowest level
        while '.' in fixture_name:
            prefix, suffix = fixture_name.split('.', 1)
            fixture_config = fixture_config.setdefault(prefix, {})  # type: ignore [misc]
            fixture_name = suffix

        # Parse the input as yaml to enable lists and dicts
        value = yaml_parser().load(fixture_value)
        fixture_config[fixture_name] = value  # type: ignore[literal-required]

    ctx.logger.debug(f'CLI config modified through --fixture: {cli_config})')


def _configure_recipe(
        ctx: CLIContext,
        config: RecipeConfig,
        architectures: list[Arch],
        recipe_url: str) -> None:
    """Configure recipe with architecture and reportportal defaults."""
    # Extend dimensions with system architecture but do not override existing settings
    if 'arch' not in config.dimensions:
        config.dimensions['arch'] = []
        for architecture in architectures:
            config.dimensions['arch'].append({'arch': architecture})

    # If RP launch name is not specified in the recipe, set it based on the recipe filename
    if not config.fixtures.get('reportportal', None):
        config.fixtures['reportportal'] = RawRecipeReportPortalConfigDimension()

    # Populate default for config.fixtures['reportportal']['launch_name']
    if ((config.fixtures['reportportal'] is not None) and
            (not config.fixtures['reportportal'].get('launch_name', None))):
        config.fixtures['reportportal']['launch_name'] = os.path.splitext(
            get_url_basename(recipe_url))[0]


def _render_request_attributes(
        request: Request,
        jinja_vars: dict[str, Any]) -> None:
    """Render request attributes as Jinja templates in place."""
    for attr in ("reportportal", "tmt", "testingfarm", "environment", "context", "compose"):
        # compose value is a string, not dict
        if attr == 'compose':
            value = getattr(request, attr, '')
            new_value = render_template(str(value), **jinja_vars)
            if new_value:
                setattr(request, attr, new_value)
        else:
            # getattr(request, attr) could also be None due to 'attr' being None
            mapping = getattr(request, attr, {}) or {}
            for (key, value) in mapping.items():
                # launch_attributes is a dict
                if key == 'launch_attributes':
                    for (k, v) in value.items():
                        mapping[key][k] = render_template(str(v), **jinja_vars)
                else:
                    mapping[key] = render_template(str(value), **jinja_vars)


def _prepare_jinja_vars_for_request(
        jira_job: JiraJob,
        request: Request,
        issue_fields: Any) -> dict[str, Any]:
    """Prepare Jinja template variables for request rendering."""
    jinja_vars: dict[str, Any] = {
        'EVENT': jira_job.event,
        'ERRATUM': jira_job.erratum,
        'COMPOSE': jira_job.compose,
        'ROG': jira_job.rog,
        'CONTEXT': request.context,
        'ENVIRONMENT': request.environment,
        'ISSUE': issue_fields}

    if request.arch:
        jinja_vars['ARCH'] = request.arch.value

    return jinja_vars


def _get_issue_fields_for_jira(
        ctx: CLIContext,
        jira_job: JiraJob) -> Any:
    """Get Jira issue fields if available, otherwise return empty dict."""
    if jira_job.jira.id and (not jira_job.jira.id.startswith(JIRA_NONE_ID)):
        jira_connection = initialize_jira_connection(ctx)
        issue_fields = jira_connection.issue(jira_job.jira.id).fields
        issue_fields.id = jira_job.jira.id
        short_sleep()
        return issue_fields
    return {}


def _process_jira_job(
        ctx: CLIContext,
        jira_job: JiraJob,
        arch_options: list[str],
        fixtures: list[str],
        no_reportportal: bool) -> None:
    """Process a single jira_job and create schedule jobs."""
    # Determine compose and architectures
    compose = jira_job.compose.id if jira_job.compose else None
    architectures = _determine_architectures(ctx, arch_options, jira_job, compose)

    # Prepare initial and CLI configs
    initial_config = _prepare_initial_config(ctx, jira_job, compose)
    cli_config = RawRecipeConfigDimension(
        environment=ctx.cli_environment,
        context=ctx.cli_context)
    ctx.logger.debug(f'CLI config: {cli_config})')

    # Process fixtures
    _process_fixtures(ctx, fixtures, cli_config)

    # Load and configure recipe
    config = RecipeConfig.from_yaml_with_includes(jira_job.recipe.url)
    _configure_recipe(ctx, config, architectures, jira_job.recipe.url)

    # Build requests
    jinja_vars: dict[str, Any] = {
        'EVENT': jira_job.event,
        'ERRATUM': jira_job.erratum,
        }
    requests = list(config.build_requests(initial_config, cli_config, jinja_vars))
    ctx.logger.info(f'{len(requests)} requests have been generated')

    # Get Jira issue fields
    issue_fields = _get_issue_fields_for_jira(ctx, jira_job)

    # Create ScheduleJob for each request
    for request in requests:
        # Clear reportportal attribute when --no-reportportal
        if no_reportportal:
            request.reportportal = None

        # Prepare Jinja variables and render request attributes
        jinja_vars = _prepare_jinja_vars_for_request(jira_job, request, issue_fields)
        _render_request_attributes(request, jinja_vars)

        # Create and save schedule job
        schedule_job = ScheduleJob(
            event=jira_job.event,
            erratum=jira_job.erratum,
            compose=jira_job.compose,
            rog=jira_job.rog,
            jira=jira_job.jira,
            recipe=jira_job.recipe,
            request=request)
        ctx.save_schedule_job(schedule_job)


@main.command(name='schedule')
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


@main.command(name='cancel')
@click.pass_obj
def cmd_cancel(ctx: CLIContext) -> None:
    ctx.enter_command('cancel')

    # error out if state dir was not provided
    if not ctx.state_dirpath.exists():
        ctx.logger.error('ERROR: Cannot find NEWA state-dir! Use --state-dir or similar option.')
        sys.exit(1)

    # ensure existing state dir is initialized (store ppid)
    initialize_state_dir(ctx)

    # make TESTING_FARM_API_TOKEN available to workers as envvar if it has been
    # defined only though the settings file
    tf_token = ctx.settings.tf_token
    if not tf_token:
        raise ValueError("TESTING_FARM_API_TOKEN not set!")
    os.environ["TESTING_FARM_API_TOKEN"] = tf_token

    for execute_job in ctx.load_execute_jobs(filter_actions=True):
        if execute_job.request.how == ExecuteHow.TESTING_FARM:
            tf_request = TFRequest(
                api=execute_job.execution.request_api,
                uuid=execute_job.execution.request_uuid)
            tf_request.cancel(ctx)
            tf_request.fetch_details()
            if tf_request.details:
                execute_job.execution.state = tf_request.details['state']
                if 'cancel' in execute_job.execution.state:
                    execute_job.execution.state = 'canceled'
                    execute_job.execution.result = RequestResult.ERROR
                if tf_request.details['result']:
                    execute_job.execution.result = RequestResult(
                        tf_request.details['result']['overall'])
                ctx.save_execute_job(execute_job)


def sanitize_restart_result(ctx: CLIContext, results: list[str]) -> list[RequestResult]:
    sanitized = []
    for result in results:
        try:
            sanitized.append(RequestResult(result))
        except ValueError:
            ctx.logger.error(
                'Invalid `--restart-result` value. Possible values are: '
                f'{", ".join(RequestResult.values())}')
            sys.exit(1)

    # read current test results
    execute_files_list = [
        (ctx.state_dirpath / child.name)
        for child in ctx.state_dirpath.iterdir()
        if child.name.startswith(EXECUTE_FILE_PREFIX)]
    execute_jobs = [ExecuteJob.from_yaml_file(path) for path in execute_files_list]
    current_results = [job.execution.result if job.execution.result else RequestResult.NONE
                       for job in execute_jobs]
    # do not print warning about missing results if these are results we want reschedule
    if (RequestResult.NONE in current_results) and (RequestResult.NONE not in sanitized):
        ctx.logger.warning('WARN: Some requests do not have a known result yet.')
    # error out if no test results matches required ones
    if not set(current_results).intersection(sanitized):
        ctx.logger.error(
            f"""ERROR: There are no requests with result: {" or ".join(results)}.""")
        sys.exit(1)
    return sanitized


@main.command(name='execute')
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
    ctx.enter_command('execute')

    # ensure state dir is present and initialized
    initialize_state_dir(ctx)

    ctx.continue_execution = _continue
    ctx.no_wait = no_wait

    if restart_request:
        ctx.restart_request = restart_request
        ctx.continue_execution = True

    if restart_result:
        ctx.restart_result = sanitize_restart_result(ctx, restart_result)
        ctx.continue_execution = True

    if ctx.continue_execution and ctx.new_state_dir:
        ctx.logger.error(
            'NEWA state-dir was not specified! Use --state-dir or similar option.')
        sys.exit(1)

    if ctx.settings.newa_clear_on_subcommand:
        ctx.remove_job_files(EXECUTE_FILE_PREFIX)

    if test_file_presence(ctx.state_dirpath, EXECUTE_FILE_PREFIX) and \
            not ctx.continue_execution and \
            not ctx.force:
        ctx.logger.error(
            f'"{EXECUTE_FILE_PREFIX}" files already exist in state-dir {ctx.state_dirpath}, '
            'use --force to override')
        sys.exit(1)

    # check if we have sufficient TF CLI version
    check_tf_cli_version(ctx)

    # read a list of files to be scheduled just to check there are any
    schedule_job_list = list(ctx.load_schedule_jobs(filter_actions=True))
    if not schedule_job_list:
        ctx.logger.warning('Warning: There are no previously scheduled jobs to execute')
        return

    # initialize RP connection
    rp = initialize_rp_connection(ctx)

    # initialize ET connection
    if ctx.settings.et_enable_comments:
        et = initialize_et_connection(ctx)

    # store timestamp of this execution
    ctx.timestamp = str(datetime.datetime.now(datetime.timezone.utc).timestamp())
    tf_token = ctx.settings.tf_token
    if not tf_token:
        raise ValueError("TESTING_FARM_API_TOKEN not set!")
    # make TESTING_FARM_API_TOKEN available to workers as envvar if it has been
    # defined only though the settings file
    os.environ["TESTING_FARM_API_TOKEN"] = tf_token

    # before actual scheduling prepare RP launches and store their ids
    # we will create one launch per Jira issue so we need to sort out
    # schedule_jobs per Jira id
    jira_schedule_job_mapping = {}
    # load all jobs at first as we would be rewriting them later
    for schedule_job in schedule_job_list:
        jira_id = schedule_job.jira.id
        if jira_id not in jira_schedule_job_mapping:
            jira_schedule_job_mapping[jira_id] = [schedule_job]
        else:
            jira_schedule_job_mapping[jira_id].append(schedule_job)
    # store all launch uuids for later finishing
    launch_list = []
    # now we process jobs for each jira_id
    jira_url = ctx.settings.jira_url
    for jira_id, schedule_jobs in jira_schedule_job_mapping.items():

        job = schedule_jobs[0]

        # now we create or reuse RP launch
        if schedule_jobs[0].request.reportportal:
            # when --continue the launch was probably already created
            # check the 1st job for launch_uuid
            launch_uuid = job.request.reportportal.get('launch_uuid', None)
            launch_description = job.request.reportportal.get('launch_description', '')
            if launch_description:
                launch_description += '<br><br>'
            # add the number of jobs
            if not jira_id.startswith(JIRA_NONE_ID):
                issue_url = urllib.parse.urljoin(
                    jira_url,
                    f"/browse/{jira_id}")
                launch_description += f'[{jira_id}]({issue_url}): '
            launch_description += (f'{len(schedule_jobs)} '
                                   'request(s) in total')
            # at this point we have the beginning of launch description
            # we will eventually (re)use it when restarting requests
            if launch_uuid:
                ctx.logger.debug(
                    f'Skipping RP launch creation for {jira_id} as {launch_uuid} already exists.')
                launch_list.append(launch_uuid)
                continue

            # otherwise we proceed with launch creation
            # get additional launch details from the first schedule job
            launch_name = schedule_jobs[0].request.reportportal['launch_name'].strip()
            if not launch_name:
                raise Exception("RP launch name is not configured")
            launch_attrs = schedule_jobs[0].request.reportportal.get(
                'launch_attributes', {})
            launch_attrs.update({'newa_statedir': str(ctx.state_dirpath)})
            # we store CLI --context definitions as well but not overriding
            # existing launch_attributes
            for (k, v) in ctx.cli_context.items():
                if k in launch_attrs:
                    ctx.logger.debug(
                        f'Not storing context {k} as launch attribute due to a collision')
                else:
                    launch_attrs[k] = v
            # when testing erratum, add special context erratum=XXXX
            if schedule_jobs[0].erratum and 'erratum' not in launch_attrs:
                launch_attrs['erratum'] = str(schedule_jobs[0].erratum.id)
            # create the actual launch
            launch_uuid = rp.create_launch(launch_name,
                                           launch_description,
                                           attributes=launch_attrs)
            if not launch_uuid:
                raise Exception('Failed to create RP launch')
            launch_list.append(launch_uuid)
            # save each schedule job with launch_uuid and launch_url
            ctx.logger.info(f'Created RP launch {launch_uuid} for issue {jira_id}')
            launch_url = rp.get_launch_url(launch_uuid)
            for job in jira_schedule_job_mapping[jira_id]:
                job.request.reportportal['launch_uuid'] = launch_uuid
                job.request.reportportal['launch_url'] = launch_url
                ctx.save_schedule_job(job)

        # update Jira issue with a note about the execution
        if not jira_id.startswith(JIRA_NONE_ID):
            jira_connection = initialize_jira_connection(ctx)
            if schedule_jobs[0].request.reportportal:
                comment = ("NEWA has scheduled automated test recipe for this issue, test "
                           f"results will be uploaded to ReportPortal launch\n{launch_url}")
            else:
                comment = "NEWA has scheduled automated test recipe for this issue"
            # check if we have a comment footer defined in envvar
            footer = os.environ.get('NEWA_COMMENT_FOOTER', '').strip()
            if footer:
                comment += f'\n{footer}'
            try:
                jira_connection.add_comment(
                    jira_id,
                    comment,
                    visibility={
                        'type': 'group',
                        'value': job.jira.group}
                    if job.jira.group else None)
                ctx.logger.info(
                    f'Jira issue {jira_id} was updated with a comment '
                    'about initiated test execution')
            except jira.JIRAError as e:
                raise Exception(f"Unable to add a comment to issue {jira_id}!") from e

            # update Errata Tool with a comment when required
            if (ctx.settings.et_enable_comments and
                    ErratumCommentTrigger.EXECUTE in job.jira.erratum_comment_triggers and
                    job.erratum):
                issue_summary = jira_connection.issue(jira_id).fields.summary
                issue_url = urllib.parse.urljoin(ctx.settings.jira_url, f"/browse/{jira_id}")
                et.add_comment(
                    job.erratum.id,
                    'The New Errata Workflow Automation (NEWA) has initiated test execution '
                    'for this advisory.\n'
                    f'{jira_id} - {issue_summary}\n'
                    f'{issue_url}\n'
                    f'{launch_url}')
                ctx.logger.info(
                    f"Erratum {job.erratum.id} was updated with a comment about {jira_id}")

    # prepare a list of yaml files for workers to process
    schedule_list = [(ctx, ctx.get_schedule_job_filepath(job))
                     for job in schedule_job_list]
    worker_pool = multiprocessing.Pool(workers if workers > 0 else len(schedule_list))
    for _ in worker_pool.starmap(worker, schedule_list):
        # small sleep to avoid race conditions inside tmt code
        time.sleep(0.1)

    # do some final RP launch updates
    if schedule_jobs[0].request.reportportal:
        # for ctx.no_wait update launch description at least with TF requests API URLs
        if ctx.no_wait:
            rp_chars_limit = ctx.settings.rp_launch_descr_chars_limit \
                or RP_LAUNCH_DESCR_CHARS_LIMIT
            rp_launch_descr_updated = launch_description + "\n"
            rp_launch_descr_dots = True
            for execute_job in ctx.load_execute_jobs(filter_actions=True):
                req_link = f"[{execute_job.request.id}]({execute_job.execution.request_api})\n"
                if len(req_link) + len(rp_launch_descr_updated) < int(rp_chars_limit):
                    rp_launch_descr_updated += req_link
                elif rp_launch_descr_dots:
                    rp_launch_descr_updated += "\n..."
                    rp_launch_descr_dots = False
            rp.update_launch(launch_uuid, description=rp_launch_descr_updated)

        ctx.logger.info('Finished execution')

        # let's keep the RP lauch unfinished when using --no-wait
        if not ctx.no_wait:
            # finish all RP launches so that they won't remain unfinished
            # in the report step we will update description with additional
            # details about the result
            for launch_uuid in launch_list:
                ctx.logger.info(f'Finishing launch {launch_uuid}')
                rp.finish_launch(launch_uuid)
                rp.check_for_empty_launch(launch_uuid, logger=ctx.logger)


def test_patterns_match(s: str, patterns: list[str]) -> tuple[bool, str]:
    for pattern in patterns:
        if s.strip() == pattern.strip():
            return (True, pattern)
    return (False, '')


def worker(ctx: CLIContext, schedule_file: Path) -> None:

    # read request details
    schedule_job = ScheduleJob.from_yaml_file(Path(schedule_file))
    if schedule_job.request.how == ExecuteHow.TMT:
        tmt_worker(ctx, schedule_file, schedule_job)
    else:
        tf_worker(ctx, schedule_file, schedule_job)


def tf_worker(ctx: CLIContext, schedule_file: Path, schedule_job: ScheduleJob) -> None:

    # modify log message so it contains name of the processed file
    # so that we can distinguish individual workers
    log = partial(lambda msg: ctx.logger.info("%s: %s", schedule_file.name, msg))

    log('processing TF request...')

    start_new_request = True
    skip_initial_sleep = False
    # if --continue, then read ExecuteJob details as well
    if ctx.continue_execution:
        parent = schedule_file.parent
        name = schedule_file.name
        execute_job_file = Path(
            os.path.join(
                parent,
                name.replace(
                    SCHEDULE_FILE_PREFIX,
                    EXECUTE_FILE_PREFIX,
                    1)))
        if execute_job_file.exists():
            execute_job = ExecuteJob.from_yaml_file(execute_job_file)
            if execute_job.execution.result in ctx.restart_result:
                log(f'Restarting request {execute_job.request.id}'
                    f' with result {execute_job.execution.result}')
            elif ctx.restart_request:
                (match, pattern) = test_patterns_match(execute_job.request.id, ctx.restart_request)
                if match:
                    log(f'Restarting request {execute_job.request.id} with ID matching {pattern}')
                else:
                    start_new_request = False
            else:
                start_new_request = False

    if start_new_request:
        log('initiating TF request')
        tf_request = schedule_job.request.initiate_tf_request(ctx)
        log(f'TF request filed with uuid {tf_request.uuid}')

        # generate Tf command so we can log it
        command_args, environment = schedule_job.request.generate_tf_exec_command(ctx)
        command = ' '.join(command_args)
        # hide tokens
        command = command.replace(ctx.settings.rp_token, '***')
        # export Execution to YAML so that we can report it even later
        # we won't report 'return_code' since it is not known yet
        # This is something to be implemented later
        execute_job = ExecuteJob(
            event=schedule_job.event,
            erratum=schedule_job.erratum,
            compose=schedule_job.compose,
            rog=schedule_job.rog,
            jira=schedule_job.jira,
            recipe=schedule_job.recipe,
            request=schedule_job.request,
            execution=Execution(request_uuid=tf_request.uuid,
                                request_api=tf_request.api,
                                batch_id=schedule_job.request.get_hash(ctx.timestamp),
                                command=command),
            )
        ctx.save_execute_job(execute_job)
    else:
        log(f'Re-using existing request {execute_job.request.id}')
        tf_request = TFRequest(api=execute_job.execution.request_api,
                               uuid=execute_job.execution.request_uuid)
        skip_initial_sleep = True

    if ctx.no_wait:
        log(f'Not waiting for TF request {tf_request.uuid} to finish (--no-wait set).')
        return

    # wait for TF job to finish
    finished = False
    delay = int(ctx.settings.tf_recheck_delay)
    while not finished:
        if not skip_initial_sleep:
            time.sleep(delay)
        skip_initial_sleep = False
        tf_request.fetch_details()
        if tf_request.details:
            state = tf_request.details['state']
            # if we don't know artifacts_url yet, try to get it now
            if not execute_job.execution.artifacts_url:
                try:
                    url = tf_request.details['run']['artifacts']
                    # store execute_job updated with artifacts_url
                    execute_job.execution.artifacts_url = url
                    ctx.save_execute_job(execute_job)
                except (KeyError, TypeError):
                    pass
            envs = ','.join([f"{e['os']['compose']}/{e['arch']}"
                             for e in tf_request.details['environments_requested']])
            log(f'TF request {tf_request.uuid} envs: {envs} state: {state}')
            finished = tf_request.is_finished()
        else:
            log(f'Could not read details of TF request {tf_request.uuid}')

    # this is to silence the linter, this cannot happen as the former loop cannot
    # finish without knowing request details
    if not tf_request.details:
        raise Exception(f"Failed to read details of TF request {tf_request.uuid}")
    result = tf_request.details['result']['overall'] if (
        tf_request.details['result'] and tf_request.details['state'] != 'error') else 'error'
    log(f'finished with result: {result}')
    # now write execution details once more
    execute_job.execution.artifacts_url = tf_request.details['run']['artifacts']
    execute_job.execution.state = state
    execute_job.execution.result = RequestResult(result)
    ctx.save_execute_job(execute_job)


def tmt_worker(ctx: CLIContext, schedule_file: Path, schedule_job: ScheduleJob) -> None:

    # modify log message so it contains name of the processed file
    # so that we can distinguish individual workers
    log = partial(lambda msg: ctx.logger.info("%s: %s", schedule_file.name, msg))
    log('processing tmt request...')

    # generate tmt command so we can log it
    command_args, environment = schedule_job.request.generate_tmt_exec_command(ctx)
    command = ''
    for e, v in environment.items():
        command += f'{e}="{v}" '
    command += ' '.join(command_args)
    # hide tokens
    command = command.replace(ctx.settings.rp_token, '***')
    # export Execution to YAML so that we can report it even later
    # we won't report 'return_code' since it is not known yet
    # This is something to be implemented later
    execute_job = ExecuteJob(
        event=schedule_job.event,
        erratum=schedule_job.erratum,
        compose=schedule_job.compose,
        rog=schedule_job.rog,
        jira=schedule_job.jira,
        recipe=schedule_job.recipe,
        request=schedule_job.request,
        execution=Execution(batch_id=schedule_job.request.get_hash(ctx.timestamp),
                            command=command),
        )
    ctx.save_execute_job(execute_job)


# prepares a fancy summary for Jira or ReportPortal
def execute_jobs_summary(ctx: CLIContext,
                         jira_id: str,
                         execute_jobs: list[ExecuteJob],
                         target: str = 'Jira') -> str:
    """
    Prepares a string with a summary of executed jobs.
    Parameter 'target' could be either 'Jira' or 'ReportPortal'.
    """
    separator = '<br>' if target == 'ReportPortal' else '\n'
    summary = ''
    # add configured RP description if available
    if execute_jobs[0].request.reportportal:
        launch_description = execute_jobs[0].request.reportportal.get(
            'launch_description', '')
        summary += launch_description + 2 * separator if launch_description else ''
    # prepare content with individual results
    results: dict[str, dict[str, str]] = {}
    for job in execute_jobs:
        results[job.request.id] = {
            'id': job.request.id,
            'state': job.execution.state,
            'result': str(job.execution.result),
            'uuid': job.execution.request_uuid,
            'url': job.execution.artifacts_url,
            'plan': job.request.tmt.get('plan', '')}
        if job.request.reportportal:
            results[job.request.id]['suite_desc'] = job.request.reportportal.get(
                'suite_description', '')
        else:
            results[job.request.id]['suite_desc'] = ''
    if not jira_id.startswith(JIRA_NONE_ID):
        jira_url = ctx.settings.jira_url
        issue_url = urllib.parse.urljoin(
            jira_url,
            f"/browse/{jira_id}")
        if target == 'ReportPortal':
            summary += f'[{jira_id}]({issue_url}): '
        else:
            summary += f'{jira_id}: '
    summary += f'{len(execute_jobs)} request(s) in total:'
    for req in sorted(results.keys(), key=lambda x: int(x.split('.')[-1])):
        # it would be nice to use hyperlinks in launch description however we
        # would hit launch description length limit. Therefore using plain text
        summary += separator
        if target == 'ReportPortal':
            summary += "{id}: {state}, {result}".format(**results[req])
        else:
            summary += (
                "| [{id}|{url}] | {state} | {result} | {plan} | {suite_desc} |".format(
                    **results[req])
                )
    return summary


@main.command(name='report')
@click.pass_obj
def cmd_report(ctx: CLIContext) -> None:
    ctx.enter_command('report')

    # ensure state dir is present and initialized
    initialize_state_dir(ctx)

    all_execute_jobs = list(ctx.load_execute_jobs(filter_actions=True))
    if not all_execute_jobs:
        ctx.logger.warning('Warning: There are no previously executed jobs to report')
        return

    # initialize RP connection if RP instance is configured
    if ctx.settings.rp_url:
        rp = initialize_rp_connection(ctx)

    # initialize Jira connection
    jira_connection = initialize_jira_connection(ctx)
    # initialize ET connection
    if ctx.settings.et_enable_comments:
        et = initialize_et_connection(ctx)

    # process each stored execute file
    # before actual reporting split jobs per jira id
    jira_execute_job_mapping = {}
    # load all jobs at first as we would be rewriting them later
    for execute_job in all_execute_jobs:
        jira_id = execute_job.jira.id
        if jira_id not in jira_execute_job_mapping:
            jira_execute_job_mapping[jira_id] = [execute_job]
        else:
            jira_execute_job_mapping[jira_id].append(execute_job)
        # if execute_job is not yes finished, do one status check
        if (execute_job.execution.result == RequestResult.NONE) and \
                execute_job.request.how == ExecuteHow.TESTING_FARM:
            tf_request = TFRequest(api=execute_job.execution.request_api,
                                   uuid=execute_job.execution.request_uuid)
            tf_request.fetch_details()
            if not tf_request.details:
                raise Exception(f"Failed to read details of TF request {tf_request.uuid}")
            state = tf_request.details['state']
            envs = ','.join([f"{e['os']['compose']}/{e['arch']}"
                             for e in tf_request.details['environments_requested']])
            ctx.logger.info(f'TF request {tf_request.uuid} envs: {envs} state: {state}')
            if tf_request.details['result']:
                execute_job.execution.result = RequestResult(
                    tf_request.details['result']['overall'])
                ctx.logger.info(f'finished with result: {execute_job.execution.result}')
            elif tf_request.is_finished():
                execute_job.execution.result = RequestResult.ERROR
            if tf_request.details['state']:
                execute_job.execution.state = tf_request.details['state']
            # artifacts won't be available yet if the request is still queued
            if tf_request.details['run'] and tf_request.details['run'].get('artifacts', None):
                execute_job.execution.artifacts_url = tf_request.details['run']['artifacts']
            ctx.save_execute_job(execute_job)

    # now for each jira id finish the respective launch and report results
    for jira_id, execute_jobs in jira_execute_job_mapping.items():

        # get some RP launch details
        launch_uuid = None
        launch_url = None
        if execute_jobs[0].request.reportportal:
            launch_uuid = execute_jobs[0].request.reportportal.get(
                'launch_uuid', None)
            launch_url = execute_jobs[0].request.reportportal.get(
                'launch_url', None)
        if launch_uuid:
            rp.check_for_empty_launch(launch_uuid, logger=ctx.logger)

        # check if all tests have finished and passed
        all_tests_passed = True
        all_tests_finished = True
        for job in execute_jobs:
            if job.execution.result != RequestResult.PASSED:
                all_tests_passed = False
            if job.execution.state not in TF_REQUEST_FINISHED_STATES:
                all_tests_finished = False
        jira_description = execute_jobs_summary(ctx, jira_id, execute_jobs, target='Jira')
        launch_description = execute_jobs_summary(
            ctx, jira_id, execute_jobs, target='ReportPortal')

        if launch_uuid:
            # finish launch just in case it hasn't been finished already
            # and update description with more detailed results
            rp.finish_launch(launch_uuid)
            ctx.logger.info(f'Updating launch description, {launch_url}')
            rp.update_launch(launch_uuid, description=launch_description)

        # do not report to Jira if JIRA_NONE_ID was used
        if not jira_id.startswith(JIRA_NONE_ID):
            try:
                if launch_uuid:
                    comment = ("NEWA has finished test execution and imported test results "
                               f"to RP launch\n{launch_url}\n\n{jira_description}")
                else:
                    comment = (f"NEWA has finished test execution\n\n{jira_description}")
                # check if we have a comment footer defined in envvar
                footer = os.environ.get('NEWA_COMMENT_FOOTER', '').strip()
                if footer:
                    comment += f'\n{footer}'
                jira_connection.add_comment(
                    jira_id,
                    comment,
                    visibility={
                        'type': 'group',
                        'value': execute_job.jira.group}
                    if execute_job.jira.group else None)
                ctx.logger.debug(
                    f'Jira issue {jira_id} was updated with test results')
            except jira.JIRAError as e:
                raise Exception(f"Unable to add a comment to issue {jira_id}!") from e
            # change Jira issue state if required
            if execute_job.jira.transition_passed and all_tests_passed:
                issue_transition(jira_connection,
                                 execute_job.jira.transition_passed,
                                 jira_id)
                ctx.logger.info(
                    f'Issue {jira_id} state changed to {execute_job.jira.transition_passed}')
            elif execute_job.jira.transition_processed and all_tests_finished:
                issue_transition(jira_connection,
                                 execute_job.jira.transition_processed,
                                 jira_id)
                ctx.logger.info(
                    f'Issue {jira_id} state changed '
                    f'to {execute_job.jira.transition_processed}')

            # update Errata Tool with a comment when required
            if (ctx.settings.et_enable_comments and
                    ErratumCommentTrigger.REPORT in
                    execute_job.jira.erratum_comment_triggers and
                    execute_job.erratum):
                issue_summary = jira_connection.issue(jira_id).fields.summary
                issue_url = urllib.parse.urljoin(ctx.settings.jira_url, f"/browse/{jira_id}")
                comment = 'The New Errata Workflow Automation (NEWA) has finished '
                'test execution for this advisory.\n'
                f'{jira_id} - {issue_summary}\n'
                f'{issue_url}\n'
                if launch_url:
                    comment += f'{launch_url}\n'
                    et.add_comment(execute_job.erratum.id, comment)
                    ctx.logger.info(
                        f"Erratum {execute_job.erratum.id} was updated "
                        f"with a comment about {jira_id}")
