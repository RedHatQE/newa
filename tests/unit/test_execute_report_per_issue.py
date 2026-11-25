"""Tests for per-issue reporting from within `execute` when chained with `report`."""

from pathlib import Path
from typing import Any, Optional
from unittest import mock

import click
import pytest
from click.testing import CliRunner

from newa import Event, ExecuteJob, Execution, Issue, Request, RequestResult, ScheduleJob
from newa.cli import report_helpers
from newa.cli.commands.report_cmd import cmd_report
from newa.cli.execute_helpers import _execute_worker_pool, _finalize_rp_launches
from newa.cli.main import NewaGroup
from newa.cli.report_helpers import IssueReporter
from newa.cli.utils import get_chained_command_params


def _schedule_job(jira_id: str, request_id: str) -> ScheduleJob:
    return ScheduleJob(
        event=Event(type_='erratum', id='RHSA-2024:12345'),
        erratum=None,
        compose=None,
        jira=Issue(id=jira_id),
        request=Request(id=request_id, arch='x86_64', tmt={'plan': '/plan1'}),
        )


def _execute_job(jira_id: str, request_id: str) -> ExecuteJob:
    return ExecuteJob(
        event=Event(type_='erratum', id='RHSA-2024:12345'),
        erratum=None,
        compose=None,
        jira=Issue(id=jira_id),
        request=Request(id=request_id, arch='x86_64', tmt={'plan': '/plan1'}),
        execution=Execution(batch_id='b1', state='complete', result='passed'),
        )


# --- chain detection ---------------------------------------------------------

def _run_chain(args: list[str]) -> Optional[dict[str, Any]]:
    seen: dict[str, Any] = {}

    @click.group(cls=NewaGroup, chain=True)
    def cli() -> None:
        pass

    @cli.command(name='execute')
    @click.option('--no-wait', is_flag=True)
    def execute(no_wait: bool) -> None:
        seen['report'] = get_chained_command_params('report', after='execute')

    @cli.command(name='report')
    @click.option('--progress', is_flag=True)
    def report(progress: bool) -> None:
        pass

    result = CliRunner().invoke(cli, args, catch_exceptions=False)
    assert result.exit_code == 0, result.output
    return seen.get('report')


def test_chain_report_after_execute() -> None:
    assert _run_chain(['execute', 'report']) == {'progress': False}


def test_chain_report_progress_after_execute() -> None:
    assert _run_chain(['execute', '--no-wait', 'report', '--progress']) == {'progress': True}


def test_chain_without_report() -> None:
    assert _run_chain(['execute']) is None


def test_chain_report_before_execute_is_ignored() -> None:
    assert _run_chain(['report', 'execute']) is None


# --- worker pool completion signals -----------------------------------------

class _FakeResults:
    """Mimics IMapIterator: an item may raise, iteration continues afterwards."""

    def __init__(self, items: list[Any]) -> None:
        self.items = list(items)
        # number of results handed out so far
        self.consumed = 0

    def __iter__(self) -> '_FakeResults':
        return self

    def __next__(self) -> Any:
        if not self.items:
            raise StopIteration
        item = self.items.pop(0)
        self.consumed += 1
        if isinstance(item, Exception):
            raise item
        return item


def _run_pool(jobs: list[ScheduleJob],
              order: list[Any],
              on_issue_finished: Any,
              results: Optional[_FakeResults] = None) -> None:
    ctx = mock.MagicMock()
    ctx.get_schedule_job_filepath.side_effect = lambda job: Path(
        f'/state/schedule-{job.jira.id}-{job.request.id}.yaml')
    pool = mock.MagicMock()
    pool.imap_unordered.return_value = results or _FakeResults(order)
    with mock.patch('multiprocessing.Pool', return_value=pool), \
            mock.patch('time.sleep'):
        _execute_worker_pool(ctx, jobs, 0, on_issue_finished=on_issue_finished)


def test_issue_reported_as_soon_as_its_requests_finish() -> None:
    jobs = [_schedule_job('A', 'REQ-1.1'), _schedule_job('A', 'REQ-1.2'),
            _schedule_job('B', 'REQ-2.1')]
    order = [Path('/state/schedule-A-REQ-1.1.yaml'),
             Path('/state/schedule-B-REQ-2.1.yaml'),
             Path('/state/schedule-A-REQ-1.2.yaml')]

    results = _FakeResults(order)
    # (jira_id, number of finished requests seen by the pool at callback time)
    calls: list[tuple[str, int]] = []

    def on_finished(jira_id: str) -> None:
        calls.append((jira_id, results.consumed))

    _run_pool(jobs, order, on_finished, results=results)
    # B is reported right after its only request finishes, i.e. while A's
    # second request is still running, not once all requests have finished
    assert calls == [('B', 2), ('A', 3)]


def test_worker_error_reraised_and_other_issues_still_reported() -> None:
    jobs = [_schedule_job('A', 'REQ-1.1'), _schedule_job('B', 'REQ-2.1')]
    calls: list[str] = []
    order = [RuntimeError('boom'), Path('/state/schedule-B-REQ-2.1.yaml')]

    with pytest.raises(RuntimeError, match='boom'):
        _run_pool(jobs, order, calls.append)
    # A had a failed request so it is never reported, B is
    assert calls == ['B']


def test_pool_without_callback() -> None:
    jobs = [_schedule_job('A', 'REQ-1.1')]
    _run_pool(jobs, [Path('/state/schedule-A-REQ-1.1.yaml')], None)


# --- IssueReporter -----------------------------------------------------------

def _reporter_ctx(tmp_path: Path) -> mock.MagicMock:
    ctx = mock.MagicMock()
    ctx.reported_jira_ids = set()
    ctx.get_schedule_job_filepath.side_effect = lambda job: (
        tmp_path / f'schedule-{job.jira.id}-{job.request.id}.yaml')
    ctx.load_execute_job.side_effect = ExecuteJob.from_yaml_file
    return ctx


def test_issue_reporter_reports_executed_and_pending(tmp_path: Path) -> None:
    ctx = _reporter_ctx(tmp_path)
    schedule_jobs = [_schedule_job('A', 'REQ-1.1'), _schedule_job('A', 'REQ-1.2'),
                     _schedule_job('B', 'REQ-2.1')]
    # only REQ-1.1 has been executed, REQ-1.2 is pending (e.g. --arch filter)
    _execute_job('A', 'REQ-1.1').to_yaml_file(tmp_path / 'execute-A-REQ-1.1.yaml')
    rp = mock.MagicMock()

    with mock.patch.object(report_helpers, '_update_all_tf_request_statuses') as upd, \
            mock.patch.object(report_helpers, '_report_jira_id') as rep, \
            mock.patch.object(report_helpers, '_get_rp_launch_details',
                              return_value=('launch-1', 'url')):
        reporter = IssueReporter(ctx, schedule_jobs, rp, None, None)
        reporter('A')

    upd.assert_called_once()
    rep.assert_called_once()
    _, jira_id, execute_jobs, jira_schedule_jobs = rep.call_args.args[:4]
    assert jira_id == 'A'
    assert [j.request.id for j in execute_jobs] == ['REQ-1.1']
    assert [j.request.id for j in jira_schedule_jobs] == ['REQ-1.1', 'REQ-1.2']
    assert ctx.reported_jira_ids == {'A'}
    assert reporter.finalized_launches == {'launch-1'}


def test_issue_reporter_failure_leaves_issue_for_report(tmp_path: Path) -> None:
    ctx = _reporter_ctx(tmp_path)
    execute_file = tmp_path / 'execute-A-REQ-1.1.yaml'
    # persist saved execute jobs for real, so premature marking would be visible
    ctx.save_execute_job.side_effect = lambda job: job.to_yaml_file(execute_file)
    schedule_jobs = [_schedule_job('A', 'REQ-1.1')]
    _execute_job('A', 'REQ-1.1').to_yaml_file(execute_file)

    # fail inside the real _report_jira_id, which is where jobs get marked reported
    with mock.patch.object(report_helpers, '_update_all_tf_request_statuses'), \
            mock.patch.object(report_helpers, '_process_jira_id_reports',
                              side_effect=RuntimeError('jira down')):
        reporter = IssueReporter(ctx, schedule_jobs, mock.MagicMock(), None, None)
        reporter('A')  # must not raise

    assert ctx.reported_jira_ids == set()
    assert reporter.finalized_launches == set()
    ctx.logger.warning.assert_called_once()
    # the request must stay unreported so that the following `report` handles it
    ctx.save_execute_job.assert_not_called()
    assert ExecuteJob.from_yaml_file(execute_file).execution.reported is False


def test_finalize_rp_launches_skips_finalized() -> None:
    ctx = mock.MagicMock()
    ctx.no_wait = False
    ctx.load_execute_jobs.return_value = []
    rp = mock.MagicMock()
    _finalize_rp_launches(ctx, rp, ['l1', 'l2'], skip_launches={'l1'})
    rp.finish_launch.assert_called_once_with('l2', logger=ctx.logger)


# --- report command skips already reported issues ---------------------------

def test_report_skips_issues_reported_during_execute() -> None:
    ctx = mock.MagicMock()
    ctx.reported_jira_ids = {'A'}
    ctx.load_execute_jobs.return_value = [_execute_job('A', 'REQ-1.1')]

    with mock.patch('newa.cli.commands.report_cmd.initialize_state_dir'):
        result = CliRunner().invoke(cmd_report, [], obj=ctx, catch_exceptions=False)

    assert result.exit_code == 0
    # nothing left to report, so no connection is initialized
    ctx.get_jira_connection.assert_not_called()


def test_report_reports_remaining_issues() -> None:
    ctx = mock.MagicMock()
    ctx.reported_jira_ids = {'A'}
    ctx.settings.rp_url = ''
    ctx.settings.et_enable_comments = False
    ctx.settings.rog_enable_comments = False
    ctx.load_execute_jobs.return_value = [_execute_job('A', 'REQ-1.1'),
                                          _execute_job('B', 'REQ-2.1')]
    ctx.load_schedule_jobs.return_value = []

    with mock.patch('newa.cli.commands.report_cmd.initialize_state_dir'), \
            mock.patch('newa.cli.commands.report_cmd._update_all_tf_request_statuses'), \
            mock.patch('newa.cli.commands.report_cmd._report_jira_id') as rep:
        result = CliRunner().invoke(cmd_report, [], obj=ctx, catch_exceptions=False)

    assert result.exit_code == 0
    assert [c.args[1] for c in rep.call_args_list] == ['B']


def test_command_scope_switches_log_label(tmp_path: Path) -> None:
    import io
    import logging

    from newa import CLIContext, Settings

    stream = io.StringIO()
    logger = logging.getLogger('test_command_scope')
    logger.handlers = [logging.StreamHandler(stream)]
    logger.setLevel(logging.INFO)
    logger.propagate = False
    ctx = CLIContext(logger=logger, settings=Settings(), state_dirpath=tmp_path)

    ctx.enter_command('execute')
    logger.info('before')
    with ctx.command_scope('report'):
        logger.info('during')
    logger.info('after')

    lines = stream.getvalue().splitlines()
    assert '[execute ] before' in lines[0]
    assert '[report  ] during' in lines[1]
    assert '[execute ] after' in lines[2]
    assert ctx.current_command == 'execute'


# --- persisted "reported" flag -----------------------------------------------

def test_issue_reporter_skips_issue_reported_by_previous_run(tmp_path: Path) -> None:
    ctx = _reporter_ctx(tmp_path)
    schedule_jobs = [_schedule_job('A', 'REQ-1.1')]
    job = _execute_job('A', 'REQ-1.1')
    job.execution.reported = True
    job.to_yaml_file(tmp_path / 'execute-A-REQ-1.1.yaml')

    with mock.patch.object(report_helpers, '_update_all_tf_request_statuses'), \
            mock.patch.object(report_helpers, '_report_jira_id') as rep, \
            mock.patch.object(report_helpers, '_get_rp_launch_details',
                              return_value=('launch-1', 'url')):
        reporter = IssueReporter(ctx, schedule_jobs, mock.MagicMock(), None, None)
        reporter('A')

    rep.assert_not_called()
    # the following `report` skips it too and its launch is not finished again
    assert ctx.reported_jira_ids == {'A'}
    assert reporter.finalized_launches == {'launch-1'}


def test_issue_reporter_reports_when_a_request_was_restarted(tmp_path: Path) -> None:
    ctx = _reporter_ctx(tmp_path)
    schedule_jobs = [_schedule_job('A', 'REQ-1.1'), _schedule_job('A', 'REQ-1.2')]
    reused = _execute_job('A', 'REQ-1.1')
    reused.execution.reported = True
    reused.to_yaml_file(tmp_path / 'execute-A-REQ-1.1.yaml')
    # restarted request has a fresh execution, i.e. not reported
    _execute_job('A', 'REQ-1.2').to_yaml_file(tmp_path / 'execute-A-REQ-1.2.yaml')

    with mock.patch.object(report_helpers, '_update_all_tf_request_statuses'), \
            mock.patch.object(report_helpers, '_report_jira_id') as rep:
        IssueReporter(ctx, schedule_jobs, None, None, None)('A')

    rep.assert_called_once()
    assert ctx.reported_jira_ids == {'A'}


@pytest.mark.parametrize('progress_mode', [False, True])
def test_report_jira_id_marks_finished_jobs_reported(progress_mode: bool) -> None:
    ctx = mock.MagicMock()
    finished = _execute_job('A', 'REQ-1.1')
    running = _execute_job('A', 'REQ-1.2')
    running.execution.result = RequestResult.NONE

    with mock.patch.object(report_helpers, '_process_jira_id_reports'):
        report_helpers._report_jira_id(
            ctx, 'A', [finished, running], [], None, None, None, None, 1000,
            progress_mode=progress_mode)

    if progress_mode:
        assert not finished.execution.reported
        ctx.save_execute_job.assert_not_called()
    else:
        assert finished.execution.reported
        ctx.save_execute_job.assert_called_once_with(finished)
    # a request without a final result is never marked as reported
    assert not running.execution.reported


def test_execution_reported_defaults_to_false_for_old_files(tmp_path: Path) -> None:
    job = _execute_job('A', 'REQ-1.1')
    path = tmp_path / 'execute-A-REQ-1.1.yaml'
    path.write_text(''.join(line for line in job.to_yaml().splitlines(keepends=True)
                            if 'reported:' not in line))
    assert 'reported' not in path.read_text()
    assert ExecuteJob.from_yaml_file(path).execution.reported is False
