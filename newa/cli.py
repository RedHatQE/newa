import logging
import os.path
from collections.abc import Iterable, Iterator
from pathlib import Path

import click
from attrs import define

from . import Erratum, ErratumJob, Event, EventType

logging.basicConfig(
    format='%(asctime)s %(message)s',
    datefmt='%m/%d/%Y %I:%M:%S %p',
    level=logging.INFO)


@define
class CLIContext:
    """ State information about one Newa pipeline invocation """

    logger: logging.Logger

    # Path to directory with state files
    state_dirpath: Path

    def enter_command(self, command: str) -> None:
        self.logger.handlers[0].formatter = logging.Formatter(
            f'[%(asctime)s] [{command.ljust(8, " ")}] %(message)s',
            )

    def load_erratum_job(self, filepath: Path) -> ErratumJob:
        job = ErratumJob.from_yaml_file(filepath)

        self.logger.info(f'Discovered erratum job {job.id} in {filepath}')

        return job

    def load_erratum_jobs(self, filename_prefix: str) -> Iterator[ErratumJob]:
        for child in self.state_dirpath.iterdir():
            if not child.name.startswith(filename_prefix):
                continue

            yield self.load_erratum_job(self.state_dirpath / child)

    def save_erratum_job(self, filename_prefix: str, job: ErratumJob) -> None:
        assert len(job.erratum.releases) == 1

        filepath = self.state_dirpath / \
            f'{filename_prefix}{job.event.id}-{job.erratum.releases[0]}.yaml'

        job.to_yaml_file(filepath)
        self.logger.info(f'Erratum job {job.id} written to {filepath}')

    def save_erratum_jobs(self, filename_prefix: str, jobs: Iterable[ErratumJob]) -> None:
        for job in jobs:
            self.save_erratum_job(filename_prefix, job)


@click.group(chain=True)
@click.option(
    '--state-dir',
    default='$PWD/state',
    )
@click.pass_context
def main(click_context: click.Context, state_dir: str) -> None:
    ctx = CLIContext(
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
    required=True,
    )
@click.pass_obj
def cmd_event(ctx: CLIContext, errata_ids: tuple[str, ...]) -> None:
    ctx.enter_command('event')

    erratum_jobs: list[ErratumJob] = []

    for erratum_id in errata_ids:
        event = Event(type_=EventType.ERRATUM, id=erratum_id)
        job = ErratumJob(event=event, erratum=Erratum())

        # TODO: job.erratum.fetch_details()
        # TODO: populate releases
        job.erratum.releases += ['RHEL-8.10.0', 'RHEL-9.4.0']

        for release in job.erratum.releases:
            job_erratum = job.erratum.clone()
            job_erratum.releases = [release]

            erratum_jobs.append(ErratumJob(event=event, erratum=job_erratum))

    ctx.save_erratum_jobs('event-', erratum_jobs)


@main.command(name='jira')
@click.pass_obj
def cmd_jira(ctx: CLIContext) -> None:
    ctx.enter_command('jira')

    for erratum_job in ctx.load_erratum_jobs('event-'):
        # read Jira issue configuration
        # get list of matching actions

        # for action in actions:
        # create epic
        # or create task
        # or create sutask
        # if subtask assoc. with recipes
        # clone object with yaml

        # erratum_job.issue = ...
        # what's recipe? doesn't it belong to "schedule"?
        # recipe = new JobRecipe(url)

        ctx.save_erratum_job('jira-', erratum_job)


@main.command(name='schedule')
@click.pass_obj
def cmd_schedule(ctx: CLIContext) -> None:
    ctx.enter_command('schedule')

    for erratum_job in ctx.load_erratum_jobs('jira-'):
        # prepare  parameters based on errata details (environment variables)
        # generate all relevant test jobs using the recipe
        # prepares a list of JobExec objects

        ctx.save_erratum_job('schedule-', erratum_job)


@main.command(name='execute')
@click.pass_obj
def cmd_execute(ctx: CLIContext) -> None:
    ctx.enter_command('execute')

    for erratum_job in ctx.load_erratum_jobs('schedule-'):
        # worker = new Executor(yaml)
        # run() returns result object
        # result = worker.run()

        ctx.save_erratum_job('execute-', erratum_job)


@main.command(name='report')
@click.pass_obj
def cmd_report(ctx: CLIContext) -> None:
    ctx.enter_command('report')

    for _ in ctx.load_erratum_jobs('execute-'):
        pass
        # read yaml details
        # update Jira issue with job result
