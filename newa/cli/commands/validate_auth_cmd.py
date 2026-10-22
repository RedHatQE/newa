"""Validate-auth command for NEWA CLI."""

from typing import Optional

import click
import requests

from newa import CLIContext, RoGTool
from newa.cli.initialization import (
    initialize_et_connection,
    initialize_rp_connection,
    )


def _test_errata_tool(ctx: CLIContext) -> tuple[bool, str, Optional[str]]:
    """Test Errata Tool connection using existing initialization."""
    url = ctx.settings.et_url
    if not url:
        return False, "No URL configured", None

    try:
        initialize_et_connection(ctx)
        return True, "Connected", url
    except Exception as e:
        return False, str(e), url


def _test_jira(ctx: CLIContext) -> tuple[bool, str, Optional[str]]:
    """Test Jira connection using existing connection wrapper."""
    url = ctx.settings.jira_url
    if not url:
        return False, "URL not configured", None

    try:
        jira_conn = ctx.get_jira_connection()
        jira_conn.get_connection()
        return True, "Authenticated successfully", url
    except Exception as e:
        return False, str(e), url


def _test_reportportal(ctx: CLIContext) -> tuple[bool, str, Optional[str]]:
    """Test ReportPortal connection using existing initialization."""
    url = ctx.settings.rp_url
    if not url:
        return False, "No URL configured", None

    try:
        initialize_rp_connection(ctx)
        return True, "Connected", url
    except Exception as e:
        return False, str(e), url


def _test_testing_farm(ctx: CLIContext) -> tuple[bool, str, Optional[str]]:
    """Test Testing Farm token validity."""
    token = ctx.settings.tf_token
    if not token:
        return False, "Token not configured", None

    endpoint = "https://api.testing-farm.io/v0.1/whoami"
    try:
        response = requests.get(
            endpoint,
            headers={"Authorization": f"Bearer {token}"},
            timeout=15,
            )
        if response.status_code == 200:
            return True, "Token valid", endpoint
        if response.status_code in (401, 403):
            return False, f"Token rejected (HTTP {response.status_code})", endpoint
        return False, f"Testing Farm returned HTTP {response.status_code}", endpoint
    except requests.RequestException as e:
        return False, f"Testing Farm request failed: {e}", endpoint


def _test_rog(ctx: CLIContext) -> tuple[bool, str, Optional[str]]:
    """Test RoG (GitLab) connection using existing RoGTool."""
    token = ctx.settings.rog_token
    if not token:
        return False, "Token not configured (optional)", None

    try:
        rog = RoGTool(token=token)
        # Trigger authentication by accessing the connection
        rog.connection.auth()
        return True, "Authenticated", "https://gitlab.com"
    except Exception as e:
        return False, str(e), "https://gitlab.com"


@click.command(name='validate-auth')
@click.pass_obj
def cmd_validate_auth(ctx: CLIContext) -> None:
    """Check configured service connectivity and supported authentication."""
    ctx.enter_command('validate-auth')

    tests = [
        ("Errata Tool", _test_errata_tool),
        ("Jira", _test_jira),
        ("ReportPortal", _test_reportportal),
        ("Testing Farm", _test_testing_farm),
        ("RoG (GitLab)", _test_rog),
        ]

    click.echo("=" * 70)
    click.echo("NEWA Configuration Validation")
    click.echo("=" * 70)
    click.echo()

    results: dict[str, tuple[bool, str]] = {}
    urls_tested: dict[str, str] = {}

    col_width = max(len(f"Testing {name}...") for name, _ in tests)

    for name, test_func in tests:
        label = f"Testing {name}..."
        click.echo(f"{label:<{col_width}}", nl=False)
        success, message, endpoint = test_func(ctx)
        results[name] = (success, message)
        if endpoint:
            urls_tested[name] = endpoint
        if success:
            status_display = click.style("✓ OK", fg="green")
        else:
            status_display = click.style("✗ FAIL", fg="red")
        click.echo(status_display)

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
        click.echo(f"  • {name}: {url}")
    click.echo()

    if passed < total:
        click.echo("Failed services:")
        for name, (success, message) in results.items():
            if not success:
                click.echo(f"  • {name}: {message}")
        click.echo()
        raise click.ClickException("Configuration validation failed")
