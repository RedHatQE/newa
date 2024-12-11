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
    Arch,
    ArtifactJob,
    CLIContext,
    Compose,
    ErrataTool,
    ErratumCommentTrigger,
    ErratumContentType,
    Event,
    EventType,
    ExecuteJob,
    Execution,
    Issue,
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
    ScheduleJob,
    Settings,
    TFRequest,
    eval_test,
    get_url_basename,
    render_template,
    yaml_parser,
    )

JIRA_NONE_ID = '_NO_ISSUE'
STATEDIR_PARENT_DIR = Path('/var/tmp/newa')
STATEDIR_NAME_PATTERN = r'^run-([0-9]+)$'
TF_RESULT_PASSED = 'passed'
ARGS_WITH_NO_STATEDIR = ['list', '--help']

logging.basicConfig(
    format='%(asctime)s %(message)s',
    datefmt='%m/%d/%Y %I:%M:%S %p',
    level=logging.INFO)


def get_state_dir(use_ppid: bool = False) -> Path:
    """ When not using ppid returns the first unused directory
        matching /var/tmp/newa/run-[0-9]+, starting with run-1
        When using ppid searches for the most recent state-dir directory
        containing file $PPID.ppid
    """
    counter = 0
    last_dir = None
    ppid_filename = f'{os.getppid()}.ppid'
    try:
        obj = os.scandir(STATEDIR_PARENT_DIR)
    except FileNotFoundError as e:
        if use_ppid:
            raise Exception(f'{STATEDIR_PARENT_DIR} does not exist') from e
        # return initial value run-1
        return STATEDIR_PARENT_DIR / f'run-{counter + 1}'
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
        raise Exception(f'File {ppid_filename} not found under {STATEDIR_PARENT_DIR}')
    # otherwise return the first unused value
    return STATEDIR_PARENT_DIR / f'run-{counter + 1}'


def initialize_jira_connection(ctx: CLIContext) -> Any:
    jira_url = ctx.settings.jira_url
    if not jira_url:
        raise Exception('Jira URL is not configured!')
    jira_token = ctx.settings.jira_token
    if not jira_token:
        raise Exception('Jira token is not configured!')
    return jira.JIRA(jira_url, token_auth=jira_token)


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
    is_flag=True,
    default=False,
    help='Use the latest state-dir used previously within this shell session',
    )
@click.option(
    '--conf-file',
    default='$HOME/.newa',
    help='Path to newa configuration file.',
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
    default='',
    help='Extract YAML files from the specified archive to state-dir.',
    )
@click.pass_context
def main(click_context: click.Context,
         state_dir: str,
         prev_state_dir: bool,
         conf_file: str,
         debug: bool,
         envvars: list[str],
         contexts: list[str],
         extract_state_dir: str) -> None:

    # handle state_dir settings
    if prev_state_dir and state_dir:
        raise Exception('Use either --state-dir or --prev-state-dir')
    if prev_state_dir:
        state_dir = str(get_state_dir(use_ppid=True))
    elif not state_dir:
        state_dir = str(get_state_dir())

    ctx = CLIContext(
        settings=Settings.load(Path(os.path.expandvars(conf_file))),
        logger=logging.getLogger(),
        state_dirpath=Path(os.path.expandvars(state_dir)),
        cli_environment={},
        cli_context={},
        )
    click_context.obj = ctx

    if debug:
        ctx.logger.setLevel(logging.DEBUG)

    # this is here just to suppress state-dir creation
    # for certain cmdline arguments
    if (not extract_state_dir) and set(ARGS_WITH_NO_STATEDIR) & set(sys.argv):
        return

    ctx.logger.info(f'Using --state-dir={ctx.state_dirpath}')
    if not ctx.state_dirpath.exists():
        ctx.new_state_dir = True
        ctx.logger.debug(f'State directory {ctx.state_dirpath} does not exist, creating...')
        ctx.state_dirpath.mkdir(parents=True)

    # extract YAML files from the given archive to state-dir
    if extract_state_dir:
        ctx.new_state_dir = False
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

    # create empty ppid file
    with open(os.path.join(ctx.state_dirpath, f'{os.getppid()}.ppid'), 'w'):
        pass

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
            entries = os.scandir(STATEDIR_PARENT_DIR)
        except FileNotFoundError as e:
            raise Exception(f'{STATEDIR_PARENT_DIR} does not exist') from e
        sorted_entries = sorted(entries, key=lambda entry: os.path.getmtime(Path(entry)))
        state_dirs = [Path(e.path) for e in sorted_entries[-last:]]

    def _print(indent: int, s: str, end: str = '\n') -> None:
        print(f'{" " * indent}{s}', end=end)

    for state_dir in state_dirs:
        print(f'{state_dir}:')
        ctx.state_dirpath = state_dir
        event_jobs = list(ctx.load_artifact_jobs('event-'))
        for event_job in event_jobs:
            if event_job.erratum:
                _print(2, f'event {event_job.id} - {event_job.erratum.summary}')
                _print(2, event_job.erratum.url)
            else:
                _print(2, f'event {event_job.id}')
            jira_file_prefix = f'jira-{event_job.event.id}-{event_job.short_id}'
            jira_jobs = list(ctx.load_jira_jobs(jira_file_prefix))
            for jira_job in jira_jobs:
                jira_summary = f'- {jira_job.jira.summary}' if jira_job.jira.summary else ''
                _print(4, f'issue {jira_job.jira.id} {jira_summary}')
                if jira_job.jira.url:
                    _print(4, jira_job.jira.url)
                schedule_file_prefix = (f'schedule-{event_job.event.id}-'
                                        f'{event_job.short_id}-{jira_job.jira.id}')
                schedule_jobs = list(ctx.load_schedule_jobs(schedule_file_prefix))
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
                    execute_file_prefix = (f'execute-{event_job.event.id}-'
                                           f'{event_job.short_id}-{jira_job.jira.id}-'
                                           f'{schedule_job.request.id}')
                    execute_jobs = list(ctx.load_execute_jobs(execute_file_prefix))
                    if execute_jobs:
                        for execute_job in execute_jobs:
                            if hasattr(execute_job, 'execution'):
                                state = getattr(execute_job.execution, "state", "unknown")
                                # if state was None check of request_uuid
                                if (not state) and getattr(
                                        execute_job.execution, "request_uuid", None):
                                    state = 'executed, not reported'
                                result = getattr(execute_job.execution, "result", "unknown")
                                url = getattr(
                                    execute_job.execution, "artifacts_url", "not available")
                                print(f' - state: {state}, result: {result}, artifacts: {url}')
                    else:
                        print(' - not executed')
        print()
    # no other command will be processed
    sys.exit(0)


def apply_release_mapping(string: str,
                          mapping: Optional[list[str]] = None,
                          regexp: bool = True,
                          logger: Optional[logging.Logger] = None) -> str:
    # define default mapping
    if not mapping:
        mapping = [
            r'\.GA$=',
            r'\.Z\.(MAIN)?(\+)?(AUS|EUS|E4S|TUS)?$=',
            r'RHEL-10\.0\.BETA=RHEL-10-Beta',
            r'$=-Nightly',
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
    '--compose-mapping', 'compose_mapping',
    default=[],
    multiple=True,
    help=('Custom Erratum release to Testing Farm compose mapping in the form '
          '"RELEASE=COMPOSE". For example, '
          '"--compose-mapping RHEL-9.4.0.Z.MAIN+EUS=RHEL-9.4.0-Nightly". '
          'Can be specified multiple times, the 1st match is used'
          ),
    )
@click.pass_obj
def cmd_event(
        ctx: CLIContext,
        errata_ids: list[str],
        compose_ids: list[str],
        compose_mapping: list[str]) -> None:
    ctx.enter_command('event')

    # Errata IDs were not given, try to load them from init- files.
    if not errata_ids and not compose_ids:
        events = [e.event for e in ctx.load_initial_errata('init-')]
        for event in events:
            if event.type_ is EventType.ERRATUM:
                errata_ids.append(event.id)
            if event.type_ is EventType.COMPOSE:
                compose_ids.append(event.id)

    if not errata_ids and not compose_ids:
        raise Exception('Missing event IDs!')

    # process errata IDs
    if errata_ids:
        # Abort if there are still no errata IDs.
        et_url = ctx.settings.et_url
        if not et_url:
            raise Exception('Errata Tool URL is not configured!')

        for erratum_id in errata_ids:
            event = Event(type_=EventType.ERRATUM, id=erratum_id)
            errata = ErrataTool(url=et_url).get_errata(event)
            for erratum in errata:
                release = erratum.release.strip()
                # when compose_mapping is provided, apply it with regexp disabled
                if compose_mapping:
                    compose = apply_release_mapping(
                        release, compose_mapping, regexp=False, logger=ctx.logger)
                # otherwise use the built-in default mapping
                else:
                    compose = apply_release_mapping(release, logger=ctx.logger)
                # skip compose if it has been transformed to an empty compose
                if not compose:
                    ctx.logger.info(
                        f"""Erratum release {release} transformed to an empty string, skipping""")
                    continue
                ctx.logger.info(
                    f"""Erratum release {release} transformed to a compose {compose}""")

                if erratum.content_type in (ErratumContentType.RPM, ErratumContentType.MODULE):
                    artifact_job = ArtifactJob(event=event, erratum=erratum,
                                               compose=Compose(id=compose))
                    ctx.save_artifact_job('event-', artifact_job)
                # for docker content type we create ArtifactJob per build
                if erratum.content_type == ErratumContentType.DOCKER:
                    erratum_clone = erratum.clone()
                    for build in erratum.builds:
                        erratum_clone.builds = [build]
                        erratum_clone.components = [NVRParser(build).name]
                        artifact_job = ArtifactJob(event=event, erratum=erratum_clone,
                                                   compose=Compose(id=compose))
                        ctx.save_artifact_job('event-', artifact_job)

    # process compose IDs
    for compose_id in compose_ids:
        event = Event(type_=EventType.COMPOSE, id=compose_id)
        artifact_job = ArtifactJob(event=event, erratum=None, compose=Compose(id=compose_id))
        ctx.save_artifact_job('event-', artifact_job)


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
        recreate: bool,
        issue: str,
        job_recipe: str,
        assignee: str,
        unassigned: bool) -> None:
    ctx.enter_command('jira')

    jira_url = ctx.settings.jira_url
    if not jira_url:
        raise Exception('Jira URL is not configured!')

    jira_token = ctx.settings.jira_token
    if not jira_token:
        raise Exception('Jira token is not configured!')

    if assignee and unassigned:
        raise Exception('Options --assignee and --unassigned cannot be used together')

    # initialize ET connection
    if ctx.settings.et_enable_comments:
        et_url = ctx.settings.et_url
        if not et_url:
            raise Exception('Errata Tool URL is not configured!')
        et = ErrataTool(url=et_url)

    def _jira_fake_id_generator() -> Generator[str, int, None]:
        n = 1
        while True:
            yield f'{JIRA_NONE_ID}_{n}'
            n += 1

    jira_none_id = _jira_fake_id_generator()

    # load issue mapping specified on a command line
    issue_mapping: dict[str, str] = {}
    artifact_jobs = ctx.load_artifact_jobs('event-')

    # issue mapping is relevant only when using issue-config file
    # check for wrong ids provided on a cmdline
    if issue_config:
        # read Jira issue configuration
        config = IssueConfig.read_file(os.path.expandvars(issue_config))

        # read --map-issue keys and values into a dictionary
        for m in map_issue:
            r = re.fullmatch(r'([^\s=]+)=([^=]*)', m)
            if not r:
                raise Exception(f"Mapping {m} does not having expected format 'key=value'")
            key, value = r.groups()
            issue_mapping[key] = value
        # gather ids from the config file
        ids = [getattr(action, "id", None) for action in config.issues[:]]
        # check for keys not present in a config file
        for key in issue_mapping:
            if key not in ids:
                raise Exception(f"Key '{key}' from mapping '{m}' doesn't match issue item id "
                                f"from '{os.path.expandvars(issue_config)}'. Typo?")

    for artifact_job in artifact_jobs:
        # when issue_config is defined, --issue and --job-recipe are ignored
        # as it will be set depending on the --issue-config content
        if issue_config:

            jira_handler = IssueHandler(
                artifact_job,
                jira_url,
                jira_token,
                config.project,
                config.transitions,
                group=getattr(
                    config,
                    'group',
                    None))
            ctx.logger.info("Initialized Jira handler")

            # All issue action from the configuration.
            issue_actions = config.issues[:]

            # Processed action (action.id : issue).
            processed_actions: dict[str, Issue] = {}

            # action_ids for which new Issues have been created
            created_action_ids: list[str] = []

            # Length of the queue the last time issue action was processed,
            # Use to prevent endless loop over the issue actions.
            endless_loop_check: dict[str, int] = {}

            # Iterate over issue actions. Take one, if it's not possible to finish it,
            # put it back at the end of the queue.
            while issue_actions:
                action = issue_actions.pop(0)

                if not action.id:
                    raise Exception(f"Action {action} does not have 'id' assigned")

                ctx.logger.info(f"Processing {action.id}")

                if action.when and not eval_test(action.when,
                                                 JOB=artifact_job,
                                                 EVENT=artifact_job.event,
                                                 ERRATUM=artifact_job.erratum,
                                                 COMPOSE=artifact_job.compose,
                                                 ENVIRONMENT=ctx.cli_environment):
                    ctx.logger.info(f"Skipped, issue action is irrelevant ({action.when})")
                    continue

                if not action.summary:
                    raise Exception(f"Action {action} does not have a 'summary' defined.")
                if not action.description:
                    raise Exception(f"Action {action} does not have a 'description' defined.")

                rendered_summary = render_template(
                    action.summary,
                    ERRATUM=artifact_job.erratum,
                    COMPOSE=artifact_job.compose,
                    ENVIRONMENT=ctx.cli_environment)
                rendered_description = render_template(
                    action.description,
                    ERRATUM=artifact_job.erratum,
                    COMPOSE=artifact_job.compose,
                    ENVIRONMENT=ctx.cli_environment)
                if assignee:
                    rendered_assignee = assignee
                elif unassigned:
                    rendered_assignee = None
                elif action.assignee:
                    rendered_assignee = render_template(
                        action.assignee,
                        ERRATUM=artifact_job.erratum,
                        COMPOSE=artifact_job.compose,
                        ENVIRONMENT=ctx.cli_environment)
                else:
                    rendered_assignee = None
                if action.newa_id:
                    action.newa_id = render_template(
                        action.newa_id,
                        ERRATUM=artifact_job.erratum,
                        COMPOSE=artifact_job.compose,
                        ENVIRONMENT=ctx.cli_environment)

                # Detect that action has parent available (if applicable), if we went trough the
                # actions already and parent was not found, we abort.
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

                # Issues related to the curent respin and previous one(s).
                new_issues: list[Issue] = []
                old_issues: list[Issue] = []

                # read transition settings
                transition_passed = None
                transition_processed = None
                if action.auto_transition:
                    if jira_handler.transitions.passed:
                        transition_passed = jira_handler.transitions.passed[0]
                    if jira_handler.transitions.processed:
                        transition_processed = jira_handler.transitions.processed[0]

                # first check if we have a match in issue_mapping
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

                # otherwise we need to search for the issue in Jira
                else:
                    # Find existing issues related to artifact_job and action
                    # If we are supposed to recreate closed issues, search only for opened ones
                    if recreate:
                        search_result = jira_handler.get_related_issues(
                            action, all_respins=True, closed=False)
                    else:
                        search_result = jira_handler.get_related_issues(
                            action, all_respins=True, closed=True)

                    for jira_issue_key, jira_issue in search_result.items():
                        ctx.logger.info(f"Checking {jira_issue_key}")

                        # In general, issue is new (relevant to the current respin) if it has
                        # newa_id of this action in the description. Otherwise, it is old
                        # (relevant to the previous respins).
                        # However, it might happen that we encounter an issue that is new but
                        # its original parent has been replaced by a newly created issue.
                        # In such a case we have to re-create the issue as well and drop the
                        # old one.
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
                        # opened old issues may be reused
                        elif jira_issue["status"] == "opened":
                            old_issues.append(
                                Issue(
                                    jira_issue_key,
                                    group=config.group,
                                    closed=False,
                                    transition_passed=transition_passed,
                                    transition_processed=transition_processed))

                # Old opened issue(s) can be re-used for the current respin.
                if old_issues and action.on_respin == OnRespinAction.KEEP:
                    new_issues.extend(old_issues)
                    old_issues = []

                # Unless we want recreate closed issues we would stop processing
                # if new_issues are closed as it means they are already processed by a user
                if new_issues and (not recreate):
                    opened_issues = [i for i in new_issues if not i.closed]
                    closed_issues = [i for i in new_issues if i.closed]
                    # if there are no opened new issues we are done processing
                    if not opened_issues:
                        closed_ids = ', '.join([i.id for i in closed_issues])
                        ctx.logger.info(
                            f"Relevant issues {closed_ids} found but already closed")
                        continue
                    # otherwise we continue processing new issues
                    new_issues = opened_issues

                # Processing new opened issues.
                #
                # 1. Either there is no new issue (it does not exist yet - we need to create it).
                trigger_erratum_comment = False
                if not new_issues:
                    parent = None
                    if action.parent_id:
                        parent = processed_actions.get(action.parent_id, None)

                    new_issue = jira_handler.create_issue(
                        action,
                        rendered_summary,
                        rendered_description,
                        rendered_assignee,
                        parent,
                        group=config.group,
                        transition_passed=transition_passed,
                        transition_processed=transition_processed,
                        fields=action.fields)

                    processed_actions[action.id] = new_issue
                    created_action_ids.append(action.id)

                    new_issues.append(new_issue)
                    ctx.logger.info(f"New issue {new_issue.id} created")
                    trigger_erratum_comment = True

                # Or there is exactly one new issue (already created or re-used old issue).
                elif len(new_issues) == 1:
                    new_issue = new_issues[0]
                    processed_actions[action.id] = new_issue

                    # If the old issue was reused, re-fresh it.
                    trigger_erratum_comment = jira_handler.refresh_issue(action, new_issue)
                    ctx.logger.info(f"Issue {new_issue} re-used")

                # But if there are more than one new issues we encountered error.
                else:
                    raise Exception(f"More than one new {action.id} found ({new_issues})!")

                # update Errata Tool with a comment when required
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

                if action.job_recipe:
                    recipe_url = render_template(
                        action.job_recipe,
                        ERRATUM=artifact_job.erratum,
                        COMPOSE=artifact_job.compose,
                        ENVIRONMENT=ctx.cli_environment)
                    if action.erratum_comment_triggers:
                        new_issue.erratum_comment_triggers = action.erratum_comment_triggers
                    jira_job = JiraJob(event=artifact_job.event,
                                       erratum=artifact_job.erratum,
                                       compose=artifact_job.compose,
                                       jira=new_issue,
                                       recipe=Recipe(url=recipe_url))
                    ctx.save_jira_job('jira-', jira_job)

                # Processing old issues - we only expect old issues that are to be closed (if any).
                if old_issues:
                    if action.on_respin != OnRespinAction.CLOSE:
                        raise Exception(
                            f"Invalid respin action {action.on_respin} for {old_issues}!")
                    for old_issue in old_issues:
                        jira_handler.drop_obsoleted_issue(
                            old_issue, obsoleted_by=processed_actions[action.id])
                        ctx.logger.info(f"Old issue {old_issue} closed")

        # when there is no issue_config we will create one
        # using --issue and --job_recipe parameters
        else:
            if not job_recipe:
                raise Exception("Option --job-recipe is mandatory when --issue-config is not set")
            if issue:
                # verify that specified Jira issue truly exists
                jira_connection = initialize_jira_connection(ctx)
                jira_connection.issue(issue)
                ctx.logger.info(f"Using issue {issue}")
                new_issue = Issue(issue)
            else:
                # when --issue is not specified, we would use an empty string as ID
                # so we will skip Jira reporting steps in later stages
                new_issue = Issue(next(jira_none_id))

            jira_job = JiraJob(event=artifact_job.event,
                               erratum=artifact_job.erratum,
                               compose=artifact_job.compose,
                               jira=new_issue,
                               recipe=Recipe(url=job_recipe))
            ctx.save_jira_job('jira-', jira_job)


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
@click.pass_obj
def cmd_schedule(ctx: CLIContext, arch: list[str], fixtures: list[str]) -> None:
    ctx.enter_command('schedule')

    for jira_job in ctx.load_jira_jobs('jira-'):
        # prepare parameters based on the recipe from recipe.url
        # generate all relevant test request using the recipe data
        # prepare a list of Request objects

        # would it be OK not to pass compose to TF? I guess so
        compose = jira_job.compose.id if jira_job.compose else None
        if arch:
            architectures = Arch.architectures(
                [Arch(a.strip()) for a in arch])
        else:
            architectures = jira_job.erratum.archs if (
                jira_job.erratum and jira_job.erratum.archs) else Arch.architectures()
        initial_config = RawRecipeConfigDimension(compose=compose,
                                                  environment=ctx.cli_environment,
                                                  context=ctx.cli_context)
        ctx.logger.debug(f'Initial config: {initial_config})')
        if fixtures:
            for fixture in fixtures:
                r = re.fullmatch(r'([^\s=]+)=([^=]*)', fixture)
                if not r:
                    raise Exception(
                        f"Fixture {fixture} does not having expected format 'name=value'")
                fixture_name, fixture_value = r.groups()
                fixture_config = initial_config
                # descent through keys to the lowest level
                while '.' in fixture_name:
                    prefix, suffix = fixture_name.split('.', 1)
                    fixture_config = fixture_config.setdefault(prefix, {})  # type: ignore [misc]
                    fixture_name = suffix
                # now we are at the lowest level
                # Is it beneficial to parse the input as yaml?
                # It enables us to define list and dicts but there might be drawbacks as well
                value = yaml_parser().load(fixture_value)
                fixture_config[fixture_name] = value  # type: ignore[literal-required]
            ctx.logger.debug(f'Initial config modified through --fixture: {initial_config})')

        # when testing erratum, add special context erratum=XXXX
        if jira_job.erratum:
            initial_config['context'].update({'erratum': str(jira_job.erratum.id)})

        if re.search('^https?://', jira_job.recipe.url):
            config = RecipeConfig.from_yaml_url(jira_job.recipe.url)
        else:
            config = RecipeConfig.from_yaml_file(Path(jira_job.recipe.url))
        # extend dimensions with system architecture but do not override existing settings
        if 'arch' not in config.dimensions:
            config.dimensions['arch'] = []
            for architecture in architectures:
                config.dimensions['arch'].append({'arch': architecture})
        # if RP launch name is not specified in the recipe, set it based on the recipe filename
        if not config.fixtures.get('reportportal', None):
            config.fixtures['reportportal'] = RawRecipeReportPortalConfigDimension()
        # Populate default for config.fixtures['reportportal']['launch_name']
        # Although config.fixtures['reportportal'] is not None, though linter still complaints
        # so we repeat the condition once more
        if ((config.fixtures['reportportal'] is not None) and
                (not config.fixtures['reportportal'].get('launch_name', None))):
            config.fixtures['reportportal']['launch_name'] = os.path.splitext(
                get_url_basename(jira_job.recipe.url))[0]
        # build requests
        jinja_vars: dict[str, Any] = {
            'ERRATUM': jira_job.erratum,
            }

        requests = list(config.build_requests(initial_config, jinja_vars))
        ctx.logger.info(f'{len(requests)} requests have been generated')

        # create ScheduleJob object for each request
        for request in requests:
            # prepare dict for Jinja template rendering
            jinja_vars = {
                'ERRATUM': jira_job.erratum,
                'COMPOSE': jira_job.compose,
                'CONTEXT': request.context,
                'ENVIRONMENT': request.environment}
            # before yaml export render all fields as Jinja templates
            for attr in (
                    "reportportal",
                    "tmt",
                    "testingfarm",
                    "environment",
                    "context",
                    "compose"):
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

            # export schedule_job yaml
            schedule_job = ScheduleJob(
                event=jira_job.event,
                erratum=jira_job.erratum,
                compose=jira_job.compose,
                jira=jira_job.jira,
                recipe=jira_job.recipe,
                request=request)
            ctx.save_schedule_job('schedule-', schedule_job)


@main.command(name='cancel')
@click.pass_obj
def cmd_cancel(ctx: CLIContext) -> None:
    ctx.enter_command('cancel')
    # make TESTING_FARM_API_TOKEN available to workers as envvar if it has been
    # defined only though the settings file
    tf_token = ctx.settings.tf_token
    if not tf_token:
        raise ValueError("TESTING_FARM_API_TOKEN not set!")
    os.environ["TESTING_FARM_API_TOKEN"] = tf_token

    for execute_job in ctx.load_execute_jobs('execute-'):
        # if not execute_job.execution.result:
        tf_request = TFRequest(
            api=execute_job.execution.request_api,
            uuid=execute_job.execution.request_uuid)
        tf_request.cancel(ctx)
        tf_request.fetch_details()
        if tf_request.details:
            execute_job.execution.state = tf_request.details['state']
            if 'cancel' in execute_job.execution.state:
                execute_job.execution.state = 'canceled'
                execute_job.execution.result = 'error'
            if tf_request.details['result']:
                execute_job.execution.result = tf_request.details['result']['overall']
            ctx.save_execute_job('execute-', execute_job)


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
    ctx.continue_execution = _continue
    ctx.no_wait = no_wait

    if restart_request:
        ctx.restart_request = restart_request
        ctx.continue_execution = True

    if restart_result:
        ctx.restart_result = restart_result
        ctx.continue_execution = True

    if ctx.continue_execution and ctx.new_state_dir:
        ctx.logger.error(
            'NEWA state-dir was not specified! Use --state-dir or similar option.')
        sys.exit(1)

    # initialize RP connection
    rp_project = ctx.settings.rp_project
    rp_url = ctx.settings.rp_url
    rp = ReportPortal(url=rp_url,
                      token=ctx.settings.rp_token,
                      project=rp_project)

    # initialize ET connection
    if ctx.settings.et_enable_comments:
        et_url = ctx.settings.et_url
        if not et_url:
            raise Exception('Errata Tool URL is not configured!')
        et = ErrataTool(url=et_url)

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
    for schedule_job in ctx.load_schedule_jobs('schedule-'):
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
        # when --continue the launch was probably already created
        # check the 1st job for launch_uuid
        job = schedule_jobs[0]
        launch_uuid = job.request.reportportal.get('launch_uuid', None)
        if launch_uuid:
            ctx.logger.debug(
                f'Skipping RP launch creation for {jira_id} as {launch_uuid} already exists.')
            launch_list.append(launch_uuid)
            continue
        # otherwise we proceed with launch creation
        # get launch details from the first schedule job
        launch_name = schedule_jobs[0].request.reportportal['launch_name']
        launch_attrs = schedule_jobs[0].request.reportportal.get(
            'launch_attributes', {})
        launch_attrs.update({'newa_statedir': str(ctx.state_dirpath)})
        launch_description = schedule_jobs[0].request.reportportal.get(
            'launch_description', '')
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
            ctx.save_schedule_job('schedule-', job)

        # update Jira issue with a note about the RP launch
        if not jira_id.startswith(JIRA_NONE_ID):
            jira_connection = initialize_jira_connection(ctx)
            try:
                jira_connection.add_comment(
                    jira_id,
                    ("NEWA has scheduled automated test recipe for this issue, test "
                     f"results will be uploaded to ReportPortal launch\n{launch_url}"),
                    visibility={
                        'type': 'group',
                        'value': job.jira.group}
                    if job.jira.group else None)
                ctx.logger.info(
                    f'Jira issue {jira_id} was updated with a RP launch URL {launch_url}')
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

    # get a list of files to be scheduled so that they can be distributed across workers
    schedule_list = [
        (ctx, ctx.state_dirpath / child.name)
        for child in ctx.state_dirpath.iterdir()
        if child.name.startswith('schedule-')]

    worker_pool = multiprocessing.Pool(workers if workers > 0 else len(schedule_list))
    for _ in worker_pool.starmap(worker, schedule_list):
        # small sleep to avoid race conditions inside tmt code
        time.sleep(0.1)

    ctx.logger.info('Finished execution')

    # let's keep the RP lauch unfinished when using --no-wait
    if not ctx.no_wait:
        # finish all RP launches so that they won't remain unfinished
        # in the report step we will update description with additional
        # details about the result
        for launch_uuid in launch_list:
            ctx.logger.info(f'Finishing launch {launch_uuid}')
            rp.finish_launch(launch_uuid)


def test_patterns_match(s: str, patterns: list[str]) -> tuple[bool, str]:
    for pattern in patterns:
        if s.strip() == pattern.strip():
            return (True, pattern)
    return (False, '')


def worker(ctx: CLIContext, schedule_file: Path) -> None:

    # modify log message so it contains name of the processed file
    # so that we can distinguish individual workers
    log = partial(lambda msg: ctx.logger.info("%s: %s", schedule_file.name, msg))

    log('processing request...')
    # read request details
    schedule_job = ScheduleJob.from_yaml_file(Path(schedule_file))

    start_new_request = True
    skip_initial_sleep = False
    # if --continue, then read ExecuteJob details as well
    if ctx.continue_execution:
        parent = schedule_file.parent
        name = schedule_file.name
        execute_job_file = Path(os.path.join(parent, name.replace('schedule-', 'execute-', 1)))
        if execute_job_file.exists():
            execute_job = ExecuteJob.from_yaml_file(execute_job_file)
            if execute_job.execution.result and execute_job.execution.result in ctx.restart_result:
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
            jira=schedule_job.jira,
            recipe=schedule_job.recipe,
            request=schedule_job.request,
            execution=Execution(request_uuid=tf_request.uuid,
                                request_api=tf_request.api,
                                batch_id=schedule_job.request.get_hash(ctx.timestamp),
                                command=command),
            )
        ctx.save_execute_job('execute-', execute_job)
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
            envs = ','.join([f"{e['os']['compose']}/{e['arch']}"
                             for e in tf_request.details['environments_requested']])
            log(f'TF request {tf_request.uuid} envs: {envs} state: {state}')
            finished = state in ['complete', 'error', 'canceled']
        else:
            log(f'Could not read details of TF request {tf_request.uuid}')

    # this is to silence the linter, this cannot happen as the former loop cannot
    # finish without knowing request details
    if not tf_request.details:
        raise Exception(f"Failed to read details of TF request {tf_request.uuid}")
    result = tf_request.details['result']['overall'] if tf_request.details['result'] else 'error'
    log(f'finished with result: {result}')
    # now write execution details once more
    execute_job.execution.artifacts_url = tf_request.details['run']['artifacts']
    execute_job.execution.state = state
    execute_job.execution.result = result
    ctx.save_execute_job('execute-', execute_job)


@main.command(name='report')
@click.pass_obj
def cmd_report(ctx: CLIContext) -> None:
    ctx.enter_command('report')

    # initialize RP connection
    rp_project = ctx.settings.rp_project
    rp_url = ctx.settings.rp_url
    rp = ReportPortal(url=rp_url,
                      token=ctx.settings.rp_token,
                      project=rp_project)
    # initialize Jira connection
    jira_connection = initialize_jira_connection(ctx)
    # initialize ET connection
    if ctx.settings.et_enable_comments:
        et_url = ctx.settings.et_url
        if not et_url:
            raise Exception('Errata Tool URL is not configured!')
        et = ErrataTool(url=et_url)

    # process each stored execute file
    # before actual reporting split jobs per jira id
    jira_execute_job_mapping = {}
    # load all jobs at first as we would be rewriting them later
    for execute_job in ctx.load_execute_jobs('execute-'):
        jira_id = execute_job.jira.id
        if jira_id not in jira_execute_job_mapping:
            jira_execute_job_mapping[jira_id] = [execute_job]
        else:
            jira_execute_job_mapping[jira_id].append(execute_job)

    # now for each jira id finish the respective launch and report results
    for jira_id, execute_jobs in jira_execute_job_mapping.items():
        all_tests_passed = True
        # get RP launch details
        launch_uuid = execute_jobs[0].request.reportportal.get(
            'launch_uuid', None)
        launch_url = execute_jobs[0].request.reportportal.get(
            'launch_url', None)
        if launch_uuid:
            # prepare description with individual results
            results: dict[str, dict[str, str]] = {}
            for job in execute_jobs:
                results[job.request.id] = {
                    'id': job.request.id,
                    'state': job.execution.state,
                    'result': job.execution.result,
                    'uuid': job.execution.request_uuid,
                    'url': job.execution.artifacts_url}
                if job.execution.result != TF_RESULT_PASSED:
                    all_tests_passed = False
            launch_description = execute_jobs[0].request.reportportal.get(
                'launch_description', '')
            if launch_description:
                launch_description += '<br><br>'
            if not jira_id.startswith(JIRA_NONE_ID):
                launch_description += f'{jira_id}: '
            launch_description += f'{len(execute_jobs)} request(s) in total:'
            jira_description = launch_description.replace('<br>', '\n')
            for req in sorted(results.keys(), key=lambda x: int(x.split('.')[-1])):
                # it would be nice to use hyperlinks in launch description however we
                # would hit description length limit. Therefore using plain text
                launch_description += "<br>{id}: {state}, {result}".format(**results[req])
                jira_description += "\n[{id}|{url}]: {state}, {result}".format(**results[req])
            # finish launch just in case it hasn't been finished already
            # and update description with more detailed results
            rp.finish_launch(launch_uuid)
            ctx.logger.info(f'Updating launch description, {launch_url}')
            rp.update_launch(launch_uuid, description=launch_description)
            # do not report to Jira if JIRA_NONE_ID was used
            if not jira_id.startswith(JIRA_NONE_ID):
                try:
                    jira_connection.add_comment(
                        jira_id,
                        (f"NEWA has imported test results to RP launch "
                         f"{launch_url}\n\n{jira_description}"),
                        visibility={
                            'type': 'group',
                            'value': execute_job.jira.group}
                        if execute_job.jira.group else None)
                    ctx.logger.info(
                        f'Jira issue {jira_id} was updated with a RP launch URL {launch_url}')
                except jira.JIRAError as e:
                    raise Exception(f"Unable to add a comment to issue {jira_id}!") from e
                # change Jira issue state if required
                if execute_job.jira.transition_passed and all_tests_passed:
                    issue_transition(jira_connection,
                                     execute_job.jira.transition_passed,
                                     jira_id)
                    ctx.logger.info(
                        f'Issue {jira_id} state changed to {execute_job.jira.transition_passed}')
                elif execute_job.jira.transition_processed:
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
                    et.add_comment(
                        execute_job.erratum.id,
                        'The New Errata Workflow Automation (NEWA) has finished test execution '
                        'for this advisory.\n'
                        f'{jira_id} - {issue_summary}\n'
                        f'{issue_url}\n'
                        f'{launch_url}')
                    ctx.logger.info(
                        f"Erratum {execute_job.erratum.id} was updated "
                        f"with a comment about {jira_id}")
