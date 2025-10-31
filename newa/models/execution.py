"""Test execution and request models."""

import copy
import os
import re
import subprocess
import sys
from collections.abc import Iterator
from typing import TYPE_CHECKING, Optional

try:
    from attrs import define, field
except ModuleNotFoundError:
    from attr import define, field

from newa.models.base import (
    TF_REQUEST_FINISHED_STATES,
    Arch,
    Cloneable,
    ExecuteHow,
    RequestResult,
    Serializable,
    )
from newa.models.recipes import (
    RawRecipeReportPortalConfigDimension,
    RawRecipeTFConfigDimension,
    RawRecipeTmtConfigDimension,
    RecipeContext,
    RecipeEnvironment,
    )
from newa.utils.http import ResponseContentType, get_request

if TYPE_CHECKING:
    from typing import Any

    from newa.models.settings import CLIContext


def global_request_counter() -> Iterator[int]:
    i = 1
    while True:
        yield i
        i += 1


gen_global_request_counter = global_request_counter()


@define
class Request(Cloneable, Serializable):
    """A test job request configuration"""

    id: str
    context: RecipeContext = field(factory=dict)
    environment: RecipeEnvironment = field(factory=dict)
    arch: Optional[Arch] = field(converter=Arch, default=Arch.X86_64)
    compose: Optional[str] = None
    tmt: Optional[RawRecipeTmtConfigDimension] = None
    testingfarm: Optional[RawRecipeTFConfigDimension] = None
    reportportal: Optional[RawRecipeReportPortalConfigDimension] = None
    # TODO: 'when' not really needed, adding it to silent the linter
    when: Optional[str] = None
    how: Optional[ExecuteHow] = field(converter=ExecuteHow, default=ExecuteHow.TESTING_FARM)

    def fetch_details(self) -> None:
        raise NotImplementedError

    def generate_tf_exec_command(self, ctx: 'CLIContext') -> tuple[list[str], dict[str, str]]:
        environment: dict[str, str] = {
            'NO_COLOR': 'yes',
            }
        command: list[str] = [
            'testing-farm', 'request', '--no-wait',
            '--context',
            f"""newa_batch={self.get_hash(ctx.timestamp)}""",
            ]
        # set ReportPortal related parameters only when reportportal attribute is not empty
        if self.reportportal:
            rp_token = ctx.settings.rp_token
            rp_url = ctx.settings.rp_url
            rp_project = ctx.settings.rp_project
            rp_test_param_filter = ctx.settings.rp_test_param_filter
            rp_launch = self.reportportal.get("launch_uuid", None)
            if not rp_token:
                raise Exception('ERROR: ReportPortal token is not set')
            if not rp_url:
                raise Exception('ERROR: ReportPortal URL is not set')
            if not rp_project:
                raise Exception('ERROR: ReportPortal project is not set')
            if not self.reportportal.get('launch_name', None):
                raise Exception('ERROR: ReportPortal launch name is not specified')
            command += ['--tmt-environment',
                        f"""'TMT_PLUGIN_REPORT_REPORTPORTAL_TOKEN="{rp_token}"'""",
                        '--tmt-environment',
                        f"""'TMT_PLUGIN_REPORT_REPORTPORTAL_URL="{rp_url}"'""",
                        '--tmt-environment',
                        f"""'TMT_PLUGIN_REPORT_REPORTPORTAL_PROJECT="{rp_project}"'""",
                        '--tmt-environment',
                        f"""'TMT_PLUGIN_REPORT_REPORTPORTAL_UPLOAD_TO_LAUNCH="{rp_launch}"'""",
                        '--tmt-environment',
                        f"""'TMT_PLUGIN_REPORT_REPORTPORTAL_LAUNCH="{
                            self.reportportal["launch_name"]}"'""",
                        '--tmt-environment',
                        """TMT_PLUGIN_REPORT_REPORTPORTAL_SUITE_PER_PLAN=1""",
                        '--context',
                        'newa_report_rp=1',
                        ]
            if self.reportportal.get("suite_description", None):
                # we are intentionally using suite_description, not launch description
                # as due to SUITE_PER_PLAN enabled the launch description will end up
                # in suite description as well once
                # https://github.com/teemtee/tmt/issues/2990 is implemented
                desc = self.reportportal.get("suite_description")
                command += [
                    '--tmt-environment',
                    f"""'TMT_PLUGIN_REPORT_REPORTPORTAL_LAUNCH_DESCRIPTION="{desc}"'"""]
            if rp_test_param_filter:
                command += ['--tmt-environment',
                            f"""'TMT_PLUGIN_REPORT_REPORTPORTAL_EXCLUDE_VARIABLES="{rp_test_param_filter}"'"""]
        # check compose
        if not self.compose:
            raise Exception('ERROR: compose is not specified for the request')
        command += ['--compose', self.compose]
        # process tmt related settings
        if not self.tmt:
            raise Exception('ERROR: tmt settings is not specified for the request')
        if not self.tmt.get("url", None):
            raise Exception('ERROR: tmt "url" is not specified for the request')
        if self.tmt['url']:
            command += ['--git-url', self.tmt['url']]
        if self.tmt.get("ref") and self.tmt['ref']:
            command += ['--git-ref', self.tmt['ref']]
        if self.tmt.get("path") and self.tmt['path']:
            command += ['--path', self.tmt['path']]
        if self.tmt.get("plan") and self.tmt['plan']:
            command += ['--plan', self.tmt['plan']]
        if self.tmt.get("plan_filter") and self.tmt['plan_filter']:
            command += ['--plan-filter', f"""'{self.tmt['plan_filter']}'"""]
        # process Testing Farm related settings
        if self.testingfarm and self.testingfarm['cli_args']:
            command += [self.testingfarm['cli_args']]
        # process arch
        if self.arch:
            command += ['--arch', self.arch.value]
        # newa request ID
        command += ['-c', f"""'newa_req="{self.id}"'"""]
        # process context
        if self.context:
            for k, v in self.context.items():
                command += ['-c', f"""'{k}="{v}"'"""]
        # process environment
        if self.environment:
            for k, v in self.environment.items():
                command += ['-e', f"""'{k}="{v}"'"""]

        return command, environment

    def initiate_tf_request(self, ctx: 'CLIContext') -> 'TFRequest':
        command, environment = self.generate_tf_exec_command(ctx)
        # extend current envvars with the ones from the generated command
        env = copy.deepcopy(os.environ)
        env.update(environment)
        # disable colors and escape control sequences
        env['NO_COLOR'] = "1"
        env['NO_TTY'] = "1"
        if not command:
            raise Exception("Failed to generate testing-farm command")
        try:
            process = subprocess.run(
                ' '.join(command),
                env=env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                shell=True)
            output = process.stdout
        except subprocess.CalledProcessError as e:
            output = e.stdout
        r = re.search('api (https://[\\S]*)', output)
        if not r:
            raise Exception(f"TF request failed:\n{output}\n")
        api = r.group(1).strip()
        request_uuid = api.split('/')[-1]
        return TFRequest(api=api, uuid=request_uuid)

    def generate_tmt_exec_command(self, ctx: 'CLIContext') -> tuple[list[str], dict[str, str]]:
        # beginning of the tmt command
        command: list[str] = ['tmt']
        # newa request ID
        command += ['-c', f'newa_req="{self.id}"']
        command += ['-c', f'newa_batch="{self.get_hash(ctx.timestamp)}"']
        # process context
        if self.context:
            for k, v in self.context.items():
                command += ['-c', f'{k}="{v}"']
        # process envvars
        environment: dict[str, str] = {}
        if self.reportportal:
            # add newa_report_rp context
            command += ['-c', 'newa_report_rp=1']
            # reportportal settings will be passed through envvars
            rp_token = ctx.settings.rp_token
            rp_url = ctx.settings.rp_url
            rp_project = ctx.settings.rp_project
            rp_test_param_filter = ctx.settings.rp_test_param_filter
            rp_launch = self.reportportal.get("launch_uuid", None)
            if not rp_token:
                raise Exception('ERROR: ReportPortal token is not set')
            if not rp_url:
                raise Exception('ERROR: ReportPortal URL is not set')
            if not rp_project:
                raise Exception('ERROR: ReportPortal project is not set')
            if (not self.reportportal) or (not self.reportportal['launch_name']):
                raise Exception('ERROR: ReportPortal launch name is not specified')
            environment.update({
                'TMT_PLUGIN_REPORT_REPORTPORTAL_TOKEN': f"{rp_token}",
                'TMT_PLUGIN_REPORT_REPORTPORTAL_URL': f"{rp_url}",
                'TMT_PLUGIN_REPORT_REPORTPORTAL_PROJECT': f"{rp_project}",
                'TMT_PLUGIN_REPORT_REPORTPORTAL_UPLOAD_TO_LAUNCH': f"{rp_launch}",
                'TMT_PLUGIN_REPORT_REPORTPORTAL_LAUNCH': f"{self.reportportal['launch_name']}",
                'TMT_PLUGIN_REPORT_REPORTPORTAL_SUITE_PER_PLAN': '1',
                })
            if self.reportportal.get("suite_description", None):
                # we are intentionally using suite_description, not launch description
                # as due to SUITE_PER_PLAN enabled the launch description will end up
                # in suite description as well once
                # https://github.com/teemtee/tmt/issues/2990 is implemented
                desc = self.reportportal.get("suite_description")
                environment['TMT_PLUGIN_REPORT_REPORTPORTAL_LAUNCH_DESCRIPTION'] = f"{desc}"
            if rp_test_param_filter:
                environment['TMT_PLUGIN_REPORT_REPORTPORTAL_EXCLUDE_VARIABLES'] = \
                    f"{rp_test_param_filter}"
        # tmt run --all
        command += ['run']
        # process environment
        if self.environment:
            for k, v in self.environment.items():
                # TMT_ variables will be passed to tmt itself
                if k.startswith('TMT_'):
                    environment[k] = v
                else:
                    command += ['-e', f'{k}="{v}"']
        # process tmt related settings
        if not self.tmt:
            raise Exception('ERROR: tmt settings is not specified for the request')
        if not self.tmt.get("url", None):
            raise Exception('ERROR: tmt "url" is not specified for the request')
        if self.tmt.get("plan", None) or self.tmt.get("plan_filter", None):
            command += ['plan']
            if self.tmt.get("plan", None):
                command += ['--name', f"""'{self.tmt["plan"]}'"""]
            if self.tmt.get("plan_filter", None):
                command += ['--filter', f"""'{self.tmt["plan_filter"]}'"""]
        # add tmt cmd args
        if self.tmt.get("cli_args", None):
            command += [f'{self.tmt["cli_args"]}']
        else:
            command += ['discover', 'provision', 'prepare', 'execute', 'report', 'finish']
        # process reportportal configuration
        return command, environment


@define
class TFRequest(Cloneable, Serializable):
    """A class representing plain Testing Farm request"""

    api: str
    uuid: str
    details: Optional[dict[str, 'Any']] = None

    def cancel(self, ctx: 'CLIContext') -> None:
        env = copy.deepcopy(os.environ)
        # disable colors and escape control sequences
        env['NO_COLOR'] = "1"
        env['NO_TTY'] = "1"
        command: list[str] = ['testing-farm', 'cancel', self.uuid]
        try:
            process = subprocess.run(
                command,
                env=env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True)
            output = process.stdout
        except subprocess.CalledProcessError as e:
            output = e.stdout
        if 'cancellation requested' in output:
            ctx.logger.info(f'Cancellation of TF request {self.uuid} requested.')
        elif 'already canceled' in output:
            ctx.logger.info(f'TF request {self.uuid} has been already cancelled.')
        elif 'already finished' in output:
            ctx.logger.info(f'TF request {self.uuid} has been already finished.')
        else:
            ctx.logger.error(f'Failed cancelling TF request {self.uuid}.')
            ctx.logger.debug(output)

    def fetch_details(self) -> None:
        self.details = get_request(
            url=self.api,
            response_content=ResponseContentType.JSON)

    def is_finished(self) -> bool:
        return bool(self.details and self.details.get('state', None) in TF_REQUEST_FINISHED_STATES)


def check_tf_cli_version(ctx: 'CLIContext') -> None:
    env = copy.deepcopy(os.environ)
    # disable colors and escape control sequences
    env['NO_COLOR'] = "1"
    env['NO_TTY'] = "1"
    command: list[str] = ['testing-farm', 'version']
    try:
        process = subprocess.run(
            ' '.join(command),
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            shell=True)
        output = process.stdout
    except subprocess.CalledProcessError as e:
        raise Exception("Cannot get testing-farm CLI version") from e
    output = output.strip()
    m = re.search(r'([0-9]+)\.([0-9]+)\.([0-9]+)', output)
    if m:
        a, b, c = map(int, m.groups())
    else:
        raise Exception(f"Cannot get testing-farm CLI version, got '{output}'.")
    # versions 0.0.20 and lower are too old
    if a == 0 and b == 0 and c <= 20:
        ctx.logger.error(f"testing-farm CLI version {a}.{b}.{c} is too old, please update.")
        sys.exit(1)


@define
class Execution(Cloneable, Serializable):  # type: ignore [no-untyped-def]
    """A test job execution"""

    batch_id: str
    state: Optional[str] = None
    result: Optional[RequestResult] = field(  # type: ignore[var-annotated]
        converter=lambda x: RequestResult.NONE if not x else x if isinstance(
            x, RequestResult) else RequestResult(x), default=RequestResult.NONE)
    request_uuid: Optional[str] = None
    request_api: Optional[str] = None
    artifacts_url: Optional[str] = None
    command: Optional[str] = None

    def fetch_details(self) -> None:
        raise NotImplementedError
