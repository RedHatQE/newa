"""Tests for shell quoting of the testing-farm and tmt commands."""

import json
import shlex
import subprocess
import sys
from types import SimpleNamespace

import pytest

from newa import Request, redact_secrets

PLAN_REGEX = '/plans/httpd/(tier1|tier2|tier3|untiered)'
TRICKY = """it's a "tricky" value; $(touch /tmp/pwned) `id` & | < > * ? [x] \\ end"""
DUMMY_TOKEN = 'dummy-rp-token'


def _ctx(rp_token: str = DUMMY_TOKEN) -> SimpleNamespace:
    return SimpleNamespace(
        timestamp='1234567890',
        settings=SimpleNamespace(
            rp_token=rp_token,
            rp_url='https://reportportal.example.com',
            rp_project='dummy_project',
            rp_test_param_filter='',
            ),
        )


def _request(**kwargs) -> Request:
    defaults = {
        'id': 'REQ-1.1',
        'compose': 'RHEL-9.6.0-Nightly',
        'tmt': {'url': 'https://example.com/tests.git', 'ref': 'main', 'plan': PLAN_REGEX},
        }
    defaults.update(kwargs)
    return Request(**defaults)


def _shell_argv(command: list[str]) -> list[str]:
    """Return the argv a real shell produces for the joined command."""
    return _shell_split(' '.join(command[1:]))


def _shell_split(joined: str) -> list[str]:
    """Return the arguments a real shell produces for a command line without the program."""
    # prepend a program that dumps its arguments
    dumper = f'{shlex.quote(sys.executable)} -c "import json,sys; print(json.dumps(sys.argv[1:]))"'
    out = subprocess.run(
        f'{dumper} {joined}', shell=True, check=True, capture_output=True, text=True,
        ).stdout
    return json.loads(out)


def _option_values(argv: list[str], option: str) -> list[str]:
    return [argv[i + 1] for i, a in enumerate(argv[:-1]) if a == option]


def _assignments(argv: list[str], *options: str) -> dict[str, str]:
    """Parse KEY=VALUE options the way testing-farm and tmt do (shlex.split)."""
    result = {}
    for option in options:
        for raw in _option_values(argv, option):
            for item in shlex.split(raw):
                k, v = item.split('=', 1)
                result[k] = v
    return result


class TestTFCommand:
    def test_plan_regex_is_single_argument(self):
        command, _ = _request().generate_tf_exec_command(_ctx())
        argv = _shell_argv(command)
        assert _option_values(argv, '--plan') == [PLAN_REGEX]

    def test_tmt_values_are_passed_verbatim(self):
        tmt = {
            'url': 'https://example.com/tests.git',
            'ref': 'feature/x y',
            'path': 'sub dir',
            'plan': PLAN_REGEX,
            'plan_filter': "tag:tier1 & enabled:true & summary:'x'",
            }
        command, _ = _request(tmt=tmt, compose='RHEL-9.6.0 (custom)').generate_tf_exec_command(
            _ctx())
        argv = _shell_argv(command)
        assert _option_values(argv, '--git-url') == [tmt['url']]
        assert _option_values(argv, '--git-ref') == [tmt['ref']]
        assert _option_values(argv, '--path') == [tmt['path']]
        assert _option_values(argv, '--plan-filter') == [tmt['plan_filter']]
        assert _option_values(argv, '--compose') == ['RHEL-9.6.0 (custom)']

    def test_context_and_environment_values_unchanged(self):
        request = _request(
            context={'distro': 'rhel-9.6', 'title': TRICKY},
            environment={'EVENT_TITLE': TRICKY, 'EMPTY': '', 'SIMPLE': 'yes'},
            )
        command, _ = request.generate_tf_exec_command(_ctx())
        argv = _shell_argv(command)
        context = _assignments(argv, '-c', '--context')
        assert context['newa_req'] == 'REQ-1.1'
        assert context['distro'] == 'rhel-9.6'
        assert context['title'] == TRICKY
        assert 'newa_batch' in context
        assert _assignments(argv, '-e') == {'EVENT_TITLE': TRICKY, 'EMPTY': '', 'SIMPLE': 'yes'}

    def test_simple_values_match_previous_behaviour(self):
        # previously '-c' 'k="v"' was produced; for values without special
        # characters both forms must yield the same TF argument
        request = _request(context={'distro': 'rhel-9.6'}, environment={'FOO': 'bar baz'})
        command, _ = request.generate_tf_exec_command(_ctx())
        argv = _shell_argv(command)
        old_c = shlex.split(''.join(shlex.split("""'distro="rhel-9.6"'""")))
        old_e = shlex.split(''.join(shlex.split("""'FOO="bar baz"'""")))
        assert [shlex.split(v) for v in _option_values(argv, '-c')][-1] == old_c
        assert [shlex.split(v) for v in _option_values(argv, '-e')] == [old_e]

    def test_cli_args_stay_raw_shell_fragment(self):
        cli_args = ("--tmt-environment TMT_POLICY_NAME=rhel-ci "
                    "--redhat-brew-build 12345 --tmt-report '--how reportportal'")
        command, _ = _request(testingfarm={'cli_args': cli_args}).generate_tf_exec_command(_ctx())
        argv = _shell_argv(command)
        assert _option_values(argv, '--redhat-brew-build') == ['12345']
        assert _option_values(argv, '--tmt-report') == ['--how reportportal']
        assert 'TMT_POLICY_NAME=rhel-ci' in _option_values(argv, '--tmt-environment')

    def test_reportportal_tmt_environment(self):
        request = _request(
            reportportal={'launch_name': "httpd's launch", 'launch_uuid': 'abc-123'},
            description='Errata "RHSA-2024:1234" for httpd',
            )
        command, _ = request.generate_tf_exec_command(_ctx())
        argv = _shell_argv(command)
        assert _assignments(argv, '--tmt-environment') == {
            'TMT_PLUGIN_REPORT_REPORTPORTAL_TOKEN': DUMMY_TOKEN,
            'TMT_PLUGIN_REPORT_REPORTPORTAL_URL': 'https://reportportal.example.com',
            'TMT_PLUGIN_REPORT_REPORTPORTAL_PROJECT': 'dummy_project',
            'TMT_PLUGIN_REPORT_REPORTPORTAL_UPLOAD_TO_LAUNCH': 'abc-123',
            'TMT_PLUGIN_REPORT_REPORTPORTAL_LAUNCH': "httpd's launch",
            'TMT_PLUGIN_REPORT_REPORTPORTAL_SUITE_PER_PLAN': '1',
            'TMT_PLUGIN_REPORT_REPORTPORTAL_LAUNCH_DESCRIPTION':
                'Errata "RHSA-2024:1234" for httpd',
            }
        assert _assignments(argv, '--context')['newa_report_rp'] == '1'

    def test_reportportal_launch_uuid_unset_kept_as_none(self):
        # matches the previous behaviour, launch_uuid is rendered as 'None'
        request = _request(reportportal={'launch_name': 'launch'})
        command, _ = request.generate_tf_exec_command(_ctx())
        env = _assignments(_shell_argv(command), '--tmt-environment')
        assert env['TMT_PLUGIN_REPORT_REPORTPORTAL_UPLOAD_TO_LAUNCH'] == 'None'

    def test_token_with_quote_is_redacted_from_logged_command(self):
        # the token is shell-quoted twice in the command, which changes how
        # a ' in it is written; redact_secrets() must still hide all of it
        token = "abc'def"
        ctx = _ctx(rp_token=token)
        ctx.settings.tf_token = ''
        command, _ = _request(reportportal={'launch_name': 'launch'}).generate_tf_exec_command(ctx)
        args = ' '.join(command[1:])
        assert token not in args
        redacted = redact_secrets(args, ctx)
        assert 'abc' not in redacted
        assert 'def' not in redacted
        env = _assignments(_shell_split(redacted), '--tmt-environment')
        assert env['TMT_PLUGIN_REPORT_REPORTPORTAL_TOKEN'] == '***'


class TestTmtCommand:
    def test_reproducer_quoting(self):
        tmt = {
            'url': 'https://example.com/tests.git',
            'plan': PLAN_REGEX,
            'plan_filter': "tag:tier1 & summary:'x'",
            }
        request = _request(
            tmt=tmt,
            context={'title': TRICKY},
            environment={'EVENT_TITLE': TRICKY, 'TMT_FOO': 'passed via env'},
            how='tmt',
            )
        command, environment = request.generate_tmt_exec_command(_ctx())
        argv = _shell_argv(command)
        assert _option_values(argv, '--name') == [PLAN_REGEX]
        assert _option_values(argv, '--filter') == [tmt['plan_filter']]
        context = _assignments(argv, '-c')
        assert context['title'] == TRICKY
        assert context['newa_req'] == 'REQ-1.1'
        assert _assignments(argv, '-e') == {'EVENT_TITLE': TRICKY}
        assert environment == {'TMT_FOO': 'passed via env'}

    @pytest.mark.parametrize('cli_args', [None, 'discover -h fmf provision -h container'])
    def test_reproducer_cli_args_raw(self, cli_args):
        tmt = {'url': 'https://example.com/tests.git', 'plan': PLAN_REGEX}
        if cli_args:
            tmt['cli_args'] = cli_args
        command, _ = _request(tmt=tmt, how='tmt').generate_tmt_exec_command(_ctx())
        argv = _shell_argv(command)
        tail = argv[argv.index('--name') + 2:]
        if cli_args:
            assert tail == shlex.split(cli_args)
        else:
            assert tail == ['discover', 'provision', 'prepare', 'execute', 'report', 'finish']
