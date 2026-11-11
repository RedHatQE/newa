"""Tests for report rendering of scheduled-but-not-executed (pending) requests."""

from unittest import mock

from newa import Event, ExecuteJob, Execution, Issue, Request, ScheduleJob
from newa.cli.constants import JIRA_NONE_ID
from newa.cli.report_helpers import _check_test_status, execute_jobs_summary


def _execute_job(request_id: str, arch: str, result: str, state: str) -> ExecuteJob:
    return ExecuteJob(
        event=Event(type_='erratum', id='RHSA-2024:12345'),
        erratum=None,
        compose=None,
        jira=Issue(id=JIRA_NONE_ID),
        request=Request(id=request_id, arch=arch, tmt={'plan': '/plan1'}),
        execution=Execution(
            batch_id='b1',
            state=state,
            result=result,
            request_uuid=f'uuid-{request_id}',
            request_api=f'https://tf.example.com/{request_id}',
            artifacts_url=f'https://artifacts.example.com/{request_id}'),
        )


def _schedule_job(request_id: str, arch: str) -> ScheduleJob:
    return ScheduleJob(
        event=Event(type_='erratum', id='RHSA-2024:12345'),
        erratum=None,
        compose=None,
        jira=Issue(id=JIRA_NONE_ID),
        request=Request(id=request_id, arch=arch, tmt={'plan': '/plan1'}),
        )


def test_summary_includes_pending_rows():
    ctx = mock.MagicMock()
    executed = [_execute_job('1.1', 'x86_64', 'passed', 'complete')]
    pending = [_schedule_job('1.2', 'aarch64')]

    summary = execute_jobs_summary(
        ctx, JIRA_NONE_ID, executed, target='Jira', pending_jobs=pending)[0]

    # both the executed and the pending request appear
    assert '1.1' in summary
    assert '1.2' in summary
    # total count reflects all scheduled requests, not just executed ones
    assert '2 request(s) in total' in summary
    # the pending request is marked as not executed
    assert 'not executed' in summary
    # executed request keeps its hyperlink, pending one does not
    assert '[1.1|https://artifacts.example.com/1.1]' in summary
    assert '[1.2|' not in summary


def test_summary_without_pending_is_unchanged():
    ctx = mock.MagicMock()
    executed = [_execute_job('1.1', 'x86_64', 'passed', 'complete')]

    summary = execute_jobs_summary(ctx, JIRA_NONE_ID, executed, target='Jira')[0]

    assert '1 request(s) in total' in summary
    assert 'not executed' not in summary


def test_check_test_status_pending_blocks_finished():
    executed = [_execute_job('1.1', 'x86_64', 'passed', 'complete')]
    pending = [_schedule_job('1.2', 'aarch64')]

    # without pending, a fully passed set is passed+finished
    assert _check_test_status(executed) == (True, True)
    # with pending requests, the set is neither passed nor finished
    assert _check_test_status(executed, pending) == (False, False)
