"""
Tests for parsing testing-farm CLI output.

The CLI's exit code is not inspected; the outcome is decided from its output
alone, so the relevant tests run with both a zero and a non-zero exit code.
"""

import re
import subprocess
from contextlib import AbstractContextManager
from types import SimpleNamespace
from typing import Any
from unittest import mock

import pytest

from newa import Request, TFRequest, check_tf_cli_version
from newa.models import execution

EXIT_CODES = pytest.mark.parametrize('returncode', [0, 1])


def _ctx() -> SimpleNamespace:
    return SimpleNamespace(
        timestamp='1234567890',
        logger=mock.MagicMock(),
        settings=SimpleNamespace(
            rp_token='',
            tf_token='',
            rp_url='',
            rp_project='',
            rp_test_param_filter='',
            ),
        )


def _fake_subprocess(**run_kwargs: Any) -> AbstractContextManager[Any]:
    """Replace ``subprocess`` in ``newa.models.execution`` only, not globally."""
    fake = SimpleNamespace(
        run=mock.Mock(**run_kwargs),
        PIPE=subprocess.PIPE,
        STDOUT=subprocess.STDOUT,
        )
    return mock.patch.object(execution, 'subprocess', fake)


def _run(output: str, returncode: int = 1) -> AbstractContextManager[Any]:
    completed = subprocess.CompletedProcess(args='', returncode=returncode, stdout=output)
    return _fake_subprocess(return_value=completed)


def _request() -> Request:
    return Request(id='REQ-1.1', compose='RHEL-9.6.0-Nightly',
                   tmt={'url': 'https://example.com/tests.git'})


@EXIT_CODES
def test_initiate_tf_request_without_api_url_raises_with_output(returncode):
    with _run('⛔ API token is invalid', returncode), \
            pytest.raises(Exception, match='TF request failed:\n⛔ API token is invalid'):
        _request().initiate_tf_request(_ctx())


@EXIT_CODES
def test_initiate_tf_request_with_api_url(returncode):
    output = '📦 repository ...\n🔎 api https://api.testing-farm.io/v0.1/requests/abc-123\n'
    with _run(output, returncode):
        tf_request = _request().initiate_tf_request(_ctx())
    assert tf_request.api == 'https://api.testing-farm.io/v0.1/requests/abc-123'
    assert tf_request.uuid == 'abc-123'


@EXIT_CODES
@pytest.mark.parametrize(('output', 'expected'), [
    ('✅ cancellation requested', 'Cancellation of TF request abc requested.'),
    ('⛔ Request is already finished', 'TF request abc has been already finished.'),
    ('⛔ Request was already canceled', 'TF request abc has been already cancelled.'),
    ])
def test_cancel_status_from_output(output, expected, returncode):
    ctx = _ctx()
    with _run(output, returncode):
        TFRequest(api='https://api.example.com/abc', uuid='abc').cancel(ctx)
    ctx.logger.info.assert_called_once_with(expected)


def test_cancel_unknown_output_logs_error():
    ctx = _ctx()
    with _run('something went wrong'):
        TFRequest(api='https://api.example.com/abc', uuid='abc').cancel(ctx)
    ctx.logger.error.assert_called_once_with('Failed cancelling TF request abc.')
    ctx.logger.debug.assert_called_once_with('something went wrong')


def test_cancel_missing_cli_logs_error():
    ctx = _ctx()
    error = FileNotFoundError(2, 'No such file or directory', 'testing-farm')
    with _fake_subprocess(side_effect=error):
        TFRequest(api='https://api.example.com/abc', uuid='abc').cancel(ctx)
    ctx.logger.error.assert_called_once_with(
        "Failed cancelling TF request abc: [Errno 2] No such file or directory: 'testing-farm'")


def test_check_tf_cli_version_missing_cli():
    output = '/bin/sh: line 1: testing-farm: command not found'
    expected = f"Cannot get testing-farm CLI version, got '{output}'."
    with _run(output, returncode=127), pytest.raises(Exception, match=re.escape(expected)):
        check_tf_cli_version(_ctx())
