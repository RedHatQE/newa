"""Tests for hiding tokens in TF request errors and logged commands."""

import shlex
import subprocess
from types import SimpleNamespace
from unittest import mock

import pytest

from newa import Request, redact_secrets

RP_TOKEN = 'dummy-rp-token'
TF_TOKEN = 'dummy-tf-token'


def _ctx(rp_token: str = RP_TOKEN, tf_token: str = TF_TOKEN) -> SimpleNamespace:
    return SimpleNamespace(
        timestamp='1234567890',
        settings=SimpleNamespace(
            rp_token=rp_token,
            tf_token=tf_token,
            rp_url='https://reportportal.example.com',
            rp_project='dummy_project',
            rp_test_param_filter='',
            ),
        )


def test_redacts_both_tokens():
    text = f'TOKEN="{RP_TOKEN}" other={TF_TOKEN} again {RP_TOKEN}'
    assert redact_secrets(text, _ctx()) == 'TOKEN="***" other=*** again ***'


def test_longer_secret_containing_another_is_fully_redacted():
    ctx = _ctx(rp_token='abc', tf_token='xxabcyy')
    assert redact_secrets('tf=xxabcyy rp=abc', ctx) == 'tf=*** rp=***'


@pytest.mark.parametrize('quote', [
    lambda v: v,
    shlex.quote,
    lambda v: shlex.quote(shlex.quote(v)),
    ], ids=['raw', 'quoted-once', 'quoted-twice'])
def test_shell_quoted_secret_with_apostrophe_is_redacted(quote):
    token = "abc'def"
    text = 'testing-farm request --tmt-environment ' + quote(f'TOKEN={token}')
    redacted = redact_secrets(text, _ctx(rp_token=token))
    assert 'abc' not in redacted
    assert 'def' not in redacted
    assert '***' in redacted


@pytest.mark.parametrize('echoed', [
    "abc\\'d\\\"ef",
    "$'abc\\'d\"ef'",
    "abc'd\\\"ef",
    ], ids=['backslash-both', 'ansi-c', 'backslash-double'])
def test_backslash_escaped_secret_is_redacted(echoed):
    token = 'abc\'d"ef'
    redacted = redact_secrets(f'error near: TOKEN={echoed}', _ctx(rp_token=token))
    assert 'abc' not in redacted
    assert 'ef' not in redacted


def test_bash_printf_q_form_is_redacted():
    token = "abc'def"
    echoed = subprocess.run(['bash', '-c', 'printf %q "$1"', '_', token],
                            check=True, capture_output=True, text=True).stdout
    assert echoed != token
    assert 'abc' not in redact_secrets(echoed, _ctx(rp_token=token))


@pytest.mark.parametrize(('rp_token', 'tf_token'), [('', ''), ('', TF_TOKEN), (RP_TOKEN, '')])
def test_unset_tokens_leave_text_intact(rp_token, tf_token):
    text = 'testing-farm request --compose RHEL-9.6.0-Nightly'
    assert redact_secrets(text, _ctx(rp_token, tf_token)) == text


def test_tf_request_failure_does_not_leak_token():
    request = Request(
        id='REQ-1.1',
        compose='RHEL-9.6.0-Nightly',
        tmt={'url': 'https://example.com/tests.git'},
        reportportal={'launch_name': 'launch'},
        )
    # the shell echoes the failing command line on a syntax error
    output = ("/bin/sh: -c: line 1: syntax error near unexpected token `('\n"
              "/bin/sh: -c: line 1: `testing-farm request --tmt-environment "
              f"'TMT_PLUGIN_REPORT_REPORTPORTAL_TOKEN=\"{RP_TOKEN}\"' ...'\n")
    completed = subprocess.CompletedProcess(args='', returncode=2, stdout=output)
    with mock.patch('newa.models.execution.subprocess.run', return_value=completed), \
            pytest.raises(Exception, match='TF request failed') as excinfo:
        request.initiate_tf_request(_ctx())
    message = str(excinfo.value)
    assert RP_TOKEN not in message
    assert 'TMT_PLUGIN_REPORT_REPORTPORTAL_TOKEN="***"' in message
