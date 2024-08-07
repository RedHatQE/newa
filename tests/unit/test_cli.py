from pathlib import Path
from unittest import mock

import pytest
from click.testing import CliRunner

import newa
from newa import Settings, cli


@pytest.fixture()
def mock_clicontext(tmp_path):
    """ Return a CLIContext object with mocked logger and temp dirpath"""
    return cli.CLIContext(
        logger=mock.MagicMock(),
        settings=Settings(
            et_url='http://dummy.et.url.com',
            ),
        state_dirpath=tmp_path,
        cli_environment={},
        cli_context={})


@pytest.fixture()
def _mock_errata_tool(monkeypatch):
    """ Patch methods and functions to avoid communication with ErrataTool """

    def mock_get_request(url: str):
        return {"mock_key": "mock_response"}

    def mock_et_fetch_info(self, id: str):
        """ Return a meaningful json with info """
        return {
            "synopsis": "testing errata",
            "content_types": ["rpm"],
            "people": {
                "assigned_to": "user@domain.com"},
            "respin_count": "1"}

    def mock_et_fetch_releases(self, id: str):
        """ Return a meaningful json with releases/builds """
        return {
            "RHEL-9.0.0.Z.EUS": [
                {
                    "somepkg-1.2-1.el9_3": {},
                    },
                ],
            "RHEL-9.2.0.Z.EUS": [
                {
                    "somepkg-1.2-1.el9_3": {},
                    },
                ],
            }

    # TODO in the future we might want to do more complex patching of the class
    # methods, but this will suffice for now
    monkeypatch.setenv("NEWA_ET_URL", "https://fake.erratatool.com")
    # We want to make sure we don't make requests to errata tool
    monkeypatch.setattr(newa, 'get_request', mock_get_request)
    monkeypatch.setattr(newa.ErrataTool, 'fetch_info', mock_et_fetch_info)
    monkeypatch.setattr(newa.ErrataTool, 'fetch_releases', mock_et_fetch_releases)


# TODO There's still not much logic to test in cli. These test is just a stub to
# have some tests running. We'll need to update them as we add more functionality

@pytest.mark.usefixtures('_mock_errata_tool')
def test_main_event():
    runner = CliRunner()
    with runner.isolated_filesystem() as temp_dir:
        result = runner.invoke(
            cli.main, ['--state-dir', temp_dir, 'event', '--erratum', '12345'])
        assert result.exit_code == 0
        assert len(list(Path(temp_dir).glob('event-12345*'))) == 2


@pytest.mark.usefixtures('_mock_errata_tool')
def test_event_with_id(mock_clicontext):
    runner = CliRunner()

    # Test that passing an erratum works
    ctx = mock_clicontext
    result = runner.invoke(cli.cmd_event, ['--erratum', '12345'], obj=ctx)
    assert result.exit_code == 0
    # This should have produced 2 event files, one per release (from mock_errata_tool)
    assert len(list(Path(ctx.state_dirpath).glob('event-12345*'))) == 2


@pytest.mark.usefixtures('_mock_errata_tool')
def test_event_no_id(mock_clicontext):
    # Test that not passing erratum loads the default errata config and excepts
    runner = CliRunner()
    ctx = mock_clicontext
    result = runner.invoke(cli.cmd_event, obj=ctx)
    assert result.exception
    assert len(list(Path(ctx.state_dirpath).glob('event-*'))) == 0
