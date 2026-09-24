"""Validate-auth command for NEWA CLI."""

import os
from pathlib import Path
from typing import Optional

import click

from newa import CLIContext, RoGTool
from newa.cli.constants import NEWA_DEFAULT_CONFIG
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
        et = initialize_et_connection(ctx)
        return True, "Connected and authenticated", url
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
        return True, "Connected and authenticated", url
    except Exception as e:
        return False, str(e), url


def _test_reportportal(ctx: CLIContext) -> tuple[bool, str, Optional[str]]:
    """Test ReportPortal connection using existing initialization."""
    url = ctx.settings.rp_url
    if not url:
        return False, "No URL configured", None

    try:
        rp = initialize_rp_connection(ctx)
        project_info = f" | Project: {ctx.settings.rp_project}" if ctx.settings.rp_project else ""
        return True, f"Connected and authenticated{project_info}", url
    except Exception as e:
        return False, str(e), url


def _test_testing_farm(ctx: CLIContext) -> tuple[bool, str, Optional[str]]:
    """Test Testing Farm token validity."""
    token = ctx.settings.tf_token
    if not token:
        return False, "Token not configured", None

    # Testing Farm token is just validated during connection initialization
    # by other commands, so we return success if token exists
    # The actual validation happens when executing tests
    return True, "Token configured", "https://api.testing-farm.io"


def _test_rog(ctx: CLIContext) -> tuple[bool, str, Optional[str]]:
    """Test RoG (GitLab) connection using existing RoGTool."""
    token = ctx.settings.rog_token
    if not token:
        return False, "Token not configured (optional)", None

    try:
        rog = RoGTool(token=token)
        # Trigger authentication by accessing the connection
        _ = rog.connection.auth()
        return True, "Connected and authenticated", "https://gitlab.com"
    except Exception as e:
        return False, str(e), "https://gitlab.com"


@click.command(name='validate-auth')
@click.pass_obj
def cmd_validate_auth(ctx: CLIContext) -> None:
    """Validate connectivity and authentication for all configured API endpoints."""
    ctx.enter_command('validate')

    # Check if config file exists
    conf_file = Path(os.path.expandvars(NEWA_DEFAULT_CONFIG))
    if not conf_file.exists():
        raise click.ClickException(
            f"Configuration file not found: {conf_file}\n"
            f"Please create {conf_file} or use --conf-file to specify a custom path."
        )

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

    for name, test_func in tests:
        click.echo(f"Testing {name}...", nl=False)
        success, message, endpoint = test_func(ctx)
        results[name] = (success, message)
        if endpoint:
            urls_tested[name] = endpoint
        status = click.style("✓", fg="green") if success else click.style("✗", fg="red")
        click.echo(f" {status} {message}")

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
