"""Search command for NEWA CLI."""

import logging
import re

import click

from newa import CLIContext
from newa.cli.color_utils import init_colors
from newa.cli.commands.list_cmd import print_state_dirs


@click.command(name='search')
@click.option(
    '--text',
    required=True,
    help='Regular expression pattern to search for across all metadata in state directories '
         '(case-insensitive).',
    )
@click.pass_obj
def cmd_search(
        ctx: CLIContext,
        text: str) -> None:
    """Search for text/pattern across all metadata in state directories.

    The search supports regular expressions and is case-insensitive by default.
    """
    ctx.enter_command('search')
    init_colors(ctx.settings.newa_color_config)

    # save current logger level and statedir
    saved_logger_level = ctx.logger.level
    saved_state_dir = ctx.state_dirpath
    # when not in DEBUG, decrease log verbosity so it won't be too noisy
    # when loading individual YAML files
    if ctx.logger.level != logging.DEBUG:
        ctx.logger.setLevel(logging.WARN)

    # Compile regex pattern (case-insensitive)
    try:
        search_pattern = re.compile(text, re.IGNORECASE)
    except re.error as e:
        raise click.ClickException(
            f'Invalid regular expression pattern: {e}') from e

    matching_state_dirs = []

    # Get all state directories
    try:
        entries = list(ctx.settings.newa_statedir_topdir.iterdir())
    except FileNotFoundError as e:
        raise Exception(f'{ctx.settings.newa_statedir_topdir} does not exist') from e

    sorted_entries = sorted(entries, key=lambda entry: entry.stat().st_mtime)

    # Find state directories with matches
    for state_dir in sorted_entries:
        if not state_dir.is_dir():
            continue

        # Search in all YAML files in the state directory
        yaml_files = list(state_dir.glob('*.yaml'))
        if not yaml_files:
            continue

        # Check if any file contains the search pattern
        for yaml_file in yaml_files:
            try:
                content = yaml_file.read_text()
                if search_pattern.search(content):
                    matching_state_dirs.append(state_dir)
                    ctx.logger.debug(f'Match in {state_dir}: {yaml_file.name}')
                    break  # Found match in this dir, move to next dir
            except Exception as e:
                ctx.logger.debug(f'Error reading {yaml_file}: {e}')
                continue

    # Print matching state directories with event-level details
    if matching_state_dirs:
        print_state_dirs(
            ctx,
            matching_state_dirs,
            events=True,  # Show event-level details like 'newa list --events'
            issues=False,
            refresh=False,
            refresh_all=False,
            specific_state_dir=False)
        print(
            f'Found {
                len(matching_state_dirs)} state director{
                "y" if len(matching_state_dirs) == 1 else "ies"} with matches')
    else:
        print(f'No matches found for "{text}"')

    # Restore logger level and statedir
    ctx.logger.setLevel(saved_logger_level)
    ctx.state_dirpath = saved_state_dir
