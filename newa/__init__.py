from __future__ import annotations

import copy
import io
import itertools
import os
import re
import subprocess
import time
from collections.abc import Iterator
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
import requests
import ruamel.yaml
import ruamel.yaml.nodes
import ruamel.yaml.representer
import urllib3.response
from attrs import define, field, frozen, validators
from requests_kerberos import HTTPKerberosAuth

if TYPE_CHECKING:
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
    """
    Interface to Errata Tool instance

    Its only purpose is to serve information about an erratum and its builds.
    """

    url: str = field(validator=validators.matches_re("^https?://.+$"))

    # TODO: Not used at this point because we only consume builds now
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

        # TODO: We might need to assert QE (or later) state. There is no point
        # in fetching errata in NEW_FILES where builds need not to be present.

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

        releases_json = self.fetch_releases(event.id)
        for release in releases_json:
            builds = []
            builds_json = releases_json[release]
            for item in builds_json:
                builds += list(item.keys())
            if builds:
                errata.append(Erratum(release, builds))
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

    # TODO: We might need to add more in the future.
    release: str
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
        for combination in merged_combinations:
            # before creating a Request check if there is a condition present and evaluate it
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
            yield Request(id=f'REQ-{next(recipe_id_gen)}', **combination)


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

    def generate_tf_exec_command(self) -> tuple[list[str], dict[str, str]]:
        environment: dict[str, str] = {
            'NO_COLOR': 'yes',
            }
        command: list[str] = [
            'testing-farm', 'request', '--no-wait',
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

    def initiate_tf_request(self) -> TFRequest:
        command, environment = self.generate_tf_exec_command()
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

    return_code: Optional[int] = None
    artifacts_url: Optional[str] = None

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
    assignee: str
    id: str
    on_respin: Optional[OnRespinAction] = field(  # type: ignore[var-annotated]
        converter=lambda value: OnRespinAction(value) if value else None)
    type: IssueType = field(converter=IssueType)
    parent_id: Optional[str] = None
    job_recipe: Optional[str] = None
    when: Optional[str] = None


@define
class ErratumConfig(Serializable):
    issues: list[IssueAction] = field(  # type: ignore[var-annotated]
        factory=list, converter=lambda issues: [
            IssueAction(**issue) for issue in issues])
