"""Helper functions for the Schedule command."""

import os
import re
from typing import Any, Optional

from newa import (
    Arch,
    CLIContext,
    JiraJob,
    RawRecipeConfigDimension,
    RawRecipeReportPortalConfigDimension,
    RecipeConfig,
    Request,
    get_url_basename,
    render_template,
    short_sleep,
    yaml_parser,
    )
from newa.cli.constants import JIRA_NONE_ID


def _determine_architectures(
        ctx: CLIContext,
        arch_options: list[str],
        jira_job: JiraJob,
        compose: Optional[str]) -> list[Arch]:
    """Determine which architectures to use for scheduling."""
    if arch_options:
        return Arch.architectures([Arch(a.strip()) for a in arch_options])

    if jira_job.erratum and jira_job.erratum.archs:
        return jira_job.erratum.archs

    return Arch.architectures(compose=compose)


def _prepare_initial_config(
        ctx: CLIContext,
        jira_job: JiraJob,
        compose: Optional[str]) -> RawRecipeConfigDimension:
    """Prepare initial configuration from jira_job."""
    initial_config = RawRecipeConfigDimension(
        compose=compose,
        environment=jira_job.recipe.environment or {},
        context=jira_job.recipe.context or {})

    # Add erratum context if testing erratum
    if jira_job.erratum:
        initial_config['context'].update({'erratum': str(jira_job.erratum.id)})

    ctx.logger.debug(f'Initial config: {initial_config})')
    return initial_config


def _process_fixtures(
        ctx: CLIContext,
        fixtures: list[str],
        cli_config: RawRecipeConfigDimension) -> None:
    """Process command-line fixtures and update cli_config in place."""
    if not fixtures:
        return

    for fixture in fixtures:
        r = re.fullmatch(r'([^\s=]+)=([^=]*)', fixture)
        if not r:
            raise Exception(
                f"Fixture {fixture} does not having expected format 'name=value'")

        fixture_name, fixture_value = r.groups()
        fixture_config = cli_config

        # Descend through keys to the lowest level
        while '.' in fixture_name:
            prefix, suffix = fixture_name.split('.', 1)
            fixture_config = fixture_config.setdefault(prefix, {})  # type: ignore [misc]
            fixture_name = suffix

        # Parse the input as yaml to enable lists and dicts
        value = yaml_parser().load(fixture_value)
        fixture_config[fixture_name] = value  # type: ignore[literal-required]

    ctx.logger.debug(f'CLI config modified through --fixture: {cli_config})')


def _configure_recipe(
        ctx: CLIContext,
        config: RecipeConfig,
        architectures: list[Arch],
        recipe_url: str) -> None:
    """Configure recipe with architecture and reportportal defaults."""
    # Extend dimensions with system architecture but do not override existing settings
    if 'arch' not in config.dimensions:
        config.dimensions['arch'] = []
        for architecture in architectures:
            config.dimensions['arch'].append({'arch': architecture})

    # If RP launch name is not specified in the recipe, set it based on the recipe filename
    if not config.fixtures.get('reportportal', None):
        config.fixtures['reportportal'] = RawRecipeReportPortalConfigDimension()

    # Populate default for config.fixtures['reportportal']['launch_name']
    if ((config.fixtures['reportportal'] is not None) and
            (not config.fixtures['reportportal'].get('launch_name', None))):
        config.fixtures['reportportal']['launch_name'] = os.path.splitext(
            get_url_basename(recipe_url))[0]


def _render_request_attributes(
        request: Request,
        jinja_vars: dict[str, Any]) -> None:
    """Render request attributes as Jinja templates in place."""
    for attr in ("reportportal", "tmt", "testingfarm", "environment", "context", "compose"):
        # compose value is a string, not dict
        if attr == 'compose':
            value = getattr(request, attr, '')
            new_value = render_template(str(value), **jinja_vars)
            if new_value:
                setattr(request, attr, new_value)
        else:
            # getattr(request, attr) could also be None due to 'attr' being None
            mapping = getattr(request, attr, {}) or {}
            for (key, value) in mapping.items():
                # launch_attributes is a dict
                if key == 'launch_attributes':
                    for (k, v) in value.items():
                        mapping[key][k] = render_template(str(v), **jinja_vars)
                else:
                    mapping[key] = render_template(str(value), **jinja_vars)


def _prepare_jinja_vars_for_request(
        jira_job: JiraJob,
        request: Request,
        issue_fields: Any) -> dict[str, Any]:
    """Prepare Jinja template variables for request rendering."""
    jinja_vars: dict[str, Any] = {
        'EVENT': jira_job.event,
        'ERRATUM': jira_job.erratum,
        'COMPOSE': jira_job.compose,
        'ROG': jira_job.rog,
        'CONTEXT': request.context,
        'ENVIRONMENT': request.environment,
        'ISSUE': issue_fields}

    if request.arch:
        jinja_vars['ARCH'] = request.arch.value

    return jinja_vars


def _get_issue_fields_for_jira(
        ctx: CLIContext,
        jira_job: JiraJob) -> Any:
    """Get Jira issue fields if available, otherwise return empty dict."""
    if jira_job.jira.id and (not jira_job.jira.id.startswith(JIRA_NONE_ID)):
        jira_connection = ctx.get_jira_connection()
        issue_fields = jira_connection.get_connection().issue(jira_job.jira.id).fields
        issue_fields.id = jira_job.jira.id
        short_sleep()
        return issue_fields
    return {}


def _process_jira_job(
        ctx: CLIContext,
        jira_job: JiraJob,
        arch_options: list[str],
        fixtures: list[str],
        no_reportportal: bool) -> None:
    """Process a single jira_job and create schedule jobs."""
    from newa import ScheduleJob

    # Determine compose and architectures
    compose = jira_job.compose.id if jira_job.compose else None
    architectures = _determine_architectures(ctx, arch_options, jira_job, compose)

    # Prepare initial and CLI configs
    initial_config = _prepare_initial_config(ctx, jira_job, compose)
    cli_config = RawRecipeConfigDimension(
        environment=ctx.cli_environment,
        context=ctx.cli_context)
    ctx.logger.debug(f'CLI config: {cli_config})')

    # Process fixtures
    _process_fixtures(ctx, fixtures, cli_config)

    # Load and configure recipe
    config = RecipeConfig.from_yaml_with_includes(jira_job.recipe.url)
    _configure_recipe(ctx, config, architectures, jira_job.recipe.url)

    # Build requests
    jinja_vars: dict[str, Any] = {
        'EVENT': jira_job.event,
        'ERRATUM': jira_job.erratum,
        }
    requests = list(config.build_requests(initial_config, cli_config, jinja_vars))
    ctx.logger.info(f'{len(requests)} requests have been generated')

    # Get Jira issue fields
    issue_fields = _get_issue_fields_for_jira(ctx, jira_job)

    # Create ScheduleJob for each request
    for request in requests:
        # Clear reportportal attribute when --no-reportportal
        if no_reportportal:
            request.reportportal = None

        # Prepare Jinja variables and render request attributes
        jinja_vars = _prepare_jinja_vars_for_request(jira_job, request, issue_fields)
        _render_request_attributes(request, jinja_vars)

        # Create and save schedule job
        schedule_job = ScheduleJob(
            event=jira_job.event,
            erratum=jira_job.erratum,
            compose=jira_job.compose,
            rog=jira_job.rog,
            jira=jira_job.jira,
            recipe=jira_job.recipe,
            request=request)
        ctx.save_schedule_job(schedule_job)
