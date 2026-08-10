"""Cancel command for NEWA CLI."""

import os
import sys

import click

from newa import CLIContext, ExecuteHow, RequestResult, TFRequest
from newa.cli.execute_helpers import normalize_request_ids
from newa.cli.utils import initialize_state_dir, test_patterns_match


@click.command(name='cancel')
@click.option('--request',
              '-R',
              default=[],
              multiple=True,
              help=('Cancel NEWA request with the given request ID. '
                    'Can be specified multiple times. '
                    'Example: --request REQ-1.2.1'),
              )
@click.pass_obj
def cmd_cancel(ctx: CLIContext, request: list[str]) -> None:
    """Cancel running Testing Farm requests."""
    ctx.enter_command('cancel')

    # error out if state dir was not provided
    if not ctx.state_dirpath.exists():
        ctx.logger.error('ERROR: Cannot find NEWA state-dir! Use --state-dir or similar option.')
        sys.exit(1)

    # ensure existing state dir is initialized (store ppid)
    initialize_state_dir(ctx)

    # make TESTING_FARM_API_TOKEN available to workers as envvar if it has been
    # defined only though the settings file
    tf_token = ctx.settings.tf_token
    if not tf_token:
        raise ValueError("TESTING_FARM_API_TOKEN not set!")
    os.environ["TESTING_FARM_API_TOKEN"] = tf_token

    normalized_request = normalize_request_ids(list(request))

    for execute_job in ctx.load_execute_jobs(filter_actions=True):
        if normalized_request:
            (match, _) = test_patterns_match(
                execute_job.request.id, normalized_request)
            if not match:
                continue

        if execute_job.request.how == ExecuteHow.TESTING_FARM:
            tf_request = TFRequest(
                api=execute_job.execution.request_api,
                uuid=execute_job.execution.request_uuid)
            tf_request.cancel(ctx)
            tf_request.fetch_details()
            if tf_request.details:
                execute_job.execution.state = tf_request.details['state']
                if 'cancel' in execute_job.execution.state:
                    execute_job.execution.state = 'canceled'
                    execute_job.execution.result = RequestResult.ERROR
                elif tf_request.details['result']:
                    execute_job.execution.result = RequestResult(
                        tf_request.details['result']['overall'])
                ctx.save_execute_job(execute_job)
