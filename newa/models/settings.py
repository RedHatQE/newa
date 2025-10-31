"""Settings and CLI context models."""

import logging
import os
from collections.abc import Iterable, Iterator
from configparser import ConfigParser
from pathlib import Path
from re import Pattern
from typing import TYPE_CHECKING, Optional, TypeVar

try:
    from attrs import define, field
except ModuleNotFoundError:
    from attr import define, field

from newa.models.events import InitialErratum
from newa.models.execution import RequestResult
from newa.models.recipes import RecipeContext, RecipeEnvironment

if TYPE_CHECKING:
    from newa.models.jobs import ArtifactJob, ExecuteJob, JiraJob, ScheduleJob

# File prefixes for different job types
EVENT_FILE_PREFIX = 'event-'
INIT_FILE_PREFIX = 'init-'
JIRA_FILE_PREFIX = 'jira-'
SCHEDULE_FILE_PREFIX = 'schedule-'
EXECUTE_FILE_PREFIX = 'execute-'

STATEDIR_TOPDIR = Path('/var/tmp/newa')

SettingsT = TypeVar('SettingsT', bound='Settings')


@define
class Settings:  # type: ignore[no-untyped-def]
    """Class storing newa settings."""

    newa_statedir_topdir: Path = field(  # type: ignore[var-annotated]
        factory=Path, converter=lambda p: Path(p) if p else STATEDIR_TOPDIR)
    newa_clear_on_subcommand: bool = False
    et_url: str = ''
    et_enable_comments: bool = False
    rp_url: str = ''
    rp_token: str = ''
    rp_project: str = ''
    rp_test_param_filter: str = ''
    rp_launch_descr_chars_limit: str = ''
    jira_url: str = ''
    jira_token: str = ''
    jira_project: str = ''
    tf_token: str = ''
    tf_recheck_delay: str = ''
    rog_token: str = ''

    def get(self, key: str, default: str = '') -> str:
        return str(getattr(self, key, default))

    @classmethod
    def load(cls: type[SettingsT], config_file: Path) -> 'Settings':
        cp = ConfigParser()
        cp.read(config_file)

        def _get(
                cp: ConfigParser,
                path: str,
                envvar: str,
                default: Optional[str] = '') -> str:
            section, key = path.split('/', 1)
            # first attemp to read environment variable
            env = os.environ.get(envvar, None) if envvar else None
            # then attempt to use the value from config file, use fallback value otherwise
            return env if env else cp.get(section, key, fallback=str(default))

        def _str_to_bool(value: str) -> bool:
            return value.strip().lower() in ['1', 'true']

        return Settings(
            newa_statedir_topdir=_get(
                cp,
                'newa/statedir_topdir',
                'NEWA_STATEDIR_TOPDIR'),
            newa_clear_on_subcommand=_str_to_bool(
                _get(
                    cp,
                    'newa/clear_on_subcommand',
                    'NEWA_CLEAR_ON_SUBCOMMAND')),
            et_url=_get(
                cp,
                'erratatool/url',
                'NEWA_ET_URL'),
            et_enable_comments=_str_to_bool(
                _get(
                    cp,
                    'erratatool/enable_comments',
                    'NEWA_ET_ENABLE_COMMENTS')),
            rp_url=_get(
                cp,
                'reportportal/url',
                'NEWA_REPORTPORTAL_URL'),
            rp_token=_get(
                cp,
                'reportportal/token',
                'NEWA_REPORTPORTAL_TOKEN'),
            rp_project=_get(
                cp,
                'reportportal/project',
                'NEWA_REPORTPORTAL_PROJECT'),
            rp_test_param_filter=_get(
                cp,
                'reportportal/test_param_filter',
                'NEWA_REPORTPORTAL_TEST_PARAM_FILTER'),
            rp_launch_descr_chars_limit=_get(
                cp,
                'reportportal/launch_descr_chars_limit',
                'NEWA_REPORTPORTAL_LAUNCH_DESCR_CHARS_LIMIT'),
            jira_project=_get(
                cp,
                'jira/project',
                'NEWA_JIRA_PROJECT'),
            jira_url=_get(
                cp,
                'jira/url',
                'NEWA_JIRA_URL'),
            jira_token=_get(
                cp,
                'jira/token',
                'NEWA_JIRA_TOKEN'),
            tf_token=_get(
                cp,
                'testingfarm/token',
                'TESTING_FARM_API_TOKEN'),
            tf_recheck_delay=_get(
                cp,
                'testingfarm/recheck_delay',
                'NEWA_TF_RECHECK_DELAY',
                "60"),
            rog_token=_get(
                cp,
                'rog/token',
                'NEWA_ROG_TOKEN'),
            )


@define
class CLIContext:  # type: ignore[no-untyped-def]
    """State information about one Newa pipeline invocation."""

    logger: logging.Logger
    settings: Settings
    # Path to directory with state files
    state_dirpath: Path
    cli_environment: RecipeEnvironment = field(factory=dict)
    cli_context: RecipeContext = field(factory=dict)
    timestamp: str = ''
    continue_execution: bool = False
    no_wait: bool = False
    restart_request: list[str] = field(factory=list)
    restart_result: list[RequestResult] = field(factory=list,  # type: ignore[var-annotated]
                                                converter=lambda results: [
                                                    (r if isinstance(r, RequestResult)
                                                     else RequestResult(r))
                                                    for r in results])
    new_state_dir: bool = False
    prev_state_dirpath: Optional[Path] = None
    force: bool = False
    action_id_filter_pattern: Optional[Pattern[str]] = None

    def enter_command(self, command: str) -> None:
        self.logger.handlers[0].formatter = logging.Formatter(
            f'[%(asctime)s] [{command.ljust(8, " ")}] %(message)s',
            )

    def load_initial_erratum(self, filepath: Path) -> InitialErratum:
        erratum = InitialErratum.from_yaml_file(filepath)

        self.logger.info(f'Discovered initial erratum {erratum.event.short_id} in {filepath}')

        return erratum

    def load_initial_errata(
            self,
            filename_prefix: str = INIT_FILE_PREFIX) -> Iterator[InitialErratum]:
        for child in self.state_dirpath.iterdir():
            if not child.name.startswith(filename_prefix):
                continue

            yield self.load_initial_erratum(child.resolve())

    def load_artifact_job(self, filepath: Path) -> 'ArtifactJob':
        from newa.models.jobs import ArtifactJob
        job = ArtifactJob.from_yaml_file(filepath)

        self.logger.info(f'Discovered erratum job {job.id} in {filepath}')

        return job

    def load_artifact_jobs(
            self,
            filename_prefix: str = EVENT_FILE_PREFIX) -> Iterator['ArtifactJob']:
        for child in self.state_dirpath.iterdir():
            if not child.name.startswith(filename_prefix):
                continue

            yield self.load_artifact_job(child.resolve())

    def load_jira_job(self, filepath: Path) -> 'JiraJob':
        from newa.models.jobs import JiraJob
        job = JiraJob.from_yaml_file(filepath)

        self.logger.info(f'Discovered jira job {job.id} in {filepath}')

        return job

    def load_jira_jobs(
            self,
            filename_prefix: str = JIRA_FILE_PREFIX,
            filter_actions: bool = False) -> Iterator['JiraJob']:
        for child in self.state_dirpath.iterdir():
            if not child.name.startswith(filename_prefix):
                continue

            job = self.load_jira_job(child.resolve())
            if filter_actions and self.skip_action(job.jira.action_id):
                continue
            yield job

    def load_schedule_job(self, filepath: Path) -> 'ScheduleJob':
        from newa.models.jobs import ScheduleJob
        job = ScheduleJob.from_yaml_file(filepath)

        self.logger.info(f'Discovered schedule job {job.id} in {filepath}')

        return job

    def load_schedule_jobs(
            self,
            filename_prefix: str = SCHEDULE_FILE_PREFIX,
            filter_actions: bool = False) -> Iterator['ScheduleJob']:
        for child in self.state_dirpath.iterdir():
            if not child.name.startswith(filename_prefix):
                continue

            job = self.load_schedule_job(child.resolve())
            if filter_actions and self.skip_action(job.jira.action_id):
                continue
            yield job

    def load_execute_job(self, filepath: Path) -> 'ExecuteJob':
        from newa.models.jobs import ExecuteJob
        job = ExecuteJob.from_yaml_file(filepath)

        self.logger.info(f'Discovered execute job {job.id} in {filepath}')

        return job

    def load_execute_jobs(
            self,
            filename_prefix: str = EXECUTE_FILE_PREFIX,
            filter_actions: bool = False) -> Iterator['ExecuteJob']:
        for child in self.state_dirpath.iterdir():
            if not child.name.startswith(filename_prefix):
                continue

            job = self.load_execute_job(child.resolve())
            if filter_actions and self.skip_action(job.jira.action_id):
                continue
            yield job

    def get_artifact_job_filepath(
            self,
            job: 'ArtifactJob',
            filename_prefix: str = EVENT_FILE_PREFIX) -> Path:
        return self.state_dirpath / \
            f'{filename_prefix}{job.event.short_id}-{job.short_id}.yaml'

    def get_jira_job_filepath(self, job: 'JiraJob', filename_prefix: str = JIRA_FILE_PREFIX) -> Path:  # noqa: E501
        return self.state_dirpath / \
            f'{filename_prefix}{job.event.short_id}-{job.short_id}-{job.jira.id}.yaml'

    def get_schedule_job_filepath(
            self,
            job: 'ScheduleJob',
            filename_prefix: str = SCHEDULE_FILE_PREFIX) -> Path:
        return self.state_dirpath / \
            f'{filename_prefix}{job.event.short_id}-{job.short_id}-{job.jira.id}-{job.request.id}.yaml'

    def get_execute_job_filepath(
            self,
            job: 'ExecuteJob',
            filename_prefix: str = EXECUTE_FILE_PREFIX) -> Path:
        return self.state_dirpath / \
            f'{filename_prefix}{job.event.short_id}-{job.short_id}-{job.jira.id}-{job.request.id}.yaml'

    def save_artifact_job(
            self,
            job: 'ArtifactJob',
            filename_prefix: str = EVENT_FILE_PREFIX) -> None:
        filepath = self.get_artifact_job_filepath(job, filename_prefix)
        job.to_yaml_file(filepath)
        self.logger.info(f'Artifact job {job.id} written to {filepath}')

    def save_artifact_jobs(
            self,
            jobs: Iterable['ArtifactJob'],
            filename_prefix: str = EVENT_FILE_PREFIX) -> None:
        for job in jobs:
            self.save_artifact_job(job, filename_prefix)

    def save_jira_job(self, job: 'JiraJob', filename_prefix: str = JIRA_FILE_PREFIX) -> None:
        filepath = self.get_jira_job_filepath(job, filename_prefix)
        job.to_yaml_file(filepath)
        self.logger.info(f'Jira job {job.id} written to {filepath}')

    def save_schedule_job(
            self,
            job: 'ScheduleJob',
            filename_prefix: str = SCHEDULE_FILE_PREFIX) -> None:
        filepath = self.get_schedule_job_filepath(job, filename_prefix)
        job.to_yaml_file(filepath)
        self.logger.info(f'Schedule job {job.id} written to {filepath}')

    def save_execute_job(
            self,
            job: 'ExecuteJob',
            filename_prefix: str = EXECUTE_FILE_PREFIX) -> None:
        filepath = self.get_execute_job_filepath(job, filename_prefix)
        job.to_yaml_file(filepath)
        self.logger.info(f'Execute job {job.id} written to {filepath}')

    def remove_job_files(self, filename_prefix: str) -> None:
        for filepath in self.state_dirpath.glob(f'{filename_prefix}*'):
            self.logger.debug(f'Removing existing file {filepath}')
            filepath.unlink()

    def skip_action(self, action_id: Optional[str], log_message: bool = True) -> bool:
        # check if action_id matches filtered items
        if self.action_id_filter_pattern and not (
                action_id and self.action_id_filter_pattern.fullmatch(action_id)):
            if log_message:
                self.logger.info(
                    f"Skipping action {action_id} as it doesn't match "
                    "the --action-id-filter regular expression.")
            return True
        return False
