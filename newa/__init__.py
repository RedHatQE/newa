from __future__ import annotations

import copy
import hashlib
import io
import itertools
import logging
import os
import re
import subprocess
import time
from collections.abc import Iterable, Iterator
from configparser import ConfigParser
from enum import Enum
from pathlib import Path
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    Literal,
    Optional,
    TypedDict,
    TypeVar,
    Union,
    cast,
    overload,
    )

import attrs
import jinja2
import jira
import jira.client
import requests
import ruamel.yaml
import ruamel.yaml.nodes
import ruamel.yaml.representer
import urllib3.response
from attrs import define, field, frozen, validators
from requests_kerberos import HTTPKerberosAuth

if TYPE_CHECKING:
    from typing import ClassVar

    from typing_extensions import Self, TypeAlias

    ErratumId: TypeAlias = str
    JSON: TypeAlias = Any


T = TypeVar('T')
SerializableT = TypeVar('SerializableT', bound='Serializable')
SettingsT = TypeVar('SettingsT', bound='Settings')


def yaml_parser() -> ruamel.yaml.YAML:
    """ Create standardized YAML parser """

    yaml = ruamel.yaml.YAML(typ='safe')

    yaml.indent(mapping=4, sequence=4, offset=2)
    yaml.default_flow_style = False
    yaml.allow_unicode = True
    yaml.encoding = 'utf-8'

    # For simpler dumping of well-known classes
    def _represent_enum(
            representer: ruamel.yaml.representer.Representer,
            data: Enum) -> ruamel.yaml.nodes.ScalarNode:
        return representer.represent_scalar('tag:yaml.org,2002:str', data.value)

    yaml.representer.add_representer(EventType, _represent_enum)

    return yaml


def default_template_environment() -> jinja2.Environment:
    """
    Create a Jinja2 environment with default settings.

    Adds common filters, and enables block trimming and left strip.
    """

    environment = jinja2.Environment()

    environment.trim_blocks = True
    environment.lstrip_blocks = True

    return environment


def render_template(
        template: str,
        environment: Optional[jinja2.Environment] = None,
        **variables: Any,
        ) -> str:
    """
    Render a template.

    :param template: template to render.
    :param environment: Jinja2 environment to use.
    :param variables: variables to pass to the template.
    """

    environment = environment or default_template_environment()

    try:
        return environment.from_string(template).render(**variables).strip()

    except jinja2.exceptions.TemplateSyntaxError as exc:
        raise Exception(
            f"Could not parse template at line {exc.lineno}.") from exc

    except jinja2.exceptions.TemplateError as exc:
        raise Exception("Could not render template.") from exc


@define
class Settings:
    """ Class storing newa settings """

    et_url: str = ''
    rp_url: str = ''
    rp_token: str = ''
    rp_project: str = ''
    jira_url: str = ''
    jira_token: str = ''
    jira_project: str = ''
    tf_token: str = ''
    tf_recheck_delay: str = ''

    def get(self, key: str, default: str = '') -> str:
        return str(getattr(self, key, default))

    @classmethod
    def load(cls: type[SettingsT], config_file: Path) -> Settings:
        cp = ConfigParser()
        cp.read(config_file)

        def _get(
                cp: ConfigParser,
                path: str,
                envvar: str,
                default: Optional[str] = '') -> str:
            section, key = path.split('/', 1)
            # first attemp to read environment variable
            env = os.environ.get(envvar, None) if envvar else None
            # then attempt to use the value from config file, use fallback value otherwise
            return env if env else cp.get(section, key, fallback=str(default))

        return Settings(
            et_url=_get(cp, 'erratatool/url', 'NEWA_ET_URL'),
            rp_url=_get(cp, 'reportportal/url', 'NEWA_REPORTPORTAL_URL'),
            rp_token=_get(cp, 'reportportal/token', 'NEWA_REPORTPORTAL_TOKEN'),
            rp_project=_get(cp, 'reportportal/project', 'NEWA_REPORTPORTAL_PROJECT'),
            jira_project=_get(cp, 'jira/project', 'NEWA_JIRA_PROJECT'),
            jira_url=_get(cp, 'jira/url', 'NEWA_JIRA_URL'),
            jira_token=_get(cp, 'jira/token', 'NEWA_JIRA_TOKEN'),
            tf_token=_get(cp, 'testingfarm/token', 'TESTING_FARM_API_TOKEN'),
            tf_recheck_delay=_get(cp, 'testingfarm/recheck_delay', 'NEWA_TF_RECHECK_DELAY', "60"),
            )


class ResponseContentType(Enum):
    TEXT = 'text'
    JSON = 'json'
    RAW = 'raw'
    BINARY = 'binary'


@overload
def get_request(
        *,
        url: str,
        krb: bool = False,
        attempts: int = 5,
        delay: int = 5,
        response_content: Literal[ResponseContentType.TEXT]) -> str:
    pass


@overload
def get_request(
        *,
        url: str,
        krb: bool = False,
        attempts: int = 5,
        delay: int = 5,
        response_content: Literal[ResponseContentType.BINARY]) -> bytes:
    pass


@overload
def get_request(
        *,
        url: str,
        krb: bool = False,
        attempts: int = 5,
        delay: int = 5,
        response_content: Literal[ResponseContentType.JSON]) -> JSON:
    pass


@overload
def get_request(
        *,
        url: str,
        krb: bool = False,
        attempts: int = 5,
        delay: int = 5,
        response_content: Literal[ResponseContentType.RAW]) -> urllib3.response.HTTPResponse:
    pass


def get_request(
        url: str,
        krb: bool = False,
        attempts: int = 5,
        delay: int = 5,
        response_content: ResponseContentType = ResponseContentType.TEXT) -> Any:
    """ Generic GET request, optionally using Kerberos authentication """
    while attempts:
        r = requests.get(url, auth=HTTPKerberosAuth(delegate=True)) if krb else requests.get(url)
        if r.status_code == 200:
            response = getattr(r, response_content.value)
            if callable(response):
                return response()
            return response
        time.sleep(delay)
        attempts -= 1

    raise Exception(f"GET request to {url} failed")


def eval_test(
        test: str,
        environment: Optional[jinja2.Environment] = None,
        **variables: Any,
        ) -> bool:
    """
    Evaluate a test expression.

    :param test: expression to evaluate. It must be a Jinja2-compatible expression.
    :param environment: Jinja2 environment to use.
    :param variables: variables to pass to the template.
    :returns: whether the expression evaluated to true-ish value.
    """

    environment = environment or default_template_environment()

    def _test_erratum(obj: Union[Event, ErratumJob]) -> bool:
        if isinstance(obj, Event):
            return obj.type_ is EventType.ERRATUM

        if isinstance(obj, ErratumJob):
            return obj.event.type_ is EventType.ERRATUM

        raise Exception(f"Unsupported type in 'erratum' test: {type(obj)}")

    def _test_match(s: str, pattern: str) -> bool:
        return re.match(pattern, s) is not None

    environment.tests['erratum'] = _test_erratum
    environment.tests['match'] = _test_match

    try:
        outcome = render_template(
            f'{{% if {test} %}}true{{% else %}}false{{% endif %}}',
            environment=environment,
            **variables,
            )

    except Exception as exc:
        raise Exception(f"Could not evaluate test '{test}'") from exc

    return bool(outcome == 'true')


class EventType(Enum):
    """ Event types """

    ERRATUM = 'erratum'


@define
class Cloneable:
    """ A class whose instances can be cloned """

    def clone(self) -> Self:
        return attrs.evolve(self)


@define
class Serializable:
    """ A class whose instances can be serialized into YAML """

    def get_hash(self, seed: str = '') -> str:
        # use only first 12 characters
        return hashlib.sha256(f'{seed}{self.to_yaml()}'.encode()).hexdigest()[:12]

    def to_yaml(self) -> str:
        output = io.StringIO()

        yaml_parser().dump(attrs.asdict(self, recurse=True), output)

        return output.getvalue()

    def to_yaml_file(self, filepath: Path) -> None:
        filepath.write_text(self.to_yaml())

    @classmethod
    def from_yaml(cls: type[SerializableT], serialized: str) -> SerializableT:
        data = yaml_parser().load(serialized)

        return cls(**data)

    @classmethod
    def from_yaml_file(cls: type[SerializableT], filepath: Path) -> SerializableT:
        return cls.from_yaml(filepath.read_text())

    @classmethod
    def from_yaml_url(cls: type[SerializableT], url: str) -> SerializableT:
        r = get_request(url=url, response_content=ResponseContentType.TEXT)
        return cls.from_yaml(r)


@define
class Event(Serializable):
    """ A triggering event of Newa pipeline """

    type_: EventType = field(converter=EventType)
    id: ErratumId


@frozen
class ErrataTool:
    """ Interface to Errata Tool instance """

    url: str = field(validator=validators.matches_re("^https?://.+$"))

    def fetch_info(self, erratum_id: str) -> JSON:
        return get_request(
            url=f"{self.url}/advisory/{erratum_id}.json",
            krb=True,
            response_content=ResponseContentType.JSON)

    def fetch_releases(self, erratum_id: str) -> JSON:
        return get_request(
            url=f"{self.url}/advisory/{erratum_id}/builds.json",
            krb=True,
            response_content=ResponseContentType.JSON)

    def get_errata(self, event: Event) -> list[Erratum]:
        """
        Creates a list of Erratum instances based on given errata ID

        Errata is split into one or more instances of an erratum. There is one
        for each release included in errata. Each errata has a single release
        set - it is either regular one or ASYNC. An errata with a regular
        release (e.g. RHEL-9.0.0.Z.EUS) will result into a single erratatum.
        On the other hand an errata with ASYNC release might result into one
        or more instances of erratum.
        """

        errata = []

        # In QE state there is are zero or more builds in an erratum, each
        # contains one or more packages, e.g.:
        # {
        #   "RHEL-9.0.0.Z.EUS": [
        #     {
        #       "scap-security-guide-0.1.72-1.el9_3": {
        #          ...
        #     }
        #   ]
        #   "RHEL-9.2.0.Z.EUS": [
        #     {
        #       "scap-security-guide-0.1.72-1.el9_3": {
        #          ...
        #     }
        #   ]
        # }

        info_json = self.fetch_info(event.id)
        releases_json = self.fetch_releases(event.id)
        for release in releases_json:
            builds = []
            builds_json = releases_json[release]
            for item in builds_json:
                builds += list(item.keys())
            if builds:
                errata.append(Erratum(id=event.id,
                                      respin_count=int(info_json["respin_count"]),
                                      summary=info_json["synopsis"],
                                      people_assigned_to=info_json["people"]["assigned_to"],
                                      release=release,
                                      builds=builds))
            else:
                raise Exception(f"No builds found in ER#{event.id}")

        return errata


@define
class InitialErratum(Serializable):
    """
    An initial erratum as an input.

    It does not track releases, just the initial event. It will be expanded
    into corresponding :py:class:`ErratumJob` instances.
    """

    event: Event = field(  # type: ignore[var-annotated]
        converter=lambda x: x if isinstance(x, Event) else Event(**x),
        )


@define
class Erratum(Cloneable, Serializable):
    """
    An eratum

    Represents a set of builds targetting a single release.
    """

    id: str = field()
    respin_count: int = field(repr=False)
    summary: str = field(repr=False)
    people_assigned_to: str = field(repr=False)
    release: str = field()
    builds: list[str] = field(factory=list)


@define
class Issue(Cloneable, Serializable):
    """ Issue - a key in Jira (eg. NEWA-123) """

    id: str = field()

    def __str__(self) -> str:
        return self.id


@define
class Recipe(Cloneable, Serializable):
    """ A job recipe """

    url: str


# A tmt context for a recipe, dimension -> value mapping.
RecipeContext = dict[str, str]

# An environment for e recipe, name -> value mapping.
RecipeEnvironment = dict[str, str]


class RawRecipeConfigDimension(TypedDict, total=False):
    context: RecipeContext
    environment: RecipeEnvironment
    compose: Optional[str]
    git_url: Optional[str]
    git_ref: Optional[str]
    when: Optional[str]


_RecipeConfigDimensionKey = Literal['context', 'environment', 'git_url', 'git_ref', 'when']


# A list of recipe config dimensions, as stored in a recipe config file.
RawRecipeConfigDimensions = dict[str, list[RawRecipeConfigDimension]]


@define
class RecipeConfig(Cloneable, Serializable):
    """ A job recipe configuration """

    fixtures: RawRecipeConfigDimension = field(
        factory=cast(Callable[[], RawRecipeConfigDimension], dict))
    dimensions: RawRecipeConfigDimensions = field(
        factory=cast(Callable[[], RawRecipeConfigDimensions], dict))

    def build_requests(self, initial_config: RawRecipeConfigDimension) -> Iterator[Request]:
        # this is here to generate unique recipe IDs
        recipe_id_gen = itertools.count(start=1)

        # get all options from dimentions
        options: list[list[RawRecipeConfigDimension]] = []
        for dimension in self.dimensions:
            options.append(self.dimensions[dimension])
        # generate combinations
        combinations = list(itertools.product(*options))
        # extend each combination with fixtures
        for i in range(len(combinations)):
            combinations[i] = (self.fixtures,) + (combinations[i])

        # Note: moved into its own function to avoid being indented too much;
        # mypy needs to be silenced because we use `key` variable instead of
        # literal keys defined in the corresponding typeddicts. And being nested
        # too much, autopep8 was reformatting and misplacing `type: ignore`.
        def _merge_key(
                dest: RawRecipeConfigDimension,
                src: RawRecipeConfigDimension,
                key: str) -> None:
            # instruct how individual attributes should be merged
            # attribute 'when' needs special treatment as we are joining conditions with 'and'
            if key == 'when' and ("when" not in dest) and src["when"]:
                dest['when'] = f'( {src["when"]} )'
            elif key == 'when' and dest["when"] and src["when"]:
                dest['when'] += f' and ( {src["when"]} )'
            elif key not in dest:
                # we need to do a deep copy so we won't corrupt the original data
                dest[key] = copy.deepcopy(src[key])  # type: ignore[literal-required]
            elif isinstance(dest[key], dict) and isinstance(src[key], dict):  # type: ignore[literal-required]
                dest[key].update(src[key])  # type: ignore[literal-required]
            elif isinstance(dest[key], list) and isinstance(src[key], list):  # type: ignore[literal-required]
                dest[key].extend(src[key])  # type: ignore[literal-required]
            else:
                raise Exception(f"Don't know how to merge record type '{key}'")

        def merge_combination_data(
                combination: tuple[RawRecipeConfigDimension, ...]) -> RawRecipeConfigDimension:
            merged = copy.deepcopy(initial_config)
            for record in combination:
                for key in record:
                    _merge_key(merged, record, key)
            return merged

        # now for each combination merge data from individual dimensions
        merged_combinations = list(map(merge_combination_data, combinations))
        # and filter them evaluating 'when' conditions
        filtered_combinations = []
        for combination in merged_combinations:
            # check if there is a condition present and evaluate it
            condition = combination.get('when', '')
            if condition:
                # we will expose COMPOSE, ENVIRONMENT, CONTEXT to evaluate a condition
                test_result = eval_test(
                    condition,
                    COMPOSE=combination['compose'],
                    ENVIRONMENT=combination['environment'],
                    CONTEXT=combination['context'])
                if not test_result:
                    continue
            filtered_combinations.append(combination)
        # now build Request instances
        total = len(filtered_combinations)
        for combination in filtered_combinations:
            yield Request(id=f'REQ-{next(recipe_id_gen)}.{total}',
                          **combination)


@define
class Request(Cloneable, Serializable):
    """ A test job request configuration """

    id: str
    context: RecipeContext = field(factory=dict)
    environment: RecipeEnvironment = field(factory=dict)
    compose: Optional[str] = None
    git_url: Optional[str] = None
    git_ref: Optional[str] = None
    tmt_path: Optional[str] = None
    plan: Optional[str] = None
    # TODO: 'when' not really needed, adding it to silent the linter
    when: Optional[str] = None

    def fetch_details(self) -> None:
        raise NotImplementedError

    def generate_tf_exec_command(self, ctx: CLIContext) -> tuple[list[str], dict[str, str]]:
        environment: dict[str, str] = {
            'NO_COLOR': 'yes',
            }
        command: list[str] = [
            'testing-farm', 'request', '--no-wait',
            ]
        rp_token = ctx.settings.rp_token
        rp_url = ctx.settings.rp_url
        if rp_token and rp_url:
            command += [
                '--tmt-environment',
                f'TMT_PLUGIN_REPORT_REPORTPORTAL_TOKEN={rp_token}',
                '--tmt-environment',
                f'TMT_PLUGIN_REPORT_REPORTPORTAL_URL={rp_url}',
                '--tmt-environment',
                'TMT_PLUGIN_REPORT_REPORTPORTAL_PROJECT=baseosqe',
                '--tmt-environment',
                'TMT_PLUGIN_REPORT_REPORTPORTAL_LAUNCH=ksrot_newa',
                '--tmt-environment',
                'TMT_PLUGIN_REPORT_REPORTPORTAL_SUITE_PER_PLAN=1',
                '--context', f'newa_batch={self.get_hash(ctx.timestamp)}',
                ]
        # check compose
        if not self.compose:
            raise Exception('ERROR: compose is not specified for the request')
        command += ['--compose', self.compose]
        # process git_url
        if not self.git_url:
            raise Exception('ERROR: git_url is not specified for the request')
        command += ['--git-url', self.git_url]
        # process git_ref
        if self.git_ref:
            command += ['--git-ref', self.git_ref]
        # process tmt_path
        if self.tmt_path:
            command += ['--path', self.tmt_path]
        # process plan
        if self.plan:
            command += ['--plan', self.plan]
        # process context
        if self.context:
            for k, v in self.context.items():
                command += ['-c', f'{k}="{v}"']
        # process environment
        if self.environment:
            for k, v in self.environment.items():
                command += ['-e', f'{k}="{v}"']

        return command, environment

    def initiate_tf_request(self, ctx: CLIContext) -> TFRequest:
        command, environment = self.generate_tf_exec_command(ctx)
        # extend current envvars with the ones from the generated command
        env = copy.deepcopy(os.environ)
        env.update(environment)
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
        r = re.search(' api [^h]*(https://[\\S]*)\x1b', output)
        if not r:
            raise Exception(f"TF request failed:\n{output}\n")
        api = r.group(1).strip()
        request_uuid = api.split('/')[-1]
        return TFRequest(api=api, uuid=request_uuid)


@define
class TFRequest(Cloneable, Serializable):
    """ A class representing plain Testing Farm request """

    api: str
    uuid: str
    details: Optional[dict[str, Any]] = None

    def fetch_details(self) -> None:
        self.details = get_request(
            url=self.api,
            response_content=ResponseContentType.JSON)


@define
class Execution(Cloneable, Serializable):
    """ A test job execution """

    return_code: Optional[int] = 0
    artifacts_url: Optional[str] = None
    batch_id: str = ''

    def fetch_details(self) -> None:
        raise NotImplementedError


@define
class Job(Cloneable, Serializable):
    """ A single job """

    event: Event = field(  # type: ignore[var-annotated]
        converter=lambda x: x if isinstance(x, Event) else Event(**x),
        )

    # jira: ...
    # recipe: ...
    # test_job: ...
    # job_result: ...

    @property
    def id(self) -> str:
        raise NotImplementedError


@define
class ErratumJob(Job):
    """ A single *erratum* job """

    erratum: Erratum = field(  # type: ignore[var-annotated]
        converter=lambda x: x if isinstance(x, Erratum) else Erratum(**x),
        )

    @property
    def id(self) -> str:
        return f'E: {self.event.id} @ {self.erratum.release}'


@define
class JiraJob(ErratumJob):
    """ A single *jira* job """

    jira: Issue = field(  # type: ignore[var-annotated]
        converter=lambda x: x if isinstance(x, Issue) else Issue(**x),
        )

    recipe: Recipe = field(  # type: ignore[var-annotated]
        converter=lambda x: x if isinstance(x, Recipe) else Recipe(**x),
        )

    @property
    def id(self) -> str:
        return f'J: {self.event.id} @ {self.erratum.release} - {self.jira.id}'


@define
class ScheduleJob(JiraJob):
    """ A single *request* to be scheduled for execution """

    request = field(  # type: ignore[var-annotated]
        converter=lambda x: x if isinstance(x, Request) else Request(**x),
        )

    @property
    def id(self) -> str:
        return f'S: {self.event.id} @ {self.erratum.release} - {self.jira.id} / {self.request.id}'


@define
class ExecuteJob(ScheduleJob):
    """ A single *request* to be scheduled for execution """

    execution = field(  # type: ignore[var-annotated]
        converter=lambda x: x if isinstance(x, Execution) else Execution(**x),
        )

    @property
    def id(self) -> str:
        return f'X: {self.event.id} @ {self.erratum.release} - {self.jira.id} / {self.request.id}'


#
# Component configuration
#


class IssueType(Enum):
    EPIC = 'epic'
    TASK = 'task'
    SUBTASK = 'subtask'


class OnRespinAction(Enum):
    # TODO: what's the default? It would simplify the class a bit.
    KEEP = 'keep'
    CLOSE = 'close'


@define
class IssueAction:  # type: ignore[no-untyped-def]
    summary: str
    description: str
    id: str
    on_respin: Optional[OnRespinAction] = field(  # type: ignore[var-annotated]
        converter=lambda value: OnRespinAction(value) if value else None)
    type: IssueType = field(converter=IssueType)
    assignee: Optional[str] = None
    parent_id: Optional[str] = None
    job_recipe: Optional[str] = None
    when: Optional[str] = None


@define
class ErratumConfig(Serializable):  # type: ignore[no-untyped-def]

    project: str = field()
    transitions: dict[str, list[str]] = field()
    issues: list[IssueAction] = field(  # type: ignore[var-annotated]
        factory=list, converter=lambda issues: [
            IssueAction(**issue) for issue in issues])

<<<<<<< HEAD

@frozen
class IssueHandler:
    """ An interface to Jira instance handling a specific ErratumJob """

    erratum_job: ErratumJob = field()
    url: str = field()
    token: str = field()
    project: str = field()

    # Each project can have different semantics of issue status.
    transitions: dict[str, list[str]] = field()

    # We assume that all projects have the following two custom fields mapped
    # as follows.
    custom_field_map: ClassVar[dict[str, str]] = {
        "field_epic_link": "customfield_12311140",
        "field_epic_name": "customfield_12311141",
        }

    # Actual Jira connection.
    connection: jira.JIRA = field(init=False)

    # Cache of Jira user names mapped to e-mail addresses.
    user_names: dict[str, str] = field(init=False, default={})

    # NEWA label
    newa_label: ClassVar[str] = "NEWA"

    @connection.default  # pyright: ignore [reportAttributeAccessIssue]
    def connection_factory(self) -> jira.JIRA:
        return jira.JIRA(self.url, token_auth=self.token)

    def newa_id(self, action: IssueAction, partial: bool = False) -> str:
        """
        NEWA identifier

        Construct so-called NEWA identifier - it identifies all issues of given
        action for errata. By default it defines issues related to the current
        respin. If 'partial' is defined it defines issues relevant for all respins.
        """

        newa_id = f"::: {IssueHandler.newa_label} {action.id}: {self.erratum_job.id}"
        if not partial:
            newa_id += f" ({', '.join(sorted(self.erratum_job.erratum.builds))}) :::"

        return newa_id

    def get_user_name(self, assignee_email: str) -> str:
        """
        Find Jira user name associated with given e-mail address

        Notice that Jira user name has various forms, it can be either an e-mail
        address or just an user name or even an user name with some sort of prefix.
        It is possible that some e-mail addresses don't have Jira user associated,
        e.g. some mailing lists. In that case empty string is returned.
        """

        if assignee_email not in self.user_names:
            assignee_names = [u.name for u in self.connection.search_users(user=assignee_email)]
            if not assignee_names:
                self.user_names[assignee_email] = ""
            elif len(assignee_names) == 1:
                self.user_names[assignee_email] = assignee_names[0]
            else:
                raise Exception(f"At most one Jira user is expected to match {assignee_email}"
                                f"({', '.join(assignee_names)})!")

        return self.user_names[assignee_email]

    def get_details(self, issue: Issue) -> jira.Issue:
        """ Return issue details """

        try:
            return self.connection.issue(issue.id)
        except jira.JIRAError as e:
            raise Exception(f"Jira issue {issue} not found!") from e

    def get_open_issues(self,
                        action: IssueAction,
                        all_respins: bool = False) -> dict[str, dict[str, str]]:
        """
        Get issues related to erratum job with given summary

        Unless 'all_respins' is defined only issues related to the current respin are returned.
        Result is a dictionary such that keys are found Jira issue keys (ID) and values
        are dictionaries such that there is always 'description' key and if the issues has
        parent then there is also 'parent' key. For instance:

        {
            "NEWA-123": {
                "description": "description of first issue",
                "parent": "NEWA-456"
            }
            "NEWA-456": {
                "description": "description of second issue"
            }
        }
        """

        fields = ["description", "parent"]

        newa_description = f"{self.newa_id(action, True) if all_respins else self.newa_id(action)}"
        query = \
            f"project = '{self.project}' AND " + \
            f"labels in ({IssueHandler.newa_label}) AND " + \
            f"description ~ '{newa_description}' AND " + \
            f"status not in ({','.join(self.transitions['closed'])})"

        search_result = self.connection.search_issues(query, fields=fields, json_result=True)
        if not isinstance(search_result, dict):
            raise Exception(f"Unexpected search result type {type(search_result)}!")

        # Transformation of search_result json into simpler structure gets rid of
        # linter warning and also makes easier mocking (for tests).
        result = {}
        for jira_issue in search_result["issues"]:
            result[jira_issue["key"]] = {"description": jira_issue["fields"]["description"]}
            if "parent" in jira_issue["fields"]:
                result[jira_issue["key"]] |= {"parent": jira_issue["fields"]["parent"]["key"]}
        return result

    def create_issue(self,
                     action: IssueAction,
                     summary: str,
                     description: str,
                     assignee_email: str | None = None,
                     parent: Issue | None = None) -> Issue:
        """ Create issue """

        data = {
            "project": {"key": self.project},
            "summary": summary,
            "description": f"{self.newa_id(action)}\n\n{description}",
            }
        if assignee_email and self.get_user_name(assignee_email):
            data |= {"assignee": {"name": self.get_user_name(assignee_email)}}

        if action.type == IssueType.EPIC:
            data |= {
                "issuetype": {"name": "Epic"},
                IssueHandler.custom_field_map["field_epic_name"]: data["summary"],
                }
        elif action.type == IssueType.TASK:
            data |= {"issuetype": {"name": "Task"}}
            if parent:
                data |= {IssueHandler.custom_field_map["field_epic_link"]: parent.id}
        elif action.type == IssueType.SUBTASK:
            if not parent:
                raise Exception("Missing task while creating sub-task!")

            data |= {
                "issuetype": {"name": "Sub-task"},
                "parent": {"key": parent.id},
                }
        else:
            raise Exception(f"Unknown issue type {action.type}!")

        try:
            jira_issue = self.connection.create_issue(data)
            jira_issue.update(
                fields={
                    "labels": [
                        *jira_issue.fields.labels,
                        IssueHandler.newa_label]})
            return Issue(jira_issue.key)
        except jira.JIRAError as e:
            raise Exception("Unable to create issue!") from e

    def refresh_issue(self, action: IssueAction, issue: Issue) -> None:
        """ Update NEWA identifier of issue """

        issue_details = self.get_details(issue)
        description = issue_details.fields.description

        # Issue does not have any NEWA ID - error.
        if isinstance(description, str) and self.newa_id(action, True) not in description:
            raise Exception(f"Issue {issue} is missing NEWA identifier!")

        # Issue has NEWA ID but not the current respin - update it.
        if isinstance(description, str) and self.newa_id(action) not in description:
            new_description = re.sub(f"^{re.escape(self.newa_id(action, partial=True))}.*\n",
                                     f"{self.newa_id(action)}\n", description)
            try:
                self.get_details(issue).update(fields={"description": new_description})
                self.comment_issue(issue, "NEWA refreshed issue ID.")
            except jira.JIRAError as e:
                raise Exception(f"Unable to modify issue {issue}!") from e

    def comment_issue(self, issue: Issue, comment: str) -> None:
        """ Add comment to issue """

        try:
            self.connection.add_comment(issue.id, comment)
        except jira.JIRAError as e:
            raise Exception(f"Unable to add a comment to issue {issue}!") from e

    def drop_obsoleted_issue(self, issue: Issue, obsoleted_by: Issue) -> None:
        """ Close obsoleted issue and link obsoleting issue to the obsoleted one """

        obsoleting_comment = f"NEWA dropped this issue (obsoleted by {obsoleted_by})."
        try:
            self.connection.create_issue_link(type="relates to",
                                              inwardIssue=issue.id,
                                              outwardIssue=obsoleted_by.id,
                                              comment={
                                                   "body": obsoleting_comment,
                                                   "visbility": None,
                                                  })
            self.connection.transition_issue(issue.id,
                                             transition=self.transitions["dropped"][0])
        except jira.JIRAError as e:
            raise Exception(f"Cannot close issue {issue}!") from e

#
# ReportPortal communication
#

@define
class ReportPortal:

    token: str
    url: str
    project: str

    def get_launch_url(self, launch_id: str) -> str:
        return f"{self.url}/ui/#{self.project}/launches/all/{launch_id}"

    def get_request(self,
                    path: str,
                    params: Optional[dict[str, str]] = None,
                    version: int = 1) -> JSON:
        base_url = f'{self.url}/api/v{version}/{self.project}/{path.lstrip("/")}'
        get_params = '&'.join([f'{k}={v}' for (k, v) in params.items()]) if params else ''
        url = f'{base_url}?{get_params}'
        headers = {"Authorization": f"bearer {self.token}", "Content-Type": "application/json"}
        req = requests.get(url, headers=headers)
        if req.status_code == 200:
            return req.json()
        return None

    def post_request(self,
                     path: str,
                     json: JSON,
                     version: int = 1) -> JSON:
        url = f'{self.url}/api/v{version}/{self.project}/{path.lstrip("/")}'
        headers = {"Authorization": f"bearer {self.token}", "Content-Type": "application/json"}
        req = requests.post(url, headers=headers, json=json)
        if req.status_code == 200:
            return req.json()
        return None

    def find_launches_by_attr(self, attr: str, value: str) -> list[str]:
        """ Searches for RP launching having the respective attribute=value set. """
        path = '/launch'
        params = {'filter.has.compositeAttribute': f'{attr}:{value}'}
        data = self.get_request(path, params)
        if not data:
            return []
        return [launch['id'] for launch in data['content']]

    def merge_launches(self,
                       launch_ids: list[str],
                       launch_name: str,
                       description: str,
                       attributes: Optional[dict[str, str]] = None) -> str:
        query_data: JSON = {
            "attributes": [],
            'description': description,
            'name': launch_name,
            "mergeType": "BASIC",
            'mode': 'DEFAULT',
            "extendSuitesDescription": 'false',
            "launches": launch_ids,
            }
        if attributes:
            for key, value in attributes.items():
                query_data['attributes'].append({"key": key.strip(), "value": value.strip()})
        print(f'Merging launches: {launch_ids}')
        data = self.post_request('/launch/merge', json=query_data)
        if data:
            print('Launches successfully merged')
            return str(data['id'])
        print('Failed to merge launches')
        return ''


@define
class CLIContext:
    """ State information about one Newa pipeline invocation """

    logger: logging.Logger
    settings: Settings
    # Path to directory with state files
    state_dirpath: Path
    timestamp: str = ''

    def enter_command(self, command: str) -> None:
        self.logger.handlers[0].formatter = logging.Formatter(
            f'[%(asctime)s] [{command.ljust(8, " ")}] %(message)s',
            )

    def load_initial_erratum(self, filepath: Path) -> InitialErratum:
        erratum = InitialErratum.from_yaml_file(filepath)

        self.logger.info(f'Discovered initial erratum {erratum.event.id} in {filepath}')

        return erratum

    def load_initial_errata(self, filename_prefix: str) -> Iterator[InitialErratum]:
        for child in self.state_dirpath.iterdir():
            if not child.name.startswith(filename_prefix):
                continue

            yield self.load_initial_erratum(self.state_dirpath / child)

    def load_erratum_job(self, filepath: Path) -> ErratumJob:
        job = ErratumJob.from_yaml_file(filepath)

        self.logger.info(f'Discovered erratum job {job.id} in {filepath}')

        return job

    def load_erratum_jobs(self, filename_prefix: str) -> Iterator[ErratumJob]:
        for child in self.state_dirpath.iterdir():
            if not child.name.startswith(filename_prefix):
                continue

            yield self.load_erratum_job(self.state_dirpath / child)

    def load_jira_job(self, filepath: Path) -> JiraJob:
        job = JiraJob.from_yaml_file(filepath)

        self.logger.info(f'Discovered jira job {job.id} in {filepath}')

        return job

    def load_jira_jobs(self, filename_prefix: str) -> Iterator[JiraJob]:
        for child in self.state_dirpath.iterdir():
            if not child.name.startswith(filename_prefix):
                continue

            yield self.load_jira_job(self.state_dirpath / child)

    def load_schedule_job(self, filepath: Path) -> ScheduleJob:
        job = ScheduleJob.from_yaml_file(filepath)

        self.logger.info(f'Discovered schedule job {job.id} in {filepath}')

        return job

    def load_schedule_jobs(self, filename_prefix: str) -> Iterator[ScheduleJob]:
        for child in self.state_dirpath.iterdir():
            if not child.name.startswith(filename_prefix):
                continue

            yield self.load_schedule_job(self.state_dirpath / child)

    def load_execute_job(self, filepath: Path) -> ExecuteJob:
        job = ExecuteJob.from_yaml_file(filepath)

        self.logger.info(f'Discovered execute job {job.id} in {filepath}')

        return job

    def load_execute_jobs(self, filename_prefix: str) -> Iterator[ExecuteJob]:
        for child in self.state_dirpath.iterdir():
            if not child.name.startswith(filename_prefix):
                continue

            yield self.load_execute_job(self.state_dirpath / child)

    def save_erratum_job(self, filename_prefix: str, job: ErratumJob) -> None:
        filepath = self.state_dirpath / \
            f'{filename_prefix}{job.event.id}-{job.erratum.release}.yaml'

        job.to_yaml_file(filepath)
        self.logger.info(f'Erratum job {job.id} written to {filepath}')

    def save_erratum_jobs(self, filename_prefix: str, jobs: Iterable[ErratumJob]) -> None:
        for job in jobs:
            self.save_erratum_job(filename_prefix, job)

    def save_jira_job(self, filename_prefix: str, job: JiraJob) -> None:
        filepath = self.state_dirpath / \
            f'{filename_prefix}{job.event.id}-{job.erratum.release}-{job.jira.id}.yaml'

        job.to_yaml_file(filepath)
        self.logger.info(f'Jira job {job.id} written to {filepath}')

    def save_schedule_job(self, filename_prefix: str, job: ScheduleJob) -> None:
        filepath = self.state_dirpath / \
            f'{filename_prefix}{job.event.id}-{job.erratum.release}-{job.jira.id}-{job.request.id}.yaml'

        job.to_yaml_file(filepath)
        self.logger.info(f'Schedule job {job.id} written to {filepath}')

    def save_execute_job(self, filename_prefix: str, job: ExecuteJob) -> None:
        filepath = self.state_dirpath / \
            f'{filename_prefix}{job.event.id}-{job.erratum.release}-{job.jira.id}-{job.request.id}.yaml'

        job.to_yaml_file(filepath)
        self.logger.info(f'Execute job {job.id} written to {filepath}')
