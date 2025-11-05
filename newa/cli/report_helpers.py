"""Helper functions for the Report command."""

import os
import urllib.parse
from typing import Any, Optional

import jira

from newa import (
    TF_REQUEST_FINISHED_STATES,
    CLIContext,
    ErrataTool,
    ErratumCommentTrigger,
    ExecuteHow,
    ExecuteJob,
    ReportPortal,
    RequestResult,
    TFRequest,
    )
from newa.cli.constants import JIRA_NONE_ID
from newa.cli.initialization import issue_transition
from newa.utils.helpers import short_sleep


# prepares a fancy summary for Jira or ReportPortal
def execute_jobs_summary(ctx: CLIContext,
                         jira_id: str,
                         execute_jobs: list[ExecuteJob],
                         target: str = 'Jira',
                         max_length: int = 0) -> list[str]:
    """
    Prepares a string with a summary of executed jobs.
    Parameter 'target' could be either 'Jira' or 'ReportPortal'.
    """
    separator = '<br>' if target == 'ReportPortal' else '\n'
    # an actual list we are going to return
    summaries = []
    summary = ''
    msg_next_comment = 'TBC in the next comment...'
    magic_number = len(separator + msg_next_comment)
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
            'result': str(job.execution.result.value),
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
        if target == 'ReportPortal':
            new_line = "{id}: {state}, {result}".format(**results[req])
        else:
            new_line = (
                "| [{id}|{url}] | {state} | {result} | {plan} | {suite_desc} |".format(
                    **results[req])
                )
        if max_length > 0 and len(
                summary +
                separator +
                new_line) > (
                max_length -
                magic_number):
            summaries.append(summary + separator + msg_next_comment)
            summary = ''
        summary += separator + new_line
    summaries.append(summary)

    return summaries


def _update_tf_request_status(
        ctx: CLIContext,
        execute_job: ExecuteJob) -> None:
    """Check and update Testing Farm request status if not yet finished."""
    if (execute_job.execution.result == RequestResult.NONE and
            execute_job.request.how == ExecuteHow.TESTING_FARM):
        tf_request = TFRequest(
            api=execute_job.execution.request_api,
            uuid=execute_job.execution.request_uuid)
        tf_request.fetch_details()

        if not tf_request.details:
            raise Exception(f"Failed to read details of TF request {tf_request.uuid}")

        state = tf_request.details['state']
        envs = ','.join([f"{e['os']['compose']}/{e['arch']}"
                         for e in tf_request.details['environments_requested']])
        ctx.logger.info(f'TF request {tf_request.uuid} envs: {envs} state: {state}')

        # Update result if available
        if tf_request.details['result']:
            execute_job.execution.result = RequestResult(
                tf_request.details['result']['overall'])
            ctx.logger.info(f'finished with result: {execute_job.execution.result}')
        elif tf_request.is_finished():
            execute_job.execution.result = RequestResult.ERROR

        # Update state
        if tf_request.details['state']:
            execute_job.execution.state = tf_request.details['state']

        # Update artifacts URL if available
        if tf_request.details['run'] and tf_request.details['run'].get('artifacts', None):
            execute_job.execution.artifacts_url = tf_request.details['run']['artifacts']

        ctx.save_execute_job(execute_job)


def _update_all_tf_request_statuses(
        ctx: CLIContext,
        execute_jobs: list[ExecuteJob]) -> None:
    """Update TF request status for all execute jobs that need it."""
    for execute_job in execute_jobs:
        _update_tf_request_status(ctx, execute_job)


def _get_rp_launch_details(
        execute_jobs: list[ExecuteJob]) -> tuple[Optional[str], Optional[str]]:
    """Extract ReportPortal launch UUID and URL from execute jobs."""
    launch_uuid = None
    launch_url = None

    if execute_jobs[0].request.reportportal:
        launch_uuid = execute_jobs[0].request.reportportal.get('launch_uuid', None)
        launch_url = execute_jobs[0].request.reportportal.get('launch_url', None)

    return launch_uuid, launch_url


def _check_test_status(
        execute_jobs: list[ExecuteJob]) -> tuple[bool, bool]:
    """Check if all tests have passed and finished."""
    all_tests_passed = True
    all_tests_finished = True

    for job in execute_jobs:
        if job.execution.result != RequestResult.PASSED:
            all_tests_passed = False
        if job.execution.state not in TF_REQUEST_FINISHED_STATES:
            all_tests_finished = False

    return all_tests_passed, all_tests_finished


def _finalize_rp_launch(
        ctx: CLIContext,
        rp: ReportPortal,
        launch_uuid: str,
        launch_url: Optional[str],
        launch_description: str) -> None:
    """Finalize ReportPortal launch by finishing and updating description."""
    rp.finish_launch(launch_uuid)
    ctx.logger.info(f'Updating launch description, {launch_url}')
    rp.update_launch(launch_uuid, description=launch_description)


def _build_jira_comment(
        launch_uuid: Optional[str],
        launch_url: Optional[str],
        jira_description: str,
        footer: str = '',
        first_comment: bool = True) -> str:
    """Build Jira comment text with optional RP launch details."""
    if launch_uuid:
        if first_comment:
            comment = ("NEWA has finished test execution and imported test results "
                       f"to RP launch\n{launch_url}\n\n{jira_description}")
        else:
            comment = f"Continuation of the previous comment...\n\n{jira_description}"
    else:
        comment = f"NEWA has finished test execution\n\n{jira_description}"

    if footer and first_comment:
        comment += f'\n{footer}'

    return comment


def _add_jira_comment_for_report(
        ctx: CLIContext,
        jira_connection: Any,
        jira_id: str,
        execute_job: ExecuteJob,
        comment: str) -> None:
    """Add comment to Jira issue with test execution results."""
    try:
        jira_connection.add_comment(
            jira_id,
            comment,
            visibility={
                'type': 'group',
                'value': execute_job.jira.group}
            if execute_job.jira.group else None)
        ctx.logger.debug(f'Jira issue {jira_id} was updated with test results')
    except jira.JIRAError as e:
        raise Exception(f"Unable to add a comment to issue {jira_id}!") from e


def _transition_jira_issue_based_on_results(
        ctx: CLIContext,
        jira_connection: Any,
        jira_id: str,
        execute_job: ExecuteJob,
        all_tests_passed: bool,
        all_tests_finished: bool) -> None:
    """Transition Jira issue based on test results."""
    if execute_job.jira.transition_passed and all_tests_passed:
        issue_transition(
            jira_connection,
            execute_job.jira.transition_passed,
            jira_id)
        ctx.logger.info(
            f'Issue {jira_id} state changed to {execute_job.jira.transition_passed}')
    elif execute_job.jira.transition_processed and all_tests_finished:
        issue_transition(
            jira_connection,
            execute_job.jira.transition_processed,
            jira_id)
        ctx.logger.info(
            f'Issue {jira_id} state changed to {execute_job.jira.transition_processed}')


def _add_erratum_comment_for_report(
        ctx: CLIContext,
        et: ErrataTool,
        jira_connection: Any,
        jira_id: str,
        execute_job: ExecuteJob,
        launch_url: Optional[str]) -> None:
    """Add comment to Errata Tool about test execution completion."""
    if (ctx.settings.et_enable_comments and
            ErratumCommentTrigger.REPORT in execute_job.jira.erratum_comment_triggers and
            execute_job.erratum):
        issue_summary = jira_connection.issue(jira_id).fields.summary
        issue_url = urllib.parse.urljoin(ctx.settings.jira_url, f"/browse/{jira_id}")

        comment = ('The New Errata Workflow Automation (NEWA) has finished '
                   'test execution for this advisory.\n'
                   f'{jira_id} - {issue_summary}\n'
                   f'{issue_url}\n')
        if launch_url:
            comment += f'{launch_url}\n'
        et.add_comment(execute_job.erratum.id, comment)
        ctx.logger.info(
            f"Erratum {execute_job.erratum.id} was updated "
            f"with a comment about {jira_id}")


def _process_jira_id_reports(
        ctx: CLIContext,
        jira_id: str,
        execute_jobs: list[ExecuteJob],
        rp: Optional[ReportPortal],
        jira_connection: Any,
        et: Optional[ErrataTool]) -> None:
    """Process reporting for a single Jira ID."""
    # Get RP launch details
    launch_uuid, launch_url = _get_rp_launch_details(execute_jobs)

    # Check for empty launch
    if launch_uuid and rp:
        rp.check_for_empty_launch(launch_uuid, logger=ctx.logger)

    # Check test status
    all_tests_passed, all_tests_finished = _check_test_status(execute_jobs)

    # save comment footer if configured
    footer = os.environ.get('NEWA_COMMENT_FOOTER', '').strip()

    # Generate summaries
    jira_descriptions = execute_jobs_summary(
        ctx,
        jira_id,
        execute_jobs,
        target='Jira',
        max_length=65000 - len(footer))
    launch_description = execute_jobs_summary(ctx, jira_id, execute_jobs, target='ReportPortal')[0]

    # Finalize RP launch if needed
    if launch_uuid and rp:
        _finalize_rp_launch(ctx, rp, launch_uuid, launch_url, launch_description)

    # Report to Jira (skip if JIRA_NONE_ID)
    if not jira_id.startswith(JIRA_NONE_ID):
        # Add Jira comments - more might be needed if we have exceeded comment length limit
        for jira_description in jira_descriptions:
            comment = _build_jira_comment(
                launch_uuid,
                launch_url,
                jira_description,
                footer,
                first_comment=jira_description == jira_descriptions[0])
            _add_jira_comment_for_report(
                ctx, jira_connection, jira_id, execute_jobs[0], comment)
            short_sleep()

        # Transition Jira issue if needed
        _transition_jira_issue_based_on_results(
            ctx, jira_connection, jira_id, execute_jobs[0],
            all_tests_passed, all_tests_finished)

        # Add Errata Tool comment if needed
        if et:
            _add_erratum_comment_for_report(
                ctx, et, jira_connection, jira_id, execute_jobs[0], launch_url)
