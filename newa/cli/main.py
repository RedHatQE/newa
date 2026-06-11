"""Main CLI entry point for NEWA."""

import logging
import os
import re
import shutil
import sys
from pathlib import Path
from re import Pattern
from typing import TYPE_CHECKING, Optional

import click

from newa import CLIContext, Settings
from newa.cli.commands.cancel_cmd import cmd_cancel
from newa.cli.commands.event_cmd import cmd_event
from newa.cli.commands.execute_cmd import cmd_execute
from newa.cli.commands.jira_cmd import cmd_jira
from newa.cli.commands.list_cmd import cmd_list
from newa.cli.commands.report_cmd import cmd_report
from newa.cli.commands.schedule_cmd import cmd_schedule
from newa.cli.commands.search_cmd import cmd_search
from newa.cli.commands.summarize_cmd import cmd_summarize
from newa.cli.constants import NEWA_DEFAULT_CONFIG
from newa.cli.event_helpers import parse_event_filter, should_filter_by_event
from newa.cli.filter_helpers import should_filter_by_action_tags
from newa.cli.metadata import setup_state_metadata
from newa.cli.tag_filter import TagFilter, parse_tag_filter
from newa.cli.utils import get_state_dir, initialize_state_dir

if TYPE_CHECKING:
    from newa.models.settings import EventFilter

logging.basicConfig(
    format='%(asctime)s %(message)s',
    datefmt='%m/%d/%Y %I:%M:%S %p',
    level=logging.INFO)


def _should_filter_yaml_file(
        yaml_file: Path,
        action_id_pattern: Optional[Pattern[str]],
        issue_id_pattern: Optional[Pattern[str]],
        event_filter_pattern: Optional['EventFilter'],
        action_tag_filter: Optional[TagFilter],
        logger: logging.Logger) -> bool:
    """
    Check if a YAML file should be filtered out based on action_id, issue_id,
    tag, and event patterns.

    Args:
        yaml_file: Path to the YAML file to check
        action_id_pattern: Compiled regex pattern for action_id filtering (or None)
        issue_id_pattern: Compiled regex pattern for issue_id filtering (or None)
        event_filter_pattern: EventFilter for event/artifact filtering (or None)
        action_tag_filter: TagFilter for tag filtering (or None)
        logger: Logger instance for debug messages

    Returns:
        True if the file should be filtered out (skipped), False if it should be kept
    """
    if (not action_id_pattern and not issue_id_pattern
            and not event_filter_pattern and not action_tag_filter):
        return False  # No filters, keep the file

    try:
        from newa.utils.yaml_utils import yaml_parser

        # Load the YAML file once to check all filters
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

        # Check action_tag_filter if specified
        if action_tag_filter:
            action_tags = yaml_data.get('jira', {}).get('action_tags')
            if should_filter_by_action_tags(action_tags, action_tag_filter):
                logger.debug(
                    f'Filtering {yaml_file.name} (no action_tags match filter)')
                return True

        # Check event_filter if specified
        if event_filter_pattern:
            from newa.models.jobs import ArtifactJob

            # Create job from already-loaded YAML data (avoids double parsing)
            job = ArtifactJob(**yaml_data)
            if should_filter_by_event(event_filter_pattern, job, logger, log_message=False):
                logger.debug(f'Filtering {yaml_file.name} (event filter)')
                return True

    except Exception as e:
        logger.warning(f'Error reading {yaml_file.name}, filtering out: {e}')
        return True

    return False  # File matches all filters, keep it


@click.group(chain=True, invoke_without_command=True)
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
@click.option(
    '--event-filter',
    default='',
    help='Filter by event/artifact attributes, e.g., "compose.id=RHEL-8.*" '
         'or "erratum.release=RHEL-9.5". '
         'Supported: compose.id, erratum.id, erratum.release, rog.id.',
    )
@click.option(
    '--action-tag-filter',
    default='',
    help='Filter by action tags using expression syntax. '
         'Use "|" for OR (e.g., "regression|security"), '
         '"," for AND (e.g., "smoke,rhel-9"), '
         'and "!" for NOT (e.g., "!slow"). '
         'Can combine: "regression|security,!slow".',
    )
@click.option(
    '--no-comments',
    is_flag=True,
    default=False,
    help='Disable all comments (Errata Tool, RoG MR, and Jira).',
    )
@click.option(
    '--description', '-d',
    default='',
    help='Description for the state directory (stored in .newa-metadata.yaml).',
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
         issue_id_filter: str,
         event_filter: str,
         action_tag_filter: str,
         no_comments: bool,
         description: str) -> None:
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

    # Parse tag filter if provided
    try:
        tag_filter_obj = parse_tag_filter(action_tag_filter) if action_tag_filter else None
    except ValueError as e:
        raise click.ClickException(
            f'Cannot parse --action-tag-filter expression: {e}') from e

    # Parse event filter if provided
    event_filter_obj = parse_event_filter(event_filter) if event_filter else None

    ctx = CLIContext(
        settings=settings,
        logger=logging.getLogger(),
        state_dirpath=Path(os.path.expandvars(state_dir)),
        cli_environment={},
        cli_context={},
        prev_state_dirpath=prev_state_dirpath,
        force=force,
        no_comments=no_comments,
        description=description,
        action_id_filter_pattern=pattern,
        issue_id_filter_pattern=issue_pattern,
        event_filter_pattern=event_filter_obj,
        action_tag_filter_pattern=tag_filter_obj,
        )
    click_context.obj = ctx

    # Override comment settings when --no-comments is specified
    # This takes precedence over config file and environment variable settings
    if no_comments:
        ctx.settings.et_enable_comments = False
        ctx.settings.rog_enable_comments = False
        ctx.settings.jira_enable_comments = False

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
        if pattern or issue_pattern or event_filter_obj or tag_filter_obj:
            yaml_files = list(ctx.state_dirpath.glob('*.yaml'))
            deleted_count = 0
            kept_count = 0
            for yaml_file in yaml_files:
                if _should_filter_yaml_file(
                        yaml_file,
                        pattern,
                        issue_pattern,
                        event_filter_obj,
                        tag_filter_obj,
                        ctx.logger):
                    yaml_file.unlink()
                    deleted_count += 1
                else:
                    kept_count += 1

            ctx.logger.info(
                f'Kept {kept_count} YAML file(s), deleted {deleted_count} non-matching')

        # Handle metadata for extracted state-dir
        setup_state_metadata(
            ctx.state_dirpath,
            description=description,
            parent_state_dir=extract_state_dir,
            operation='extract',
            logger=ctx.logger)

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
                if _should_filter_yaml_file(
                        yaml_file,
                        pattern,
                        issue_pattern,
                        event_filter_obj,
                        tag_filter_obj,
                        ctx.logger):
                    skipped_count += 1
                    continue

                dest_file = ctx.state_dirpath / yaml_file.name
                shutil.copy2(yaml_file, dest_file)
                ctx.logger.debug(f'Copied {yaml_file.name}')
                copied_count += 1

            ctx.logger.info(
                f'Copied {copied_count} YAML file(s), skipped {skipped_count}')

        # Handle metadata for copied state-dir
        setup_state_metadata(
            ctx.state_dirpath,
            description=description,
            parent_state_dir=str(source_dir),
            operation='copy',
            logger=ctx.logger)

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

    # Handle description update for existing state-dir (when using -D or -P with --description)
    # This allows updating description without running other subcommands
    if (description and (state_dir or prev_state_dir) and not copy_state_dir
            and not extract_state_dir and ctx.state_dirpath.exists() and not ctx.new_state_dir):
        setup_state_metadata(
            ctx.state_dirpath,
            description=description,
            operation='update',
            logger=ctx.logger)

    # If no subcommand was specified, invoke the list command by default.
    # Avoid doing this during resilient parsing (e.g., shell completion).
    if (not click_context.resilient_parsing
            and click_context.invoked_subcommand is None):
        click_context.invoke(cmd_list)


# Register commands
main.add_command(cmd_list)
main.add_command(cmd_event)
main.add_command(cmd_jira)
main.add_command(cmd_schedule)
main.add_command(cmd_execute)
main.add_command(cmd_report)
main.add_command(cmd_cancel)
main.add_command(cmd_search)
main.add_command(cmd_summarize)
