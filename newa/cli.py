import datetime
import logging
import multiprocessing
import os.path
import re
import time
from functools import partial
from pathlib import Path

import click
import jira

from . import (
    Arch,
    ArtifactJob,
    CLIContext,
    Compose,
    ErrataTool,
    ErratumContentType,
    Event,
    EventType,
    ExecuteJob,
    Execution,
    Issue,
    IssueConfig,
    IssueHandler,
    IssueType,
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
    eval_test,
    get_url_basename,
    render_template,
    )

logging.basicConfig(
    format='%(asctime)s %(message)s',
    datefmt='%m/%d/%Y %I:%M:%S %p',
    level=logging.INFO)


def default_state_dir() -> Path:
    """ Returns the first unused directory matching /var/tmp/newa/run-[0-9]+ """
    parent_dir = Path('/var/tmp/newa')
    pattern = '^run-([0-9]+)$'
    counter = 0
    try:
        obj = os.scandir(parent_dir)
    except FileNotFoundError:
        return parent_dir / f'run-{counter}'
    for entry in obj:
        r = re.match(pattern, entry.name)
        if entry.is_dir() and r:
            c = int(r.group(1))
            if c >= counter:
                counter = c + 1
    return parent_dir / f'run-{counter}'


@click.group(chain=True)
@click.option(
    '--state-dir',
    default=default_state_dir,
    )
@click.option(
    '--conf-file',
    default='$HOME/.newa',
    )
@click.option(
    '--debug',
    is_flag=True,
    default=False,
    )
@click.pass_context
def main(click_context: click.Context, state_dir: str, conf_file: str, debug: bool) -> None:
    ctx = CLIContext(
        settings=Settings.load(Path(os.path.expandvars(conf_file))),
        logger=logging.getLogger(),
        state_dirpath=Path(os.path.expandvars(state_dir)),
        )
    click_context.obj = ctx

    if debug:
        ctx.logger.setLevel(logging.DEBUG)
    ctx.logger.info(f'Using state directory {ctx.state_dirpath}')
    if not ctx.state_dirpath.exists():
        ctx.logger.debug(f'State directory {ctx.state_dirpath} does not exist, creating...')
        ctx.state_dirpath.mkdir(parents=True)


@main.command(name='event')
@click.option(
    '-e', '--erratum', 'errata_ids',
    default=[],
    multiple=True,
    )
@click.option(
    '-c', '--compose', 'compose_ids',
    default=[],
    multiple=True,
    )
@click.pass_obj
def cmd_event(ctx: CLIContext, errata_ids: list[str], compose_ids: list[str]) -> None:
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
                # identify compose to be used, just a dump conversion for now
                compose = erratum.release.rstrip('.GA') + '-Nightly'
                if erratum.content_type == ErratumContentType.RPM:
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
    default='component-config.yaml.sample',
    )
@click.pass_obj
def cmd_jira(ctx: CLIContext, issue_config: str) -> None:
    ctx.enter_command('jira')

    jira_url = ctx.settings.jira_url
    if not jira_url:
        raise Exception('Jira URL is not configured!')

    jira_token = ctx.settings.jira_token
    if not jira_token:
        raise Exception('Jira URL is not configured!')

    for artifact_job in ctx.load_artifact_jobs('event-'):

        # read Jira issue configuration
        config = IssueConfig.from_yaml_with_include(os.path.expandvars(issue_config))
        jira = IssueHandler(artifact_job, jira_url, jira_token, config.project, config.transitions)
        ctx.logger.info("Initialized Jira handler")

        # All issue action from the configuration.
        issue_actions = config.issues[:]

        # Processed action (action.id : issue).
        processed_actions: dict[str, Issue] = {}

        # Length of the queue the last time issue action was processed,
        # Use to prevent endless loop over the issue actions.
        endless_loop_check: dict[str, int] = {}

        # Iterate over issue actions. Take one, if it's not possible to finish it,
        # put it back at the end of the queue.
        while issue_actions:
            action = issue_actions.pop(0)

            ctx.logger.info(f"Processing {action.id}")

            if action.when and not eval_test(action.when,
                                             JOB=artifact_job,
                                             EVENT=artifact_job.event,
                                             ERRATUM=artifact_job.erratum,
                                             COMPOSE=artifact_job.compose):
                ctx.logger.info(f"Skipped, issue action is irrelevant ({action.when})")
                continue

            rendered_summary = render_template(
                action.summary,
                ERRATUM=artifact_job.erratum,
                COMPOSE=artifact_job.compose)
            rendered_description = render_template(
                action.description, ERRATUM=artifact_job.erratum, COMPOSE=artifact_job.compose)
            if action.assignee:
                rendered_assignee = render_template(
                    action.assignee,
                    ERRATUM=artifact_job.erratum,
                    COMPOSE=artifact_job.compose)
            else:
                rendered_assignee = None
            if action.newa_id:
                action.newa_id = render_template(
                    action.newa_id,
                    ERRATUM=artifact_job.erratum,
                    COMPOSE=artifact_job.compose)

            # Detect that action has parent available (if applicable), if we went trough the
            # actions already and parent was not found, we abort.
            if action.parent_id and action.parent_id not in processed_actions:
                queue_length = len(issue_actions)
                last_queue_length = endless_loop_check.get(action.id, 0)
                if last_queue_length == queue_length:
                    raise Exception(f"Parent {action.parent_id} for {action.id} not found!")

                endless_loop_check[action.id] = queue_length
                ctx.logger.info(f"Skipped for now (parent {action.parent_id} not yet found)")

                issue_actions.append(action)
                continue

            # Find existing issues related to artifact_job and action
            search_result = jira.get_open_issues(action, all_respins=True)

            # Issues related to the curent respin and previous one(s).
            new_issues: list[Issue] = []
            old_issues: list[Issue] = []
            for jira_issue_key, jira_issue in search_result.items():
                ctx.logger.info(f"Checking {jira_issue_key}")

                # In general, issue is new (relevant to the current respin) if it has newa_id
                # of this action in the description. Otherwise, it is old (relevant to the
                # previous respins).
                #
                # However, it might happen that we encounter subtask issue that is new but its
                # original parent task got dropped (by human mistake, newa would never do that).
                # By this time new parent task already exists. Unfortunately, Jira REST API does
                # not allow updating 'parent' field [1] and hence we cannot re-use the issue with
                # updated parent - we need to handle it as an old one (unless it has KEEP on_respin
                # action it will get dropped and new one is created with the proper parent).
                #
                # [1] https://jira.atlassian.com/browse/JRASERVER-68763
                is_new = False
                if jira.newa_id(action) in jira_issue["description"] \
                        and (action.type != IssueType.SUBTASK
                             or not action.parent_id
                             or processed_actions[action.parent_id].id == jira_issue["parent"]):
                    is_new = True

                if is_new:
                    new_issues.append(Issue(jira_issue_key))
                else:
                    old_issues.append(Issue(jira_issue_key))

            # Old issue(s) can be re-used for the current respin.
            if old_issues and action.on_respin == OnRespinAction.KEEP:
                new_issues.extend(old_issues)
                old_issues = []

            # Processing new issues.
            #
            # 1. Either there is no new issue (it does not exist yet - we need to create it).
            if not new_issues:
                parent = None
                if action.parent_id:
                    parent = processed_actions.get(action.parent_id, None)

                issue = jira.create_issue(action,
                                          rendered_summary,
                                          rendered_description,
                                          rendered_assignee,
                                          parent)

                processed_actions[action.id] = issue

                new_issues.append(issue)
                ctx.logger.info(f"New issue {issue.id} created")

            # Or there is exactly one new issue (already created or re-used old issue).
            elif len(new_issues) == 1:
                issue = new_issues[0]
                processed_actions[action.id] = issue

                # If the old issue was reused, re-fresh it.
                parent = processed_actions[action.parent_id] if action.parent_id else None
                jira.refresh_issue(action, issue)
                ctx.logger.info(f"Issue {issue} re-used")

            # But if there are more than one new issues we encountered error.
            else:
                raise Exception(f"More than one new {action.id} found ({new_issues})!")

            if action.job_recipe:
                jira_job = JiraJob(event=artifact_job.event,
                                   erratum=artifact_job.erratum,
                                   compose=artifact_job.compose,
                                   jira=issue,
                                   recipe=Recipe(url=action.job_recipe))
                ctx.save_jira_job('jira-', jira_job)

            # Processing old issues - we only expect old issues that are to be closed (if any).
            if old_issues:
                if action.on_respin != OnRespinAction.CLOSE:
                    raise Exception(f"Invalid respin action {action.on_respin} for {old_issues}!")
                for issue in old_issues:
                    jira.drop_obsoleted_issue(issue, obsoleted_by=processed_actions[action.id])
                    ctx.logger.info(f"Old issue {issue} closed")


@main.command(name='schedule')
@click.option(
    '--arch',
    default=None,
    )
@click.pass_obj
def cmd_schedule(ctx: CLIContext, arch: str) -> None:
    ctx.enter_command('schedule')

    for jira_job in ctx.load_jira_jobs('jira-'):
        # prepare parameters based on the recipe from recipe.url
        # generate all relevant test request using the recipe data
        # prepare a list of Request objects

        # would it be OK not to pass compose to TF? I guess so
        compose = jira_job.compose.id if jira_job.compose else None
        if arch:
            architectures = Arch.architectures(
                [Arch(a.strip()) for a in arch.split(',')])
        else:
            architectures = jira_job.erratum.archs if (
                jira_job.erratum and jira_job.erratum.archs) else Arch.architectures()
        initial_config = RawRecipeConfigDimension(compose=compose)

        config = RecipeConfig.from_yaml_url(jira_job.recipe.url)
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
        jinja_vars = {
            'ERRATUM': jira_job.erratum,
            }

        requests = list(config.build_requests(initial_config, jinja_vars))
        ctx.logger.info(f'{len(requests)} requests have been generated')

        # create ScheduleJob object for each request
        for request in requests:
            # before yaml export render all fields as Jinja templates
            for attr in ("reportportal", "tmt", "testingfarm", "environment", "context"):
                # getattr(request, attr) could also be None due to 'attr' being None
                mapping = getattr(request, attr, {}) or {}
                for (key, value) in mapping.items():
                    mapping[key] = render_template(
                        value,
                        ERRATUM=jira_job.erratum,
                        COMPOSE=jira_job.compose,
                        CONTEXT=request.context,
                        ENVIRONMENT=request.environment,
                        )

            # export schedule_job yaml
            schedule_job = ScheduleJob(
                event=jira_job.event,
                erratum=jira_job.erratum,
                compose=jira_job.compose,
                jira=jira_job.jira,
                recipe=jira_job.recipe,
                request=request)
            ctx.save_schedule_job('schedule-', schedule_job)


@main.command(name='execute')
@click.option(
    '--workers',
    default=8,
    )
@click.pass_obj
def cmd_execute(ctx: CLIContext, workers: int) -> None:
    ctx.enter_command('execute')

    # store timestamp of this execution
    ctx.timestamp = str(datetime.datetime.now(datetime.timezone.utc).timestamp())
    tf_token = ctx.settings.tf_token
    if not tf_token:
        raise ValueError("TESTING_FARM_API_TOKEN not set!")
    # make TESTING_FARM_API_TOKEN available to workers as envvar if it has been
    # defined only though the settings file
    os.environ["TESTING_FARM_API_TOKEN"] = tf_token

    # get a list of files to be scheduled so that they can be distributed across workers
    schedule_list = [
        (ctx, ctx.state_dirpath / child.name)
        for child in ctx.state_dirpath.iterdir()
        if child.name.startswith('schedule-')]

    worker_pool = multiprocessing.Pool(workers)
    for _ in worker_pool.starmap(worker, schedule_list):
        # small sleep to avoid race conditions inside tmt code
        time.sleep(0.1)

    print('Done')


def worker(ctx: CLIContext, schedule_file: Path) -> None:

    log = partial(print, schedule_file.name)

    log('processing request...')
    # read request details
    schedule_job = ScheduleJob.from_yaml_file(Path(schedule_file))

    log('initiating TF request')
    tf_request = schedule_job.request.initiate_tf_request(ctx)
    log(f'TF request filed with uuid {tf_request.uuid}')

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
                            batch_id=schedule_job.request.get_hash(ctx.timestamp)),
        )
    ctx.save_execute_job('execute-', execute_job)
    # wait for TF job to finish
    finished = False
    delay = int(ctx.settings.tf_recheck_delay)
    while not finished:
        time.sleep(delay)
        tf_request.fetch_details()
        state = tf_request.details['state']
        log(f'TF reqest {tf_request.uuid} state: {state}')
        finished = state in ['complete', 'error']

    log(f'finished with result: {tf_request.details["result"]["overall"]}')
    # now write execution details once more
    # FIXME: we pretend return_code to be 0
    execute_job.execution.artifacts_url = tf_request.details['run']['artifacts']
    execute_job.execution.return_code = 0
    ctx.save_execute_job('execute-', execute_job)


@main.command(name='report')
@click.option(
    '--rp-project',
    default='',
    )
@click.pass_obj
@click.option(
    '--rp-url',
    default='',
    )
def cmd_report(ctx: CLIContext, rp_project: str, rp_url: str) -> None:
    ctx.enter_command('report')

    jira_request_mapping: dict[str, dict[str, list[str]]] = {}
    jira_launch_mapping: dict[str, RawRecipeReportPortalConfigDimension] = {}
    if not rp_project:
        rp_project = ctx.settings.rp_project
    if not rp_url:
        rp_url = ctx.settings.rp_url
    rp = ReportPortal(url=rp_url,
                      token=ctx.settings.rp_token,
                      project=rp_project)
    # initialize Jira connection as well
    jira_url = ctx.settings.jira_url
    if not jira_url:
        raise Exception('Jira URL is not configured!')
    jira_token = ctx.settings.jira_token
    if not jira_token:
        raise Exception('Jira URL is not configured!')
    jira_connection = jira.JIRA(jira_url, token_auth=jira_token)

    # process each stored execute file
    for execute_job in ctx.load_execute_jobs('execute-'):
        jira_id = execute_job.jira.id
        request_id = execute_job.request.id
        # it is sufficient to process each Jira issue only once
        if jira_id not in jira_request_mapping:
            jira_request_mapping[jira_id] = {}
            jira_launch_mapping[jira_id] = RawRecipeReportPortalConfigDimension(
                launch_name=execute_job.request.reportportal['launch_name'],
                launch_description=execute_job.request.reportportal.get(
                    'launch_description', None))
            # jira_launch_mapping[jira_id] = execute_job.request.reportportal['launch_name']
        # for each Jira and request ID we build a list of RP launches
        jira_request_mapping[jira_id][request_id] = rp.find_launches_by_attr(
            'newa_batch', execute_job.execution.batch_id)

    # proceed with RP launch merge
    for jira_id in jira_request_mapping:
        launch_list = []
        # prepare launch description
        # start with description specified in the recipe file
        description = jira_launch_mapping[jira_id].get('launch_description', None)
        if description:
            description += '<br><br>'
        else:
            description = ''
        # add info about the number of recipies scheduled and completed
        description += f'{jira_id}: {len(jira_request_mapping[jira_id])} requests in total<br>'
        for request in sorted(jira_request_mapping[jira_id].keys()):
            if len(jira_request_mapping[jira_id][request]):
                description += f'  {request}: COMPLETED<br>'
                launch_list.extend(jira_request_mapping[jira_id][request])
            else:
                description += f'  {request}: MISSING<br>'
        # prepare launch name
        if jira_launch_mapping[jira_id]['launch_name']:
            name = str(jira_launch_mapping[jira_id]['launch_name'])
        else:
            # should not happen
            name = 'unspecified_newa_launch_name'
        if not len(launch_list):
            ctx.logger.error('Failed to find any related ReportPortal launches')
        else:
            if len(launch_list) > 1:
                merged_launch = rp.merge_launches(
                    launch_list, name, description, {})
                if not merged_launch:
                    ctx.logger.error('Failed to merge ReportPortal launches')
                else:
                    launch_list = [merged_launch]
            # report results back to Jira
            launch_urls = [rp.get_launch_url(str(launch)) for launch in launch_list]
            ctx.logger.info(f'RP launch urls: {" ".join(launch_urls)}')
            try:
                joined_urls = '\n'.join(launch_urls)
                description = description.replace('<br>', '\n')
                jira_connection.add_comment(
                    jira_id, f"NEWA has imported test results to\n{joined_urls}\n\n{description}")
                ctx.logger.info(
                    f'Jira issue {jira_id} was updated with a RP launch URL')
            except jira.JIRAError as e:
                raise Exception(f"Unable to add a comment to issue {jira_id}!") from e
