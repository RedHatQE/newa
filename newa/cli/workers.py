"""Worker functions for parallel test execution."""

import os
import time
from functools import partial
from pathlib import Path

from newa import (
    CLIContext,
    ExecuteHow,
    ExecuteJob,
    Execution,
    RequestResult,
    ScheduleJob,
    TFRequest,
    )
from newa.cli.utils import test_patterns_match


def worker(ctx: CLIContext, schedule_file: Path) -> None:
    """Main worker function that delegates to TF or TMT worker."""
    # read request details
    schedule_job = ScheduleJob.from_yaml_file(Path(schedule_file))
    if schedule_job.request.how == ExecuteHow.TMT:
        tmt_worker(ctx, schedule_file, schedule_job)
    else:
        tf_worker(ctx, schedule_file, schedule_job)


def tf_worker(ctx: CLIContext, schedule_file: Path, schedule_job: ScheduleJob) -> None:
    """Worker function for Testing Farm execution."""
    # modify log message so it contains name of the processed file
    # so that we can distinguish individual workers
    log = partial(lambda msg: ctx.logger.info("%s: %s", schedule_file.name, msg))

    log('processing TF request...')

    start_new_request = True
    skip_initial_sleep = False
    # if --continue, then read ExecuteJob details as well
    if ctx.continue_execution:
        parent = schedule_file.parent
        name = schedule_file.name
        from newa import EXECUTE_FILE_PREFIX, SCHEDULE_FILE_PREFIX
        execute_job_file = Path(
            os.path.join(
                parent,
                name.replace(
                    SCHEDULE_FILE_PREFIX,
                    EXECUTE_FILE_PREFIX,
                    1)))
        if execute_job_file.exists():
            execute_job = ExecuteJob.from_yaml_file(execute_job_file)
            if execute_job.execution.result in ctx.restart_result:
                log(f'Restarting request {execute_job.request.id}'
                    f' with result {execute_job.execution.result}')
            elif ctx.restart_request:
                (match, pattern) = test_patterns_match(execute_job.request.id, ctx.restart_request)
                if match:
                    log(f'Restarting request {execute_job.request.id} with ID matching {pattern}')
                else:
                    start_new_request = False
            else:
                start_new_request = False

    if start_new_request:
        log('initiating TF request')
        tf_request = schedule_job.request.initiate_tf_request(ctx)
        log(f'TF request filed with uuid {tf_request.uuid}')

        # generate Tf command so we can log it
        command_args, environment = schedule_job.request.generate_tf_exec_command(ctx)
        command = ' '.join(command_args)
        # hide tokens
        command = command.replace(ctx.settings.rp_token, '***')
        # export Execution to YAML so that we can report it even later
        # we won't report 'return_code' since it is not known yet
        # This is something to be implemented later
        execute_job = ExecuteJob(
            event=schedule_job.event,
            erratum=schedule_job.erratum,
            compose=schedule_job.compose,
            rog=schedule_job.rog,
            jira=schedule_job.jira,
            recipe=schedule_job.recipe,
            request=schedule_job.request,
            execution=Execution(request_uuid=tf_request.uuid,
                                request_api=tf_request.api,
                                batch_id=schedule_job.request.get_hash(ctx.timestamp),
                                command=command),
            )
        ctx.save_execute_job(execute_job)
    else:
        log(f'Re-using existing request {execute_job.request.id}')
        tf_request = TFRequest(api=execute_job.execution.request_api,
                               uuid=execute_job.execution.request_uuid)
        skip_initial_sleep = True

    if ctx.no_wait:
        log(f'Not waiting for TF request {tf_request.uuid} to finish (--no-wait set).')
        return

    # wait for TF job to finish
    finished = False
    delay = int(ctx.settings.tf_recheck_delay)
    while not finished:
        if not skip_initial_sleep:
            time.sleep(delay)
        skip_initial_sleep = False
        tf_request.fetch_details()
        if tf_request.details:
            state = tf_request.details['state']
            # if we don't know artifacts_url yet, try to get it now
            if not execute_job.execution.artifacts_url:
                try:
                    url = tf_request.details['run']['artifacts']
                    # store execute_job updated with artifacts_url
                    execute_job.execution.artifacts_url = url
                    ctx.save_execute_job(execute_job)
                except (KeyError, TypeError):
                    pass
            envs = ','.join([f"{e['os']['compose']}/{e['arch']}"
                             for e in tf_request.details['environments_requested']])
            log(f'TF request {tf_request.uuid} envs: {envs} state: {state}')
            finished = tf_request.is_finished()
        else:
            log(f'Could not read details of TF request {tf_request.uuid}')

    # this is to silence the linter, this cannot happen as the former loop cannot
    # finish without knowing request details
    if not tf_request.details:
        raise Exception(f"Failed to read details of TF request {tf_request.uuid}")
    result = tf_request.details['result']['overall'] if (
        tf_request.details['result'] and tf_request.details['state'] not in [
            'error', 'canceled']) else 'error'
    log(f'finished with result: {result}')
    # now write execution details once more
    execute_job.execution.artifacts_url = tf_request.details['run']['artifacts']
    execute_job.execution.state = state
    execute_job.execution.result = RequestResult(result)
    ctx.save_execute_job(execute_job)


def tmt_worker(ctx: CLIContext, schedule_file: Path, schedule_job: ScheduleJob) -> None:
    """Worker function for TMT execution."""
    # modify log message so it contains name of the processed file
    # so that we can distinguish individual workers
    log = partial(lambda msg: ctx.logger.info("%s: %s", schedule_file.name, msg))
    log('processing tmt request...')

    # generate tmt command so we can log it
    command_args, environment = schedule_job.request.generate_tmt_exec_command(ctx)
    command = ''
    for e, v in environment.items():
        command += f'{e}="{v}" '
    command += ' '.join(command_args)
    # hide tokens
    command = command.replace(ctx.settings.rp_token, '***')
    # export Execution to YAML so that we can report it even later
    # we won't report 'return_code' since it is not known yet
    # This is something to be implemented later
    execute_job = ExecuteJob(
        event=schedule_job.event,
        erratum=schedule_job.erratum,
        compose=schedule_job.compose,
        rog=schedule_job.rog,
        jira=schedule_job.jira,
        recipe=schedule_job.recipe,
        request=schedule_job.request,
        execution=Execution(batch_id=schedule_job.request.get_hash(ctx.timestamp),
                            command=command),
        )
    ctx.save_execute_job(execute_job)
