from pathlib import Path
from unittest import mock

from click.testing import CliRunner

from newa import cli


# TODO There's still not much logic to test in cli. These test is just a stub to
# have some tests running. We'll need to update them as we add more functionality
def test_main():
    runner = CliRunner()
    with runner.isolated_filesystem() as temp_dir:
        result = runner.invoke(
            cli.main, ['--state-dir', temp_dir, 'event', '--erratum', '12345'])
        assert result.exit_code == 0
        assert len(list(Path(temp_dir).glob('event-12345*'))) > 0


def test_event():
    runner = CliRunner()
    ctx = mock.MagicMock()
    result = runner.invoke(cli.cmd_event, ['--erratum', '12345'], obj=ctx)
    assert result.exit_code == 0
    ctx.enter_command.assert_called()
    ctx.save_erratum_job.assert_called()
