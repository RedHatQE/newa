import itertools
import logging
import multiprocessing
import os.path
import time
from collections.abc import Iterable, Iterator
from functools import partial
from pathlib import Path
from typing import Any

import click
from attrs import define

from . import (
    ErrataTool,
    ErratumConfig,
    ErratumJob,
    Event,
    EventType,
    ExecuteJob,
    Execution,
    InitialErratum,
    Issue,
    JiraJob,
    RawRecipeConfigDimension,
    Recipe,
    RecipeConfig,
    ScheduleJob,
    Settings,
    eval_test,
    render_template,
    )

logging.basicConfig(
    format='%(asctime)s %(message)s',
    datefmt='%m/%d/%Y %I:%M:%S %p',
    level=logging.INFO)


@define
class CLIContext:
    """ State information about one Newa pipeline invocation """

    logger: logging.Logger
    settings: Settings

    # Path to directory with state files
    state_dirpath: Path

    def enter_command(self, command: str) -> None:
        self.logger.handlers[0].formatter = logging.Formatter(
            f'[%(asctime)s] [{command.ljust(8, " ")}] %(message)s',
            )

    def load_initial_erratum(self, filepath: Path) -> InitialErratum:
        erratum = InitialErratum.from_yaml_file(filepath)

        self.logger.info(f'Discovered initial erratum {erratum.event.id} in {filepath}')

        return erratum

    def load_initial_errata(self, filename_prefix: str) -> Iterator[InitialErratum]:
        for child in self.state_dirpath.iterdir():
            if not child.name.startswith(filename_prefix):
                continue

            yield self.load_initial_erratum(self.state_dirpath / child)

    def load_erratum_job(self, filepath: Path) -> ErratumJob:
        job = ErratumJob.from_yaml_file(filepath)

        self.logger.info(f'Discovered erratum job {job.id} in {filepath}')

        return job

    def load_erratum_jobs(self, filename_prefix: str) -> Iterator[ErratumJob]:
        for child in self.state_dirpath.iterdir():
            if not child.name.startswith(filename_prefix):
                continue

            yield self.load_erratum_job(self.state_dirpath / child)

    def load_jira_job(self, filepath: Path) -> JiraJob:
        job = JiraJob.from_yaml_file(filepath)

        self.logger.info(f'Discovered jira job {job.id} in {filepath}')

        return job

    def load_jira_jobs(self, filename_prefix: str) -> Iterator[JiraJob]:
        for child in self.state_dirpath.iterdir():
            if not child.name.startswith(filename_prefix):
                continue

            yield self.load_jira_job(self.state_dirpath / child)

    def load_schedule_job(self, filepath: Path) -> ScheduleJob:
        job = ScheduleJob.from_yaml_file(filepath)

        self.logger.info(f'Discovered schedule job {job.id} in {filepath}')

        return job

    def load_schedule_jobs(self, filename_prefix: str) -> Iterator[ScheduleJob]:
        for child in self.state_dirpath.iterdir():
            if not child.name.startswith(filename_prefix):
                continue

            yield self.load_schedule_job(self.state_dirpath / child)

    def save_erratum_job(self, filename_prefix: str, job: ErratumJob) -> None:
        filepath = self.state_dirpath / \
            f'{filename_prefix}{job.event.id}-{job.erratum.release}.yaml'

        job.to_yaml_file(filepath)
        self.logger.info(f'Erratum job {job.id} written to {filepath}')

    def save_erratum_jobs(self, filename_prefix: str, jobs: Iterable[ErratumJob]) -> None:
        for job in jobs:
            self.save_erratum_job(filename_prefix, job)

    def save_jira_job(self, filename_prefix: str, job: JiraJob) -> None:
        filepath = self.state_dirpath / \
            f'{filename_prefix}{job.event.id}-{job.erratum.release}-{job.jira.id}.yaml'

        job.to_yaml_file(filepath)
        self.logger.info(f'Jira job {job.id} written to {filepath}')

    def save_schedule_job(self, filename_prefix: str, job: ScheduleJob) -> None:
        filepath = self.state_dirpath / \
            f'{filename_prefix}{job.event.id}-{job.erratum.release}-{job.jira.id}-{job.request.id}.yaml'

        job.to_yaml_file(filepath)
        self.logger.info(f'Schedule job {job.id} written to {filepath}')

    def save_execute_job(self, filename_prefix: str, job: ExecuteJob) -> None:
        filepath = self.state_dirpath / \
            f'{filename_prefix}{job.event.id}-{job.erratum.release}-{job.jira.id}-{job.request.id}.yaml'

        job.to_yaml_file(filepath)
        self.logger.info(f'Execute job {job.id} written to {filepath}')


@click.group(chain=True)
@click.option(
    '--state-dir',
    default='$PWD/state',
    )
@click.option(
    '--conf-file',
    default='$HOME/.newa',
    )
@click.pass_context
def main(click_context: click.Context, state_dir: str, conf_file: str) -> None:
    ctx = CLIContext(
        settings=Settings.load(Path(os.path.expandvars(conf_file))),
        logger=logging.getLogger(),
        state_dirpath=Path(os.path.expandvars(state_dir)),
        )
    click_context.obj = ctx

    if not ctx.state_dirpath.exists():
        ctx.logger.info(f'State directory {ctx.state_dirpath} does not exist, creating...')
        ctx.state_dirpath.mkdir(parents=True)


@main.command(name='event')
@click.option(
    '-e', '--erratum', 'errata_ids',
    multiple=True,
    )
@click.pass_obj
def cmd_event(ctx: CLIContext, errata_ids: list[str]) -> None:
    ctx.enter_command('event')

    # Errata IDs were not given, try to load them from init- files.
    if not errata_ids:
        errata_ids = [e.event.id for e in ctx.load_initial_errata('init-')]

    # Abort if there are still no errata IDs.
    if not errata_ids:
        raise Exception('Missing errata IDs!')

    et_url = ctx.settings.et_url
    if not et_url:
        raise Exception('Errata Tool URL is not configured!')

    for erratum_id in errata_ids:
        event = Event(type_=EventType.ERRATUM, id=erratum_id)

        errata = ErrataTool(url=et_url).get_errata(event)

        for erratum in errata:
            erratum_job = ErratumJob(event=event, erratum=erratum)

            ctx.save_erratum_job('event-', erratum_job)


@main.command(name='jira')
@click.option(
    '--issue-config',
    default='component-config.yaml.sample',
    )
@click.pass_obj
def cmd_jira(ctx: CLIContext, issue_config: str) -> None:
    ctx.enter_command('jira')

    # this is here temporarily so we generate fake Jira issue IDs
    jira_id_gen = itertools.count(start=1)

    for erratum_job in ctx.load_erratum_jobs('event-'):
        # read Jira issue configuration
        config = ErratumConfig.from_yaml_file(Path(os.path.expandvars(issue_config)))

        # TODO: record created/existing issues. Instead of `Any`, maybe something
        # from the Jira library would be stored. Or just a Jira ticket ID.
        known_issues: dict[str, Any] = {}

        # Iterate over issue actions. Take one, if it's not possible to finish it,
        # put it back at the end of the queue.
        issue_actions = config.issues[:]

        while issue_actions:
            action = issue_actions.pop(0)

            print(f'* Processing create a {action.type.name} issue:')
            print(f'     summary: {action.summary}')
            print(f'     summary: {action.description}')
            print()

            if action.when:
                print(f'     Checking issue config condition: {action.when}')
                test_result = eval_test(
                    action.when,
                    JOB=erratum_job,
                    EVENT=erratum_job.event,
                    ERRATUM=erratum_job.erratum)
                if test_result:
                    print('       OK, proceeding...')
                else:
                    print('       FAILED, skipping...')
                    continue

            if action.id in known_issues:
                raise Exception(f'Issue "{action.id}" is already created!')

            if action.parent_id and action.parent_id not in known_issues:
                print(f'     !! Parent issue, "{action.parent_id}", is unknown, will try later')
                print()

                issue_actions.append(action)
                continue

            print(f'     Issue would be assigned to {action.assignee}.')
            print(f'       rendered: >>{render_template(action.assignee, ERRATUM=erratum_job)}<<')
            print(f'     Will remember the issue as `{action.id}`.')
            if action.parent_id:
                print(f'     Issue would have issue `{action.parent_id}` as its parent.')
            print()

            known_issues[action.id] = True

            # create a fake Issue object for now
            issue = Issue(id=f'NEWA-{next(jira_id_gen)}')

            if action.job_recipe:
                print(
                    f'* Would kick automated job for issue {action.type.name}'
                    f'based on recipe from {action.job_recipe}:')
                print()

                jira_job = JiraJob(event=erratum_job.event,
                                   erratum=erratum_job.erratum,
                                   jira=issue,
                                   recipe=Recipe(url=action.job_recipe))
                ctx.save_jira_job('jira-', jira_job)


@main.command(name='schedule')
@click.pass_obj
def cmd_schedule(ctx: CLIContext) -> None:
    ctx.enter_command('schedule')

    for jira_job in ctx.load_jira_jobs('jira-'):
        # prepare parameters based on the recipe from recipe.url
        # generate all relevant test request using the recipe data
        # prepare a list of Request objects

        # identify compose to be used
        # just a dump conversion for now
        compose = jira_job.erratum.release.rstrip('.GA') + '-Nightly'
        initial_config = RawRecipeConfigDimension(compose=compose)

        config = RecipeConfig.from_yaml_url(jira_job.recipe.url)
        # build requests
        requests = list(config.build_requests(initial_config))
        ctx.logger.info(f'{len(requests)} requests have been generated')

        # create few fake Issue objects for now
        for request in requests:
            schedule_job = ScheduleJob(
                event=jira_job.event,
                erratum=jira_job.erratum,
                jira=jira_job.jira,
                recipe=jira_job.recipe,
                request=request)
            ctx.save_schedule_job('schedule-', schedule_job)


@main.command(name='execute')
@click.option(
    '--workers',
    default=4,
    )
@click.pass_obj
def cmd_execute(ctx: CLIContext, workers: int) -> None:
    ctx.enter_command('execute')

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
    tf_request = schedule_job.request.initiate_tf_request()
    # tf_request = TFRequest(
    #    api='https://api.dev.testing-farm.io/v0.1/requests/519f5c01-46b6-47c9-a055-aecaa32e6a20',
    #    uuid='519f5c01-46b6-47c9-a055-aecaa32e6a20')
    log(f'TF request filed with uuid {tf_request.uuid}')
    finished = False
    delay = int(ctx.settings.tf_recheck_delay)
    while not finished:
        time.sleep(delay)
        tf_request.fetch_details()
        state = tf_request.details['state']
        log(f'TF reqest {tf_request.uuid} state: {state}')
        finished = state in ['complete', 'error']
    execute_job = ExecuteJob(
        event=schedule_job.event,
        erratum=schedule_job.erratum,
        jira=schedule_job.jira,
        recipe=schedule_job.recipe,
        request=schedule_job.request,
        execution=Execution(return_code=0, artifacts_url=tf_request.details['run']['artifacts']),
        )
    execute_job.to_yaml_file(
        schedule_file.parent /
        schedule_file.name.replace(
            'schedule-',
            'execute-'))
    log(f'finished with result: {tf_request.details["result"]["overall"]}')


@main.command(name='report')
@click.pass_obj
def cmd_report(ctx: CLIContext) -> None:
    ctx.enter_command('report')

    for _ in ctx.load_erratum_jobs('execute-'):
        pass
        # read yaml details
        # update Jira issue with job result
