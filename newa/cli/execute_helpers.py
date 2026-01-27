"""Helper functions for the Execute command."""

import datetime
import os
import sys
import urllib.parse
from typing import Any, Optional, Union

import jira

from newa import (
    EXECUTE_FILE_PREFIX,
    CLIContext,
    ErrataTool,
    ErratumCommentTrigger,
    ExecuteJob,
    ReportPortal,
    RequestResult,
    ScheduleJob,
    )
from newa.cli.constants import JIRA_NONE_ID, RP_LAUNCH_DESCR_CHARS_LIMIT
from newa.cli.utils import test_file_presence


def sanitize_restart_result(ctx: CLIContext, results: list[str]) -> list[RequestResult]:
    """Validate and sanitize restart result values."""
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


def _validate_execute_parameters(
        ctx: CLIContext,
        _continue: bool,
        restart_request: list[str],
        restart_result: list[str]) -> None:
    """Validate and configure execute parameters."""
    ctx.continue_execution = _continue

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


def _check_execute_file_conflicts(ctx: CLIContext) -> None:
    """Check for execute file conflicts in state directory."""
    if (test_file_presence(ctx.state_dirpath, EXECUTE_FILE_PREFIX) and
            not ctx.continue_execution and
            not ctx.force):
        ctx.logger.error(
            f'"{EXECUTE_FILE_PREFIX}" files already exist in state-dir {ctx.state_dirpath}, '
            'use --force to override')
        sys.exit(1)


def _initialize_execute_environment(ctx: CLIContext) -> None:
    """Initialize environment variables and timestamp for execution."""
    ctx.timestamp = str(datetime.datetime.now(datetime.timezone.utc).timestamp())
    tf_token = ctx.settings.tf_token
    if not tf_token:
        raise ValueError("TESTING_FARM_API_TOKEN not set!")
    os.environ["TESTING_FARM_API_TOKEN"] = tf_token


def _create_jira_job_mapping(
        job_list: Union[list[ScheduleJob], list[ExecuteJob]],
        ) -> dict[str, Union[list[ScheduleJob], list[ExecuteJob]]]:
    """Group schedule or execute jobs by Jira ID."""
    jira_job_mapping: dict[str, Union[list[ScheduleJob], list[ExecuteJob]]] = {}
    for job in job_list:
        jira_id = job.jira.id
        if jira_id not in jira_job_mapping:
            jira_job_mapping[jira_id] = [job]
        else:
            jira_job_mapping[jira_id].append(job)  # type: ignore[arg-type]
    return jira_job_mapping


def _prepare_launch_description(
        jira_id: str,
        schedule_jobs: Union[list[ScheduleJob], list[ExecuteJob]],
        jira_url: str) -> str:
    """Prepare the launch description for ReportPortal."""
    launch_description: str = str(
        schedule_jobs[0].request.reportportal.get('launch_description', ''))
    if launch_description:
        launch_description += '<br><br>'

    if not jira_id.startswith(JIRA_NONE_ID):
        issue_url = urllib.parse.urljoin(jira_url, f"/browse/{jira_id}")
        launch_description += f'[{jira_id}]({issue_url}): '

    launch_description += f'{len(schedule_jobs)} request(s) in total'
    return launch_description


def _create_or_reuse_rp_launch(
        ctx: CLIContext,
        rp: ReportPortal,
        jira_id: str,
        schedule_jobs: list[ScheduleJob],
        launch_description: str) -> Optional[str]:
    """Create or reuse a ReportPortal launch for the given jira_id."""
    # Check if launch already exists
    existing_launch_uuid: Optional[str] = schedule_jobs[0].request.reportportal.get(
        'launch_uuid', None)
    if existing_launch_uuid:
        ctx.logger.debug(
            f'Skipping RP launch creation for {jira_id} as {existing_launch_uuid} already exists.')
        return str(existing_launch_uuid)

    # Create new launch
    launch_name: str = str(schedule_jobs[0].request.reportportal['launch_name']).strip()
    if not launch_name:
        raise Exception("RP launch name is not configured")

    launch_attrs: dict[str, Any] = schedule_jobs[0].request.reportportal.get(
        'launch_attributes', {})
    launch_attrs.update({'newa_statedir': str(ctx.state_dirpath)})

    # Store CLI --context definitions as launch attributes
    for (k, v) in ctx.cli_context.items():
        if k in launch_attrs:
            ctx.logger.debug(
                f'Not storing context {k} as launch attribute due to a collision')
        else:
            launch_attrs[k] = v

    # Add erratum context if available
    if schedule_jobs[0].erratum and 'erratum' not in launch_attrs:
        launch_attrs['erratum'] = str(schedule_jobs[0].erratum.id)

    # Create the launch
    new_launch_uuid: Optional[str] = rp.create_launch(
        launch_name, launch_description, attributes=launch_attrs)
    if not new_launch_uuid:
        raise Exception('Failed to create RP launch')

    ctx.logger.info(f'Created RP launch {new_launch_uuid} for issue {jira_id}')
    return str(new_launch_uuid)


def _update_schedule_jobs_with_launch(
        ctx: CLIContext,
        rp: ReportPortal,
        jira_id: str,
        schedule_jobs: list[ScheduleJob],
        launch_uuid: str,
        jira_schedule_job_mapping: dict[str, list[ScheduleJob]]) -> str:
    """Update schedule jobs with launch UUID and URL."""
    launch_url = rp.get_launch_url(launch_uuid)
    for job in jira_schedule_job_mapping[jira_id]:
        job.request.reportportal['launch_uuid'] = launch_uuid
        job.request.reportportal['launch_url'] = launch_url
        ctx.save_schedule_job(job)
    return launch_url


def _add_jira_comment_for_execution(
        ctx: CLIContext,
        jira_connection: Any,
        jira_id: str,
        job: ScheduleJob,
        launch_url: Optional[str]) -> None:
    """Add a comment to Jira issue about test execution."""
    if job.request.reportportal:
        comment = ("NEWA has scheduled automated test recipe for this issue, test "
                   f"results will be uploaded to ReportPortal launch\n{launch_url}")
    else:
        comment = "NEWA has scheduled automated test recipe for this issue"

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


def _add_erratum_comment_for_execution(
        ctx: CLIContext,
        et: ErrataTool,
        jira_connection: Any,
        job: ScheduleJob,
        jira_id: str,
        launch_url: str) -> None:
    """Add a comment to Errata Tool about test execution."""
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


def _process_rp_launches_and_jira_updates(
        ctx: CLIContext,
        rp: ReportPortal,
        et: Optional[ErrataTool],
        jira_schedule_job_mapping: dict[str, list[ScheduleJob]]) -> list[str]:
    """
    Process ReportPortal launches and update Jira issues.
    Returns list of created launch UUIDs.
    """
    launch_list: list[str] = []
    jira_url = ctx.settings.jira_url

    for jira_id, schedule_jobs in jira_schedule_job_mapping.items():
        job = schedule_jobs[0]

        # Handle ReportPortal launch creation
        if schedule_jobs[0].request.reportportal:
            launch_description = _prepare_launch_description(jira_id, schedule_jobs, jira_url)
            launch_uuid = _create_or_reuse_rp_launch(
                ctx, rp, jira_id, schedule_jobs, launch_description)

            if launch_uuid:
                launch_list.append(launch_uuid)
                # Update schedule jobs if launch was just created
                if launch_uuid != schedule_jobs[0].request.reportportal.get('launch_uuid', None):
                    launch_url = _update_schedule_jobs_with_launch(
                        ctx, rp, jira_id, schedule_jobs, launch_uuid, jira_schedule_job_mapping)
                else:
                    launch_url = rp.get_launch_url(launch_uuid)
            else:
                continue
        else:
            launch_url = None

        # Update Jira issue if not using execute --continue
        if not (jira_id.startswith(JIRA_NONE_ID) or ctx.continue_execution):
            jira_connection = ctx.get_jira_connection().get_connection()
            _add_jira_comment_for_execution(
                ctx, jira_connection, jira_id, job, launch_url)

            # Update Errata Tool if needed
            if et and launch_url:
                _add_erratum_comment_for_execution(
                    ctx, et, jira_connection, job, jira_id, launch_url)

    return launch_list


def _execute_worker_pool(
        ctx: CLIContext,
        schedule_job_list: list[ScheduleJob],
        workers: int) -> None:
    """Execute the worker pool for processing schedule jobs."""
    import multiprocessing
    import time

    from newa.cli.workers import worker

    schedule_list = [(ctx, ctx.get_schedule_job_filepath(job))
                     for job in schedule_job_list]
    worker_pool = multiprocessing.Pool(workers if workers > 0 else len(schedule_list))
    for _ in worker_pool.starmap(worker, schedule_list):
        # small sleep to avoid race conditions inside tmt code
        time.sleep(0.1)


def _finalize_rp_launches(
        ctx: CLIContext,
        rp: ReportPortal,
        launch_list: list[str]) -> None:
    """Finalize ReportPortal launches after execution."""
    from typing import cast

    # Group execute jobs by Jira ID
    jira_execute_job_mapping = cast(
        dict[str, list[ExecuteJob]],
        _create_jira_job_mapping(list(ctx.load_execute_jobs(filter_actions=True))))

    # Update launch descriptions for each Jira ID with TF request URLs if --no-wait
    if ctx.no_wait:
        for jira_id, execute_jobs in jira_execute_job_mapping.items():
            if not execute_jobs[0].request.reportportal:
                continue

            launch_uuid = execute_jobs[0].request.reportportal.get('launch_uuid', '')
            if not launch_uuid:
                continue

            # Prepare initial launch description
            launch_description = _prepare_launch_description(
                jira_id, execute_jobs, ctx.settings.jira_url)

            # Add TF request URLs for this Jira ID's execute jobs
            rp_chars_limit = (ctx.settings.rp_launch_descr_chars_limit or
                              RP_LAUNCH_DESCR_CHARS_LIMIT)
            rp_launch_descr_updated = launch_description + "\n"
            rp_launch_descr_dots = True

            for execute_job in execute_jobs:
                req_link = f"[{execute_job.request.id}]({execute_job.execution.request_api})\n"
                if len(req_link) + len(rp_launch_descr_updated) < int(rp_chars_limit):
                    rp_launch_descr_updated += req_link
                elif rp_launch_descr_dots:
                    rp_launch_descr_updated += "\n..."
                    rp_launch_descr_dots = False

            rp.update_launch(launch_uuid, description=rp_launch_descr_updated)

    ctx.logger.info('Finished execution')

    # Finish all RP launches if not using --no-wait
    if not ctx.no_wait:
        for uuid in launch_list:
            ctx.logger.info(f'Finishing launch {uuid}')
            rp.finish_launch(uuid)
            rp.check_for_empty_launch(uuid, logger=ctx.logger)
