"""Main CLI entry point for NEWA."""

import logging
import os
import re
import shutil
import sys
from pathlib import Path
from re import Pattern
from typing import Optional

import click

from newa import CLIContext, Settings
from newa.cli.commands.cancel_cmd import cmd_cancel
from newa.cli.commands.event_cmd import cmd_event
from newa.cli.commands.execute_cmd import cmd_execute
from newa.cli.commands.jira_cmd import cmd_jira
from newa.cli.commands.list_cmd import cmd_list
from newa.cli.commands.report_cmd import cmd_report
from newa.cli.commands.schedule_cmd import cmd_schedule
from newa.cli.commands.summarize_cmd import cmd_summarize
from newa.cli.constants import NEWA_DEFAULT_CONFIG
from newa.cli.utils import get_state_dir, initialize_state_dir

logging.basicConfig(
    format='%(asctime)s %(message)s',
    datefmt='%m/%d/%Y %I:%M:%S %p',
    level=logging.INFO)


def _should_filter_yaml_file(
        yaml_file: Path,
        action_id_pattern: Optional[Pattern[str]],
        issue_id_pattern: Optional[Pattern[str]],
        logger: logging.Logger) -> bool:
    """
    Check if a YAML file should be filtered out based on action_id and issue_id patterns.

    Args:
        yaml_file: Path to the YAML file to check
        action_id_pattern: Compiled regex pattern for action_id filtering (or None)
        issue_id_pattern: Compiled regex pattern for issue_id filtering (or None)
        logger: Logger instance for debug messages

    Returns:
        True if the file should be filtered out (skipped), False if it should be kept
    """
    if not action_id_pattern and not issue_id_pattern:
        return False  # No filters, keep the file

    try:
        from newa.utils.yaml_utils import yaml_parser

        # Load the YAML file to check filters
        yaml_data = yaml_parser().load(yaml_file.read_text())

        # Check action_id_filter if specified
        if action_id_pattern:
            action_id = yaml_data.get('jira', {}).get('action_id')
            if action_id and not action_id_pattern.fullmatch(action_id):
                logger.debug(
                    f'Filtering {yaml_file.name} (action_id "{action_id}" '
                    f'does not match filter)')
                return True

        # Check issue_id_filter if specified
        if issue_id_pattern:
            issue_id = yaml_data.get('jira', {}).get('id')
            if issue_id and not issue_id_pattern.fullmatch(issue_id):
                logger.debug(
                    f'Filtering {yaml_file.name} (issue_id "{issue_id}" '
                    f'does not match filter)')
                return True

    except Exception as e:
        logger.warning(f'Error reading {yaml_file.name}, filtering out: {e}')
        return True

    return False  # File matches all filters, keep it


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
    '--copy-state-dir',
    '-C',
    is_flag=True,
    default=False,
    help='Copy YAML files from state-dir to a new state-dir '
         '(requires --state-dir or --prev-state-dir).',
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
@click.option(
    '--issue-id-filter',
    default='',
    help='Regular expression matching Jira issue keys to process (only).',
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
         copy_state_dir: bool,
         force: bool,
         action_id_filter: str,
         issue_id_filter: str) -> None:
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

    # handle --copy-state-dir requirement
    if copy_state_dir and not state_dir and not prev_state_dir:
        raise click.ClickException(
            '--copy-state-dir requires either --state-dir or --prev-state-dir '
            'to identify the source directory.')

    # handle state_dir settings
    if prev_state_dir and state_dir:
        raise Exception('Use either --state-dir or --prev-state-dir')
    if prev_state_dir:
        try:
            state_dir = str(get_state_dir(settings.newa_statedir_topdir, use_ppid=True))
        except Exception as e:
            raise click.ClickException(
                'No previous state directory found for this shell session. '
                'Run newa without -P first to create a state directory, '
                'or use -D to specify an existing state directory.',
                ) from e
    elif not state_dir and not copy_state_dir:
        state_dir = str(get_state_dir(settings.newa_statedir_topdir))

    # When using --copy-state-dir, store the source and create a new target state-dir
    source_state_dir = None
    if copy_state_dir:
        source_state_dir = state_dir
        state_dir = str(get_state_dir(settings.newa_statedir_topdir))

    # handle --clear param
    settings.newa_clear_on_subcommand = clear

    try:
        pattern = re.compile(action_id_filter) if action_id_filter else None
    except re.error as e:
        raise Exception(
            f'Cannot compile --action-id-filter regular expression. {e!r}') from e

    try:
        issue_pattern = re.compile(issue_id_filter) if issue_id_filter else None
    except re.error as e:
        raise Exception(
            f'Cannot compile --issue-id-filter regular expression. {e!r}') from e

    ctx = CLIContext(
        settings=settings,
        logger=logging.getLogger(),
        state_dirpath=Path(os.path.expandvars(state_dir)),
        cli_environment={},
        cli_context={},
        prev_state_dirpath=prev_state_dirpath,
        force=force,
        action_id_filter_pattern=pattern,
        issue_id_filter_pattern=issue_pattern,
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

    # check mutual exclusivity of --extract-state-dir and --copy-state-dir
    if extract_state_dir and copy_state_dir:
        raise click.ClickException(
            'Cannot use both --copy-state-dir and --extract-state-dir options.')

    # extract YAML files from the given archive to state-dir
    if extract_state_dir:
        from typing import Any

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

        # Apply filters if specified - delete non-matching YAML files
        if pattern or issue_pattern:
            yaml_files = list(ctx.state_dirpath.glob('*.yaml'))
            deleted_count = 0
            kept_count = 0
            for yaml_file in yaml_files:
                if _should_filter_yaml_file(yaml_file, pattern, issue_pattern, ctx.logger):
                    yaml_file.unlink()
                    deleted_count += 1
                else:
                    kept_count += 1

            ctx.logger.info(
                f'Kept {kept_count} YAML file(s), deleted {deleted_count} non-matching')

    # copy YAML files from the given state directory to a new state-dir
    if copy_state_dir:
        assert source_state_dir is not None  # guaranteed by validation above
        source_dir = Path(source_state_dir)
        if not source_dir.exists():
            raise click.ClickException(
                f'Source state directory {source_dir} does not exist.')
        if not source_dir.is_dir():
            raise click.ClickException(
                f'Source state directory {source_dir} is not a directory.')

        ctx.logger.info(f'Copying YAML files from {source_dir} to {ctx.state_dirpath}')

        # Initialize the destination state directory first
        initialize_state_dir(ctx)

        # Copy all YAML files from source to destination
        yaml_files = list(source_dir.glob('*.yaml'))
        if not yaml_files:
            ctx.logger.warning(f'No YAML files found in {source_dir}')
        else:
            copied_count = 0
            skipped_count = 0
            for yaml_file in yaml_files:
                # Check if this file should be filtered out
                if _should_filter_yaml_file(yaml_file, pattern, issue_pattern, ctx.logger):
                    skipped_count += 1
                    continue

                dest_file = ctx.state_dirpath / yaml_file.name
                shutil.copy2(yaml_file, dest_file)
                ctx.logger.debug(f'Copied {yaml_file.name}')
                copied_count += 1

            ctx.logger.info(
                f'Copied {copied_count} YAML file(s), skipped {skipped_count}')

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
main.add_command(cmd_summarize)
