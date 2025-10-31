"""Main CLI entry point for NEWA."""

import logging
import os
import re
import sys
from pathlib import Path

import click

from newa import CLIContext, Settings
from newa.cli.commands.cancel_cmd import cmd_cancel
from newa.cli.commands.event_cmd import cmd_event
from newa.cli.commands.execute_cmd import cmd_execute
from newa.cli.commands.jira_cmd import cmd_jira
from newa.cli.commands.list_cmd import cmd_list
from newa.cli.commands.report_cmd import cmd_report
from newa.cli.commands.schedule_cmd import cmd_schedule
from newa.cli.constants import NEWA_DEFAULT_CONFIG
from newa.cli.utils import get_state_dir

logging.basicConfig(
    format='%(asctime)s %(message)s',
    datefmt='%m/%d/%Y %I:%M:%S %p',
    level=logging.INFO)


@click.group(chain=True)
@click.option(
    '--state-dir',
    '-D',
    default='',
    help='Specify state directory.',
    )
@click.option(
    '--prev-state-dir',
    '-P',
    is_flag=True,
    default=False,
    help='Use the latest state-dir used previously within this shell session',
    )
@click.option(
    '--conf-file',
    default='',
    help='Path to newa configuration file.',
    )
@click.option(
    '--clear',
    is_flag=True,
    default=False,
    help='Each subcommand will remove existing YAML files before proceeding',
    )
@click.option(
    '--debug',
    is_flag=True,
    default=False,
    help='Enable debug logging',
    )
@click.option(
    '-e', '--environment', 'envvars',
    default=[],
    multiple=True,
    help='Specify custom environment variable, e.g. "-e FOO=BAR".',
    )
@click.option(
    '-c', '--context', 'contexts',
    default=[],
    multiple=True,
    help='Specify custom tmt context, e.g. "-c foo=bar".',
    )
@click.option(
    '--extract-state-dir',
    '-E',
    default='',
    help='Extract YAML files from the specified archive to state-dir (implies --force).',
    )
@click.option(
    '--force',
    is_flag=True,
    default=False,
    help='Force rewrite of existing YAML files.',
    )
@click.option(
    '--action-id-filter',
    default='',
    help='Regular expression matching issue-config action ids to process (only).',
    )
@click.pass_context
def main(click_context: click.Context,
         state_dir: str,
         prev_state_dir: bool,
         conf_file: str,
         clear: bool,
         debug: bool,
         envvars: list[str],
         contexts: list[str],
         extract_state_dir: str,
         force: bool,
         action_id_filter: str) -> None:
    """NEWA - New Errata Workflow Automation."""
    import io
    import tarfile
    import urllib

    # when user has specified config file, check its presence
    if conf_file:
        if not Path(conf_file).exists():
            raise FileNotFoundError(f"Configuration file '{conf_file}' does not exist.")
    else:
        conf_file = NEWA_DEFAULT_CONFIG
    # load settings
    settings = Settings.load(Path(os.path.expandvars(conf_file)))
    # try to identify prev_state_dirpath just in case we need it
    # we won't fail on errors
    try:
        prev_state_dirpath = get_state_dir(
            settings.newa_statedir_topdir, use_ppid=True)
    except Exception:
        prev_state_dirpath = None

    # handle state_dir settings
    if prev_state_dir and state_dir:
        raise Exception('Use either --state-dir or --prev-state-dir')
    if prev_state_dir:
        state_dir = str(get_state_dir(settings.newa_statedir_topdir, use_ppid=True))
    elif not state_dir:
        state_dir = str(get_state_dir(settings.newa_statedir_topdir))

    # handle --clear param
    settings.newa_clear_on_subcommand = clear

    try:
        pattern = re.compile(action_id_filter) if action_id_filter else None
    except re.error as e:
        raise Exception(
            f'Cannot compile --action-id-filter regular expression. {e!r}') from e

    ctx = CLIContext(
        settings=settings,
        logger=logging.getLogger(),
        state_dirpath=Path(os.path.expandvars(state_dir)),
        cli_environment={},
        cli_context={},
        prev_state_dirpath=prev_state_dirpath,
        force=force,
        action_id_filter_pattern=pattern,
        )
    click_context.obj = ctx

    # In case of '--help' we are going to end here
    if '--help' in sys.argv:
        return

    if debug:
        ctx.logger.setLevel(logging.DEBUG)

    ctx.logger.info(f'Using --state-dir={ctx.state_dirpath}')
    ctx.logger.debug(f'prev_state_dirpath={ctx.prev_state_dirpath}')

    if ctx.settings.newa_clear_on_subcommand:
        ctx.logger.debug('NEWA subcommands will remove existing YAML files.')

    # extract YAML files from the given archive to state-dir
    if extract_state_dir:
        from typing import Any

        from newa.cli.utils import initialize_state_dir
        ctx.new_state_dir = False
        # enforce --force
        ctx.force = True
        tar_open_kwargs: dict[str, Any] = {
            'mode': 'r:*',
            }
        if re.match('^https?://', extract_state_dir):
            data = urllib.request.urlopen(extract_state_dir).read()
            tar_open_kwargs['fileobj'] = io.BytesIO(data)
        else:
            tar_open_kwargs['name'] = Path(extract_state_dir)
        with tarfile.open(**tar_open_kwargs) as tf:
            for item in tf.getmembers():
                if item.name.endswith('.yaml'):
                    item.name = os.path.basename(item.name)
                    tf.extract(item, path=ctx.state_dirpath, filter='data')
        initialize_state_dir(ctx)

    def _split(s: str) -> tuple[str, str]:
        """Split key='some value' into a tuple (key, value)."""
        r = re.match(r"""^\s*([a-zA-Z0-9_][a-zA-Z0-9_\-]*)=["']?(.*?)["']?\s*$""", s)
        if not r:
            raise Exception(
                f'Option value {s} has invalid format, key=value format expected!')
        k, v = r.groups()
        return (k, v)

    # store environment variables and context provided on a cmdline
    ctx.cli_environment.update(dict(_split(s) for s in envvars))
    ctx.cli_context.update(dict(_split(s) for s in contexts))


# Register commands
main.add_command(cmd_list)
main.add_command(cmd_event)
main.add_command(cmd_jira)
main.add_command(cmd_schedule)
main.add_command(cmd_execute)
main.add_command(cmd_report)
main.add_command(cmd_cancel)
