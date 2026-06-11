"""Search command for NEWA CLI."""

import logging
import re
from typing import Any, Optional

import click

from newa import CLIContext
from newa.cli.color_utils import init_colors
from newa.cli.commands.list_cmd import print_state_dirs


def _parse_search_spec(spec: str) -> tuple[Optional[str], str]:
    """Parse search specification into key pattern and value pattern.

    Args:
        spec: Search specification, either 'PATTERN' or 'KEY=PATTERN'

    Returns:
        Tuple of (key_pattern, value_pattern). key_pattern is None for value-only search.
    """
    if '=' in spec:
        # Split on first '=' only
        key_pattern, value_pattern = spec.split('=', 1)
        return (key_pattern.strip(), value_pattern.strip())
    return (None, spec)


def _search_object(
        obj: Any,
        key_pattern: Optional[re.Pattern[str]],
        value_pattern: re.Pattern[str],
        current_key: str = '') -> bool:
    """Recursively search within an object for matching values.

    Args:
        obj: Object to search (can be dict, list, or scalar)
        key_pattern: Compiled regex for key matching (None to match all keys)
        value_pattern: Compiled regex for value matching
        current_key: Current key name (for recursive calls)

    Returns:
        True if a match is found, False otherwise
    """
    # Check if current key matches (if key_pattern is specified)
    if key_pattern and current_key:
        key_matches = bool(key_pattern.search(current_key))
    else:
        # If no key_pattern, we search all keys
        key_matches = key_pattern is None

    # If this is a dict, recurse into its items
    if isinstance(obj, dict):
        for key, value in obj.items():
            if _search_object(value, key_pattern, value_pattern, current_key=key):
                return True
        return False

    # If this is a list, recurse into its items
    if isinstance(obj, list):
        for item in obj:
            if _search_object(item, key_pattern, value_pattern, current_key=current_key):
                return True
        return False

    # Scalar value - check if we should search it
    if key_matches:
        # Convert value to string and search
        str_value = str(obj) if obj is not None else ''
        if value_pattern.search(str_value):
            return True

    return False


@click.command(name='search')
@click.option(
    '--text',
    help='Regular expression pattern to search for across all metadata in state directories '
         '(case-insensitive).',
    )
@click.option(
    '--event',
    help='Search within event objects. Format: "PATTERN" or "KEY=PATTERN". '
         'Searches event-* YAML files.',
    )
@click.option(
    '--erratum',
    help='Search within erratum objects. Format: "PATTERN" or "KEY=PATTERN". '
         'Searches event-* YAML files.',
    )
@click.option(
    '--rog-mr',
    help='Search within RoG MR objects. Format: "PATTERN" or "KEY=PATTERN". '
         'Searches event-* YAML files.',
    )
@click.option(
    '--jira',
    help='Search within Jira issue objects. Format: "PATTERN" or "KEY=PATTERN". '
         'Searches jira-* YAML files.',
    )
@click.pass_obj
def cmd_search(
        ctx: CLIContext,
        text: Optional[str],
        event: Optional[str],
        erratum: Optional[str],
        rog_mr: Optional[str],
        jira: Optional[str]) -> None:
    """Search for text/pattern across all metadata in state directories.

    The search supports regular expressions and is case-insensitive by default.

    Examples:
        # Search all metadata
        newa search --text keylime

        # Search within event objects
        newa search --event "type_=erratum"

        # Search within erratum objects for specific key
        newa search --erratum "id=154960"

        # Search within jira objects
        newa search --jira "PROJ-.*"

        # Combine multiple filters
        newa search --erratum "keylime" --jira "action_id=tier1"
    """
    # Validate that at least one search option is provided
    if not any([text, event, erratum, rog_mr, jira]):
        raise click.UsageError(
            'At least one search option is required: '
            '--text, --event, --erratum, --rog-mr, or --jira')

    ctx.enter_command('search')
    init_colors(ctx.settings.newa_color_config)

    # save current logger level and statedir
    saved_logger_level = ctx.logger.level
    saved_state_dir = ctx.state_dirpath
    # when not in DEBUG, decrease log verbosity so it won't be too noisy
    # when loading individual YAML files
    if ctx.logger.level != logging.DEBUG:
        ctx.logger.setLevel(logging.WARN)

    # Parse and compile search specifications
    search_specs: list[tuple[str, Optional[re.Pattern[str]], re.Pattern[str]]] = []

    if text:
        try:
            search_specs.append(('text', None, re.compile(text, re.IGNORECASE)))
        except re.error as e:
            raise click.ClickException(
                f'Invalid regular expression in --text: {e}') from e

    for option_name, option_value in [
            ('event', event), ('erratum', erratum),
            ('rog', rog_mr), ('jira', jira)]:
        if option_value:
            key_pattern_str, value_pattern_str = _parse_search_spec(option_value)
            try:
                key_pat: Optional[re.Pattern[str]] = re.compile(
                    key_pattern_str, re.IGNORECASE) if key_pattern_str else None
                value_pat: re.Pattern[str] = re.compile(value_pattern_str, re.IGNORECASE)
                search_specs.append((option_name, key_pat, value_pat))
            except re.error as e:
                raise click.ClickException(
                    f'Invalid regular expression in --{option_name}: {e}') from e

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

        # Set context to this state directory
        ctx.state_dirpath = state_dir
        state_dir_matches = True  # All specs must match

        # Process each search specification
        for search_type, key_pattern, value_pattern in search_specs:
            spec_matches = False

            if search_type == 'text':
                # Search in all YAML files
                yaml_files = list(state_dir.glob('*.yaml'))
                for yaml_file in yaml_files:
                    try:
                        content = yaml_file.read_text()
                        if value_pattern.search(content):
                            ctx.logger.debug(
                                f'Text match in {state_dir}: {yaml_file.name}')
                            spec_matches = True
                            break
                    except Exception as e:
                        ctx.logger.debug(f'Error reading {yaml_file}: {e}')
                        continue

            elif search_type == 'jira':
                # Load jira jobs and search in jira objects
                try:
                    from attrs import asdict
                    jira_jobs = list(ctx.load_jira_jobs(filter_actions=False))
                    for jira_job in jira_jobs:
                        # Convert jira object to dict for searching
                        try:
                            jira_dict = asdict(jira_job.jira)
                        except Exception:
                            # Fallback to vars if asdict fails
                            jira_dict = vars(jira_job.jira)

                        if _search_object(jira_dict, key_pattern, value_pattern):
                            ctx.logger.debug(
                                f'Jira match in {state_dir}: {jira_job.jira.id}')
                            spec_matches = True
                            break
                except Exception as e:
                    ctx.logger.debug(f'Error loading jira jobs from {state_dir}: {e}')

            elif search_type in ['event', 'erratum', 'rog']:
                # Load artifact jobs and search in respective objects
                try:
                    from attrs import asdict
                    artifact_jobs = list(ctx.load_artifact_jobs(filter_events=False))
                    for artifact_job in artifact_jobs:
                        # Get the appropriate object based on search type
                        obj: Any = None
                        if search_type == 'event':
                            obj = artifact_job.event
                        elif search_type == 'erratum':
                            obj = artifact_job.erratum
                        elif search_type == 'rog':
                            obj = artifact_job.rog

                        if obj is None:
                            continue

                        # Convert object to dict for searching
                        try:
                            obj_dict = asdict(obj)
                        except Exception:
                            # Fallback to vars if asdict fails
                            obj_dict = vars(obj)

                        if _search_object(obj_dict, key_pattern, value_pattern):
                            ctx.logger.debug(
                                f'{search_type.capitalize()} match in {state_dir}')
                            spec_matches = True
                            break
                except Exception as e:
                    ctx.logger.debug(
                        f'Error loading artifact jobs from {state_dir}: {e}')

            # If this spec didn't match, the directory doesn't match
            if not spec_matches:
                state_dir_matches = False
                break

        # Add directory if all search specs matched
        if state_dir_matches:
            matching_state_dirs.append(state_dir)

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
        # Build description of search criteria
        search_desc_parts = []
        if text:
            search_desc_parts.append(f'--text "{text}"')
        if event:
            search_desc_parts.append(f'--event "{event}"')
        if erratum:
            search_desc_parts.append(f'--erratum "{erratum}"')
        if rog_mr:
            search_desc_parts.append(f'--rog-mr "{rog_mr}"')
        if jira:
            search_desc_parts.append(f'--jira "{jira}"')
        search_desc = ', '.join(search_desc_parts)
        print(
            f'Found {
                len(matching_state_dirs)} state director{
                "y" if len(matching_state_dirs) == 1 else "ies"} matching {search_desc}')
    else:
        print('No matches found')

    # Restore logger level and statedir
    ctx.logger.setLevel(saved_logger_level)
    ctx.state_dirpath = saved_state_dir
