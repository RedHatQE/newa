"""Event command for NEWA CLI."""

import copy
import sys
from typing import Optional

import click

from newa import (
    EVENT_FILE_PREFIX,
    ArtifactJob,
    CLIContext,
    Compose,
    ErratumContentType,
    Event,
    EventType,
    NVRParser,
    RoGTool,
    )
from newa.cli.initialization import initialize_et_connection
from newa.cli.utils import derive_compose, initialize_state_dir, test_file_presence


def copy_events_from_previous_statedir(ctx: CLIContext) -> None:
    """Copy event files from the previous state directory to the current one."""
    if not ctx.new_state_dir:
        raise Exception("Do not use 'newa -P' or 'newa -D' together with 'event --prev-event'")
    if not ctx.prev_state_dirpath:
        raise Exception('Could not identify the previous state-dir')

    ctx_prev = copy.deepcopy(ctx)
    ctx_prev.state_dirpath = ctx.prev_state_dirpath

    artifact_jobs = list(ctx_prev.load_artifact_jobs(filter_events=True))
    if not artifact_jobs:
        raise Exception(f'No {EVENT_FILE_PREFIX} YAML files found in {ctx_prev.state_dirpath}')

    for artifact_job in artifact_jobs:
        ctx.save_artifact_job(artifact_job)


def load_event_ids_from_init_files(
        ctx: CLIContext) -> tuple[list[str], list[str], list[str], list[str]]:
    """
    Load event IDs from init files and return as tuple.

    Returns (errata_ids, compose_ids, rog_urls, jira_keys).
    """
    errata_ids: list[str] = []
    compose_ids: list[str] = []
    rog_urls: list[str] = []
    jira_keys: list[str] = []

    events = [e.event for e in ctx.load_initial_errata()]
    for event in events:
        if event.type_ is EventType.ERRATUM:
            errata_ids.append(event.id)
        if event.type_ is EventType.COMPOSE:
            compose_ids.append(event.id)
        if event.type_ is EventType.ROG:
            rog_urls.append(event.id)
        if event.type_ is EventType.JIRA:
            jira_keys.append(event.id)

    return errata_ids, compose_ids, rog_urls, jira_keys


def process_event_errata(
        ctx: CLIContext,
        errata_ids: list[str],
        compose_mapping: list[str],
        deduplicate_releases: bool) -> None:
    """Process erratum IDs and create corresponding artifact jobs."""
    if not errata_ids:
        return

    et = initialize_et_connection(ctx)

    for erratum_id in errata_ids:
        event = Event(type_=EventType.ERRATUM, id=erratum_id)
        errata = et.get_errata(event, logger=ctx.logger, deduplicate_releases=deduplicate_releases)

        for erratum in errata:
            release = erratum.release.strip()
            compose = derive_compose(release, compose_mapping, ctx.logger)

            # skip compose if it has been transformed to an empty compose
            if not compose:
                ctx.logger.info(
                    f"""Erratum release {release} transformed to an empty string, skipping""")
                continue

            ctx.logger.info(
                f"""Erratum release {release} transformed to a compose {compose}""")

            if erratum.content_type in (ErratumContentType.RPM, ErratumContentType.MODULE):
                artifact_job = ArtifactJob(
                    event=event,
                    erratum=erratum,
                    compose=Compose(id=compose),
                    rog=None)
                # Apply event filter if specified
                if ctx.should_filter_job(artifact_job):
                    continue
                ctx.save_artifact_job(artifact_job)

            # for docker content type we create ArtifactJob per build
            if erratum.content_type == ErratumContentType.DOCKER:
                erratum_clone = erratum.clone()
                for build in erratum.builds:
                    erratum_clone.builds = [build]
                    erratum_clone.components = [NVRParser(build).name]
                    artifact_job = ArtifactJob(
                        event=event,
                        erratum=erratum_clone,
                        compose=Compose(id=compose),
                        rog=None)
                    # Apply event filter if specified
                    if ctx.should_filter_job(artifact_job):
                        continue
                    ctx.save_artifact_job(artifact_job)


def process_event_composes(ctx: CLIContext, compose_ids: list[str]) -> None:
    """Process compose IDs and create corresponding artifact jobs."""
    for compose_id in compose_ids:
        event = Event(type_=EventType.COMPOSE, id=compose_id)
        artifact_job = ArtifactJob(
            event=event,
            erratum=None,
            compose=Compose(id=compose_id),
            rog=None)
        # Apply event filter if specified
        if ctx.should_filter_job(artifact_job):
            continue
        ctx.save_artifact_job(artifact_job)


def process_event_rog_urls(
        ctx: CLIContext,
        rog_urls: list[str],
        compose_mapping: list[str]) -> None:
    """Process RoG merge request URLs and create corresponding artifact jobs."""
    if not rog_urls:
        return

    if not ctx.settings.rog_token:
        raise Exception('RoG token is not configured!')

    rog_tool = RoGTool(token=ctx.settings.rog_token)
    for url in rog_urls:
        mr = rog_tool.get_mr(url)
        compose_id = derive_compose(mr.build_target, compose_mapping, ctx.logger)
        event = Event(type_=EventType.ROG, id=url)
        artifact_job = ArtifactJob(
            event=event,
            erratum=None,
            compose=Compose(id=compose_id),
            rog=mr)
        # Apply event filter if specified
        if ctx.should_filter_job(artifact_job):
            continue
        ctx.save_artifact_job(artifact_job)


def process_event_jira_keys(ctx: CLIContext, jira_keys: list[str]) -> None:
    """Process Jira issue keys and create corresponding artifact jobs."""
    for jira_key in jira_keys:
        event = Event(type_=EventType.JIRA, id=jira_key)
        artifact_job = ArtifactJob(
            event=event,
            erratum=None,
            compose=None,
            rog=None)
        # Apply event filter if specified
        if ctx.should_filter_job(artifact_job):
            continue
        ctx.save_artifact_job(artifact_job)


@click.command(name='event')
@click.option(
    '-e', '--erratum', 'errata_ids',
    default=[],
    multiple=True,
    help='Specifies erratum-type event for a given advisory ID.',
    )
@click.option(
    '-c', '--compose', 'compose_ids',
    default=[],
    multiple=True,
    help='Specifies compose-type event for a given compose.',
    )
@click.option(
    '--jira-issue', 'jira_keys',
    default=[],
    multiple=True,
    help='Specifies Jira event for a given issue key.',
    )
@click.option(
    '--rog-mr', 'rog_urls',
    default=[],
    multiple=True,
    help='Specifies RoG merge-request URL.',
    )
@click.option(
    '--compose-mapping', 'compose_mapping',
    default=[],
    multiple=True,
    help=('Custom Erratum release to Testing Farm compose mapping in the form '
          '"RELEASE=COMPOSE". For example, '
          '"--compose-mapping RHEL-9.4.0.Z.MAIN+EUS=RHEL-9.4.0-Nightly". '
          'Can be specified multiple times, the 1st match is used'
          ),
    )
@click.option(
    '--deduplicate-releases',
    is_flag=True,
    default=False,
    help='Deduplicate erratum releases that map to the same Testing Farm compose',
    )
@click.option(
    '--prev-event',
    is_flag=True,
    default=False,
    help='Copy events from the previous NEWA state-dir',
    )
@click.pass_obj
def cmd_event(
        ctx: CLIContext,
        errata_ids: list[str],
        compose_ids: list[str],
        jira_keys: list[str],
        rog_urls: list[str],
        compose_mapping: list[str],
        deduplicate_releases: Optional[bool],
        prev_event: bool) -> None:
    """Process events and create artifact jobs."""
    ctx.enter_command('event')

    # ensure state dir is present and initialized
    initialize_state_dir(ctx)

    if ctx.settings.newa_clear_on_subcommand:
        ctx.remove_job_files(EVENT_FILE_PREFIX)

    if test_file_presence(ctx.state_dirpath, EVENT_FILE_PREFIX) and not ctx.force:
        ctx.logger.error(
            f'"{EVENT_FILE_PREFIX}" files already exist in state-dir {ctx.state_dirpath}, '
            'use --force to override')
        sys.exit(1)

    # copy events from the previous statedir
    if prev_event:
        copy_events_from_previous_statedir(ctx)

    # Load event IDs from init files if not provided via command line
    if not errata_ids and not compose_ids and not rog_urls and not jira_keys:
        errata_ids, compose_ids, rog_urls, jira_keys = load_event_ids_from_init_files(ctx)

    # Validate that at least one event source is provided
    if not errata_ids and not compose_ids and not rog_urls and not jira_keys and not prev_event:
        raise Exception('Missing event IDs!')

    # Determine deduplicate_releases setting: CLI option overrides config file
    deduplicate = (deduplicate_releases
                   if deduplicate_releases is not None
                   else ctx.settings.et_deduplicate_releases)

    # Process different event types
    process_event_errata(ctx, errata_ids, compose_mapping, deduplicate)
    process_event_composes(ctx, compose_ids)
    process_event_rog_urls(ctx, rog_urls, compose_mapping)
    process_event_jira_keys(ctx, jira_keys)
