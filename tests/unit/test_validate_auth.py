from unittest import mock

from click.testing import CliRunner

from newa.cli.commands import validate_auth_cmd


def _patch_tests(monkeypatch):
    """Replace every service test with a tracked stub returning success.

    Returns a dict mapping the config-section name to the stub so callers can
    assert which integrations were actually validated.
    """
    stubs = {}
    for section, _name, func in validate_auth_cmd.TESTS:
        stub = mock.MagicMock(return_value=(True, "ok", "http://example", "stub"))
        stubs[section] = stub
        monkeypatch.setattr(validate_auth_cmd, func.__name__, stub)
    # TESTS holds references to the original functions, so rebuild it to use
    # the stubs installed above.
    monkeypatch.setattr(
        validate_auth_cmd,
        'TESTS',
        [
            (section, name, stubs[section])
            for section, name, _func in validate_auth_cmd.TESTS
            ],
        )
    return stubs


def _run(args):
    runner = CliRunner()
    return runner.invoke(
        validate_auth_cmd.cmd_validate_auth, args, obj=mock.MagicMock())


def test_no_flags_checks_all(monkeypatch):
    stubs = _patch_tests(monkeypatch)
    result = _run([])
    assert result.exit_code == 0
    for section, stub in stubs.items():
        assert stub.called, f"{section} should have been checked"


def test_skip_selected_integrations(monkeypatch):
    stubs = _patch_tests(monkeypatch)
    result = _run(['--no-jira', '--no-rog'])
    assert result.exit_code == 0
    assert not stubs['jira'].called
    assert not stubs['rog'].called
    assert stubs['erratatool'].called
    assert stubs['reportportal'].called
    assert stubs['testingfarm'].called


def test_skip_all_integrations_errors(monkeypatch):
    stubs = _patch_tests(monkeypatch)
    result = _run([
        '--no-erratatool', '--no-jira', '--no-reportportal',
        '--no-testingfarm', '--no-rog',
        ])
    assert result.exit_code != 0
    assert "nothing to validate" in result.output
    for stub in stubs.values():
        assert not stub.called
