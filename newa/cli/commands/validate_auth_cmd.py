"""Validate-auth command for NEWA CLI."""

from typing import Callable, Optional

import click
import requests

from newa import CLIContext, RoGTool
from newa.cli.initialization import (
    initialize_et_connection,
    initialize_rp_connection,
    )


def _describe_source(ctx: CLIContext, path: str, kind: str, present: bool) -> str:
    """Describe where a service's credential/setting was resolved from.

    ``path`` is the config path (e.g. ``'testingfarm/token'``), ``kind`` is a
    short noun shown to the user (e.g. ``'token'`` or ``'URL'``) and ``present``
    indicates whether a non-empty value was actually found.
    """
    if not present:
        return f"{kind} not configured"
    return f"{kind} from {ctx.settings.describe_source(path)}"


def _test_errata_tool(ctx: CLIContext) -> tuple[bool, str, Optional[str], str]:
    """Test Errata Tool connection using existing initialization."""
    url = ctx.settings.et_url
    source = _describe_source(ctx, 'erratatool/url', 'URL', bool(url))
    if not url:
        return False, "No URL configured", None, source

    try:
        initialize_et_connection(ctx)
        return True, "Connected", url, source
    except Exception as e:
        return False, str(e), url, source


def _test_jira(ctx: CLIContext) -> tuple[bool, str, Optional[str], str]:
    """Test Jira connection using existing connection wrapper."""
    url = ctx.settings.jira_url
    # Jira Cloud authenticates with email + API token, so surface the email
    # source too: a shadowing NEWA_JIRA_EMAIL can break auth invisibly.
    source = "; ".join((
        _describe_source(ctx, 'jira/token', 'token', bool(ctx.settings.jira_token)),
        _describe_source(ctx, 'jira/email', 'email', bool(ctx.settings.jira_email)),
        ))
    if not url:
        return False, "URL not configured", None, source

    try:
        jira_conn = ctx.get_jira_connection()
        jira_conn.get_connection()
        return True, "Authenticated successfully", url, source
    except Exception as e:
        return False, str(e), url, source


def _test_reportportal(ctx: CLIContext) -> tuple[bool, str, Optional[str], str]:
    """Test ReportPortal connection using existing initialization."""
    url = ctx.settings.rp_url
    source = _describe_source(ctx, 'reportportal/token', 'token', bool(ctx.settings.rp_token))
    if not url:
        return False, "No URL configured", None, source

    try:
        initialize_rp_connection(ctx)
        return True, "Connected", url, source
    except Exception as e:
        return False, str(e), url, source


def _test_testing_farm(ctx: CLIContext) -> tuple[bool, str, Optional[str], str]:
    """Test Testing Farm token validity."""
    token = ctx.settings.tf_token
    source = _describe_source(ctx, 'testingfarm/token', 'token', bool(token))
    if not token:
        return False, "Token not configured", None, source

    endpoint = "https://api.testing-farm.io/v0.1/whoami"
    try:
        response = requests.get(
            endpoint,
            headers={"Authorization": f"Bearer {token}"},
            timeout=15,
            )
        if response.status_code == 200:
            return True, "Token valid", endpoint, source
        if response.status_code in (401, 403):
            return False, f"Token rejected (HTTP {response.status_code})", endpoint, source
        return False, f"Testing Farm returned HTTP {response.status_code}", endpoint, source
    except requests.RequestException as e:
        return False, f"Testing Farm request failed: {e}", endpoint, source


def _test_rog(ctx: CLIContext) -> tuple[bool, str, Optional[str], str]:
    """Test RoG (GitLab) connection using existing RoGTool."""
    token = ctx.settings.rog_token
    source = _describe_source(ctx, 'rog/token', 'token', bool(token))
    if not token:
        return False, "Token not configured (optional)", None, source

    try:
        rog = RoGTool(token=token)
        # Trigger authentication by accessing the connection
        rog.connection.auth()
        return True, "Authenticated", "https://gitlab.com", source
    except Exception as e:
        return False, str(e), "https://gitlab.com", source


TestFunc = Callable[[CLIContext], tuple[bool, str, Optional[str], str]]

# Maps a config-file section name to its display name and test function. The
# order here is the order in which services are validated and reported.
TESTS: list[tuple[str, str, TestFunc]] = [
    ("erratatool", "Errata Tool", _test_errata_tool),
    ("jira", "Jira", _test_jira),
    ("reportportal", "ReportPortal", _test_reportportal),
    ("testingfarm", "Testing Farm", _test_testing_farm),
    ("rog", "RoG (GitLab)", _test_rog),
    ]


@click.command(name='validate-auth')
@click.option('--no-erratatool', 'no_erratatool', is_flag=True,
              help='Skip the Errata Tool check.')
@click.option('--no-jira', 'no_jira', is_flag=True,
              help='Skip the Jira check.')
@click.option('--no-reportportal', 'no_reportportal', is_flag=True,
              help='Skip the ReportPortal check.')
@click.option('--no-testingfarm', 'no_testingfarm', is_flag=True,
              help='Skip the Testing Farm check.')
@click.option('--no-rog', 'no_rog', is_flag=True,
              help='Skip the RoG (GitLab) check.')
@click.pass_obj
def cmd_validate_auth(
        ctx: CLIContext,
        no_erratatool: bool,
        no_jira: bool,
        no_reportportal: bool,
        no_testingfarm: bool,
        no_rog: bool,
        ) -> None:
    """Check configured service connectivity and supported authentication.

    By default every integration is checked. Use the ``--no-<section>`` options
    (named after the config-file sections) to skip individual integrations.
    """
    ctx.enter_command('validate-auth')

    skipped = {
        'erratatool': no_erratatool,
        'jira': no_jira,
        'reportportal': no_reportportal,
        'testingfarm': no_testingfarm,
        'rog': no_rog,
        }
    # Default to not-skipped for any section missing from the map so that a
    # future TESTS entry added without a matching --no-<section> option still
    # gets checked rather than crashing the whole command.
    tests = [
        (name, test_func)
        for section, name, test_func in TESTS
        if not skipped.get(section, False)
        ]
    if not tests:
        raise click.ClickException("All integrations were skipped; nothing to validate")

    click.echo("=" * 70)
    click.echo("NEWA Configuration Validation")
    click.echo("=" * 70)
    click.echo()

    results: dict[str, tuple[bool, str]] = {}
    urls_tested: dict[str, str] = {}
    sources: dict[str, str] = {}

    col_width = max(len(f"Testing {name}...") for name, _ in tests)

    for name, test_func in tests:
        label = f"Testing {name}..."
        click.echo(f"{label:<{col_width}}", nl=False)
        success, message, endpoint, source = test_func(ctx)
        results[name] = (success, message)
        sources[name] = source
        if endpoint:
            urls_tested[name] = endpoint
        if success:
            status_display = click.style("✓ OK", fg="green")
        else:
            status_display = click.style("✗ FAIL", fg="red")
        # Surface the credential source right next to the result so a stale
        # environment variable shadowing the config file is immediately visible.
        click.echo(f"{status_display}  {click.style(f'({source})', fg='bright_black')}")

    click.echo()
    click.echo("=" * 70)
    click.echo("Summary")
    click.echo("=" * 70)

    passed = sum(1 for success, _ in results.values() if success)
    total = len(results)
    click.echo(f"Passed: {passed}/{total}")
    click.echo()

    click.echo("Services tested:")
    for name, url in urls_tested.items():
        click.echo(f"  • {name}: {url} ({sources[name]})")
    click.echo()

    if passed < total:
        click.echo("Failed services:")
        for name, (success, message) in results.items():
            if not success:
                click.echo(f"  • {name}: {message} [{sources[name]}]")
        click.echo()
        raise click.ClickException("Configuration validation failed")
