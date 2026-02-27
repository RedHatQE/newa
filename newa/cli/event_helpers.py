"""Helper functions for event/artifact filtering."""

import logging
import re
from typing import TYPE_CHECKING, Union

import click

if TYPE_CHECKING:
    from newa.models.jobs import ArtifactJob, ExecuteJob, JiraJob, ScheduleJob
    from newa.models.settings import EventFilter


def parse_event_filter(event_filter: str) -> 'EventFilter':
    """
    Parse and validate event filter expression.

    Args:
        event_filter: Filter expression in format "object.attribute=regex"

    Returns:
        EventFilter object with parsed components

    Raises:
        click.ClickException: If the filter format or values are invalid
    """
    from newa.models.settings import EventFilter

    # Parse format: object.attribute=regex
    match = re.match(r'^([a-z]+)\.([a-z_]+)=(.+)$', event_filter)
    if not match:
        raise click.ClickException(
            f'Invalid --event-filter format: "{event_filter}". '
            'Expected format: "object.attribute=regex" (e.g., "compose.id=RHEL-8.*")')

    object_type, attribute, regex_pattern = match.groups()

    # Validate supported combinations
    supported_filters = {
        'compose': ['id'],
        'erratum': ['id', 'release'],
        'rog': ['id'],
        }

    if object_type not in supported_filters:
        raise click.ClickException(
            f'Unsupported object type "{object_type}" in --event-filter. '
            f'Supported: {", ".join(supported_filters.keys())}')

    if attribute not in supported_filters[object_type]:
        raise click.ClickException(
            f'Unsupported attribute "{attribute}" for {object_type} in --event-filter. '
            f'Supported for {object_type}: {", ".join(supported_filters[object_type])}')

    # Compile the regex pattern
    try:
        compiled_pattern = re.compile(regex_pattern)
    except re.error as e:
        raise click.ClickException(
            f'Cannot compile --event-filter regular expression "{regex_pattern}". {e!r}') from e

    return EventFilter(
        object_type=object_type,
        attribute=attribute,
        pattern=compiled_pattern,
        )


def should_filter_by_event(
        event_filter: 'EventFilter',
        job: Union['ArtifactJob', 'JiraJob', 'ScheduleJob', 'ExecuteJob'],
        logger: logging.Logger,
        log_message: bool = True) -> bool:
    """
    Check if a job should be filtered out based on event filter pattern.

    Args:
        event_filter: EventFilter with object type, attribute, and pattern
        job: Job to check (ArtifactJob or any subclass: JiraJob, ScheduleJob, ExecuteJob)
        logger: Logger instance for messages
        log_message: Whether to log info messages (vs debug only)

    Returns:
        True if the job should be skipped, False if it should be processed.
    """
    value_to_check = None
    artifact_type = None

    # Extract the value based on filter configuration
    if event_filter.object_type == 'compose' and job.compose:
        artifact_type = 'compose'
        if event_filter.attribute == 'id':
            value_to_check = job.compose.id
    elif event_filter.object_type == 'erratum' and job.erratum:
        artifact_type = 'erratum'
        if event_filter.attribute == 'id':
            value_to_check = job.erratum.id
        elif event_filter.attribute == 'release':
            value_to_check = job.erratum.release
    elif event_filter.object_type == 'rog' and job.rog:
        artifact_type = 'rog'
        if event_filter.attribute == 'id':
            value_to_check = job.rog.id

    # If the artifact type doesn't match the filter, skip it
    if artifact_type is None:
        if log_message:
            logger.debug(
                f"Skipping job {job.id} - it doesn't have a "
                f"{event_filter.object_type} artifact")
        return True

    # If we couldn't extract a value, skip it
    if not value_to_check:
        if log_message:
            logger.debug(
                f"Skipping job {job.id} - {event_filter.object_type}.{event_filter.attribute} "
                "has no value")
        return True

    # Check if the value matches the pattern
    if not event_filter.pattern.fullmatch(value_to_check):
        if log_message:
            logger.info(
                f"Skipping job {job.id} - {event_filter.object_type}.{event_filter.attribute}="
                f'"{value_to_check}" doesn\'t match the --event-filter regular expression.')
        else:
            logger.debug(
                f"Skipping job {job.id} - {event_filter.object_type}.{event_filter.attribute}="
                f'"{value_to_check}" doesn\'t match the --event-filter regular expression.')
        return True

    logger.debug(
        f"{event_filter.object_type}.{event_filter.attribute}="
        f'"{value_to_check}" matches the --event-filter regular expression.')
    return False
