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
import urllib
from functools import reduce

try:
    from attrs import asdict, define, evolve, field, frozen, validators
except ModuleNotFoundError:
    from attr import asdict, define, evolve, field, frozen, validators
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
from urllib.parse import quote as Q  # noqa: N812

import jinja2
import jira
import jira.client
import requests
import ruamel.yaml
import urllib3.response
from requests_kerberos import HTTPKerberosAuth

if TYPE_CHECKING:
    from typing import ClassVar

    from typing_extensions import Self, TypeAlias

    EventId: TypeAlias = str
    ErratumId: TypeAlias = str
    ComposeId: TypeAlias = str
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
    yaml.representer.add_representer(ErratumContentType, _represent_enum)
    yaml.representer.add_representer(Arch, _represent_enum)

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


def global_request_counter() -> Iterator[int]:
    i = 1
    while True:
        yield i
        i += 1


gen_global_request_counter = global_request_counter()


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
        try:
            r = requests.get(
                url,
                auth=HTTPKerberosAuth(delegate=True),
                ) if krb else requests.get(url)
            if r.status_code == 200:
                response = getattr(r, response_content.value)
                if callable(response):
                    return response()
                return response
        except requests.exceptions.RequestException:
            # will give it another try
            pass
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

    def _test_compose(obj: Union[Event, ArtifactJob]) -> bool:
        if isinstance(obj, Event):
            return obj.type_ is EventType.COMPOSE

        if isinstance(obj, ArtifactJob):
            return obj.event.type_ is EventType.COMPOSE

        raise Exception(f"Unsupported type in 'compose' test: {type(obj)}")

    def _test_erratum(obj: Union[Event, ArtifactJob]) -> bool:
        if isinstance(obj, Event):
            return obj.type_ is EventType.ERRATUM

        if isinstance(obj, ArtifactJob):
            return obj.event.type_ is EventType.ERRATUM

        raise Exception(f"Unsupported type in 'erratum' test: {type(obj)}")

    def _test_match(s: str, pattern: str) -> bool:
        return re.match(pattern, s) is not None

    environment.tests['compose'] = _test_compose
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


def get_url_basename(url: str) -> str:
    return os.path.basename(urllib.parse.urlparse(url).path)


class EventType(Enum):
    """ Event types """

    ERRATUM = 'erratum'
    COMPOSE = 'compose'


class Arch(Enum):
    """ Available system architectures """

    X86_64 = 'x86_64'
    AARCH64 = 'aarch64'
    S390X = 's390x'
    PPC64LE = 'ppc64le'
    NOARCH = 'noarch'
    MULTI = 'multi'
    SRPMS = 'SRPMS'  # just to ease errata processing

    @classmethod
    def architectures(cls: type[Arch],
                      preset: Optional[list[Arch]] = None) -> list[Arch]:

        _exclude = [Arch.MULTI, Arch.SRPMS, Arch.NOARCH]
        _all = [Arch(a) for a in Arch.__members__.values() if a not in _exclude]

        if not preset:
            return _all
        # 'noarch' should be tested on all architectures
        if Arch('noarch') in preset:
            return _all
        # 'multi' is given for container advisories
        if Arch('multi') in preset:
            return _all
        return list(set(_all).intersection(set(preset)))


@define
class Cloneable:
    """ A class whose instances can be cloned """

    def clone(self) -> Self:
        return evolve(self)


@define
class Serializable:
    """ A class whose instances can be serialized into YAML """

    def get_hash(self, seed: str = '') -> str:
        # use only first 12 characters
        return hashlib.sha256(f'{seed}{self.to_yaml()}'.encode()).hexdigest()[:12]

    def to_yaml(self) -> str:
        output = io.StringIO()

        yaml_parser().dump(asdict(self, recurse=True), output)

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

    @classmethod
    def from_yaml_with_include(cls: type[SerializableT], location: str) -> SerializableT:

        def load_data_from_location(location: str,
                                    stack: Optional[list[str]] = None) -> dict[str, Any]:
            if stack and location in stack:
                raise Exception(f"Recursion encountered when loading YAML from {location}")
            # include location into the stack so we can detect recursion
            if stack:
                stack.append(location)
            else:
                stack = [location]
            data: dict[str, Any] = {}
            if re.search('^https?://', location):
                data = yaml_parser().load(get_request(
                    url=location,
                    response_content=ResponseContentType.TEXT))
            else:
                try:
                    data = yaml_parser().load(Path(location).read_text())
                except ruamel.yaml.error.YAMLError as e:
                    raise Exception(
                        f'Unable to load and parse YAML file from location {location}') from e

            # process 'include' attribute
            if 'include' in data:
                locations = data['include']
                # drop 'include' so it won't be processed again
                del data['include']
                for loc in locations:
                    included_data = load_data_from_location(loc, stack)
                    if included_data:
                        # explicitly join 'issues' lists first
                        if data.get('issues', []) and included_data.get('issues', []):
                            data['issues'].extend(included_data['issues'])
                        # now update data from included YAML with data from the importing YAML
                        # so that the importing data takes precedence (except 'issues' that have
                        # been joined)
                        included_data.update(data)
                        data = copy.deepcopy(included_data)

            return data

        data = load_data_from_location(location)
        return cls(**data)


@define
class Event(Serializable):
    """ A triggering event of Newa pipeline """

    type_: EventType = field(converter=EventType)
    id: EventId


@frozen
class ErrataTool:
    """ Interface to Errata Tool instance """

    url: str = field(validator=validators.matches_re("^https?://.+$"))

    def fetch_info(self, erratum_id: str) -> JSON:
        return get_request(
            url=urllib.parse.urljoin(
                self.url,
                f"/advisory/{Q(erratum_id)}.json"),
            krb=True,
            response_content=ResponseContentType.JSON)

    def fetch_releases(self, erratum_id: str) -> JSON:
        return get_request(
            url=urllib.parse.urljoin(
                self.url,
                f"/advisory/{Q(erratum_id)}/builds.json"),
            krb=True,
            response_content=ResponseContentType.JSON)

    def fetch_blocking_errata(self, erratum_id: str) -> JSON:
        return get_request(
            url=urllib.parse.urljoin(
                self.url,
                f"/errata/blocking_errata_for/{Q(erratum_id)}.json"),
            # not using krb=True due to an authentization error/bug, we did auth already
            # krb=True,
            response_content=ResponseContentType.JSON)

    def get_errata(self, event: Event, process_blocking_errata: bool = True) -> list[Erratum]:
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
        #          "BaseOS-9.3.0.Z.EUS": {
        #            "SRPMS": [...],
        #            "x86_64": [...],
        #            "ppc64le": [...],
        #          }
        #       }
        #     }
        #   ]
        #   "RHEL-9.2.0.Z.EUS": [
        #     {
        #       "scap-security-guide-0.1.72-1.el9_3": {
        #          ...
        #     }
        #   ]
        # }

        blocking_errata = []
        if process_blocking_errata:
            blocking_errata = self.get_blocking_errata(event.id)

        info_json = self.fetch_info(event.id)
        releases_json = self.fetch_releases(event.id)
        for release in releases_json:
            builds = []
            builds_json = releases_json[release]
            blocking_builds = []
            archs = set()
            for item in builds_json:
                for (build, channels) in item.items():
                    builds.append(build)
                    for channel in channels.values():
                        archs.update([Arch(a) for a in channel])
            content_type = ErratumContentType(
                info_json["content_types"][0])
            if builds:
                if content_type == ErratumContentType.MODULE:
                    nsvcs = [NSVCParser(b) for b in builds]
                    builds = [str(n) for n in nsvcs]
                    components = [f'{n.name}:{n.stream}' for n in nsvcs]
                else:
                    components = [NVRParser(build).name for build in builds]

                if blocking_errata:
                    for e in blocking_errata:
                        if release == e.release:
                            blocking_builds.extend(e.builds)

                errata.append(
                    Erratum(
                        id=event.id,
                        content_type=content_type,
                        respin_count=int(
                            info_json["respin_count"]),
                        summary=info_json["synopsis"],
                        people_assigned_to=info_json["people"]["assigned_to"],
                        release=release,
                        builds=builds,
                        blocking_builds=blocking_builds,
                        archs=Arch.architectures(list(archs)),
                        components=components,
                        url=urllib.parse.urljoin(self.url, f"/advisory/{event.id}")))
            else:
                raise Exception(f"No builds found in ER#{event.id}")

        return errata

    def get_blocking_errata(self, erratum_id: str) -> list[Erratum]:
        blockers = list(self.fetch_blocking_errata(erratum_id).keys())
        # FIXME: cowardly evaluating just the 1st level of blocking errata to avoid recursion
        errata = [self.get_errata(Event(type_=EventType.ERRATUM, id=e),
                                  process_blocking_errata=False) for e in blockers]
        if errata:
            # each get_errata() call may return a list of objects so we need
            # to turn this list of list into a single list
            return reduce(lambda l1, l2: l1 + l2, errata)
        return []


@define
class InitialErratum(Serializable):
    """
    An initial event as an input.

    It does not track releases, just the initial event. It will be expanded
    into corresponding :py:class:`ArtifactJob` instances.
    """

    event: Event = field(  # type: ignore[var-annotated]
        converter=lambda x: x if isinstance(x, Event) else Event(**x),
        )


@define
class Compose(Cloneable, Serializable):
    """
    A distribution compose

    Represents a single distribution compose.
    """

    id: ComposeId = field()


class ErratumContentType(Enum):
    """ Supported erratum content types """

    RPM = 'rpm'
    DOCKER = 'docker'
    MODULE = 'module'


@define
class Erratum(Cloneable, Serializable):  # type: ignore[no-untyped-def]
    """
    An eratum

    Represents a set of builds targetting a single release.
    """

    id: ErratumId = field()
    content_type: Optional[ErratumContentType] = field(  # type: ignore[var-annotated]
        converter=lambda value: ErratumContentType(value) if value else None)
    respin_count: int = field(repr=False)
    summary: str = field(repr=False)
    people_assigned_to: str = field(repr=False)
    release: str = field()
    url: str = field()
    archs: list[Arch] = field(factory=list,  # type: ignore[var-annotated]
                              converter=lambda arch_list: [
                                  (a if isinstance(a, Arch) else Arch(a))
                                  for a in arch_list])
    builds: list[str] = field(factory=list)
    blocking_builds: list[str] = field(factory=list)
    components: list[str] = field(factory=list)


@define
class Issue(Cloneable, Serializable):
    """ Issue - a key in Jira (eg. NEWA-123) """

    id: str = field()
    # this is used to store comment visibility restriction
    # usually JiraHandler.group takes priority but this value
    # will be used when JiraHandler is not available
    group: Optional[str] = None
    summary: Optional[str] = None
    closed: Optional[bool] = None
    url: Optional[str] = None

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


class RawRecipeTmtConfigDimension(TypedDict, total=False):
    url: Optional[str]
    ref: Optional[str]
    path: Optional[str]
    plan: Optional[str]


_RecipeTmtConfigDimensionKey = Literal['url', 'ref', 'path', 'plan']


class RawRecipeTFConfigDimension(TypedDict, total=False):
    cli_args: Optional[str]


_RecipeTFConfigDimensionKey = Literal['cli_args']


ReportPortalAttributes = dict[str, str]


class RawRecipeReportPortalConfigDimension(TypedDict, total=False):
    launch_name: Optional[str]
    launch_description: Optional[str]
    suite_description: Optional[str]
    launch_uuid: Optional[str]
    launch_url: Optional[str]
    launch_attributes: Optional[ReportPortalAttributes]


_RecipeReportPortalConfigDimensionKey = Literal['launch_name',
                                                'launch_description',
                                                'suite_description',
                                                'launch_attributes']


class RawRecipeConfigDimension(TypedDict, total=False):
    context: RecipeContext
    environment: RecipeEnvironment
    compose: Optional[str]
    arch: Optional[Arch]
    tmt: Optional[RawRecipeTmtConfigDimension]
    testingfarm: Optional[RawRecipeTFConfigDimension]
    reportportal: Optional[RawRecipeReportPortalConfigDimension]
    when: Optional[str]


_RecipeConfigDimensionKey = Literal['context', 'environment',
                                    'tmt', 'testingfarm', 'reportportal', 'when', 'arch']


# A list of recipe config dimensions, as stored in a recipe config file.
RawRecipeConfigDimensions = dict[str, list[RawRecipeConfigDimension]]


@define
class RecipeConfig(Cloneable, Serializable):
    """ A job recipe configuration """

    fixtures: RawRecipeConfigDimension = field(
        factory=cast(Callable[[], RawRecipeConfigDimension], dict))
    dimensions: RawRecipeConfigDimensions = field(
        factory=cast(Callable[[], RawRecipeConfigDimensions], dict))

    def build_requests(self, initial_config: RawRecipeConfigDimension,
                       jinja_vars: Optional[dict[str, Any]] = None) -> Iterator[Request]:
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
            elif isinstance(dest[key], str) and isinstance(src[key], str):  # type: ignore[literal-required]
                dest[key] = src[key]  # type: ignore[literal-required]
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
                compose: Optional[str] = combination.get('compose', '')
                # we will expose COMPOSE, ENVIRONMENT, CONTEXT to evaluate a condition
                arch = combination.get('arch', None)
                test_result = eval_test(
                    condition,
                    COMPOSE=Compose(compose) if compose else None,
                    ARCH=arch.value if arch else None,
                    ENVIRONMENT=combination.get('environment', None),
                    CONTEXT=combination.get('context', None),
                    **(jinja_vars if jinja_vars else {}))
                if not test_result:
                    continue
            filtered_combinations.append(combination)
        # now build Request instances
        total = len(filtered_combinations)
        for combination in filtered_combinations:
            yield Request(
                id=f'REQ-{next(recipe_id_gen)}.{total}.{next(gen_global_request_counter)}',
                **combination)


@define
class Request(Cloneable, Serializable):
    """ A test job request configuration """

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
        rp_project = ctx.settings.rp_project
        if self.reportportal:
            rp_launch = self.reportportal.get("launch_uuid", None)
        if not rp_token:
            raise Exception('ERROR: ReportPortal token is not set')
        if not rp_url:
            raise Exception('ERROR: ReportPortal URL is not set')
        if not rp_project:
            raise Exception('ERROR: ReportPortal project is not set')
        if (not self.reportportal) or (not self.reportportal['launch_name']):
            raise Exception('ERROR: ReportPortal launch name is not specified')
        command += ['--tmt-environment',
                    f'TMT_PLUGIN_REPORT_REPORTPORTAL_TOKEN="{rp_token}"',
                    '--tmt-environment',
                    f'TMT_PLUGIN_REPORT_REPORTPORTAL_URL="{rp_url}"',
                    '--tmt-environment',
                    f'TMT_PLUGIN_REPORT_REPORTPORTAL_PROJECT="{rp_project}"',
                    '--tmt-environment',
                    f"""TMT_PLUGIN_REPORT_REPORTPORTAL_UPLOAD_TO_LAUNCH='{rp_launch}'""",
                    '--tmt-environment',
                    f"""TMT_PLUGIN_REPORT_REPORTPORTAL_LAUNCH='{self.reportportal["launch_name"]}'""",
                    '--tmt-environment',
                    'TMT_PLUGIN_REPORT_REPORTPORTAL_SUITE_PER_PLAN=1',
                    '--context',
                    f'newa_batch={self.get_hash(ctx.timestamp)}',
                    ]
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
        # process Testing Farm related settings
        if self.testingfarm and self.testingfarm['cli_args']:
            command += [self.testingfarm['cli_args']]
        # process arch
        if self.arch:
            command += ['--arch', self.arch.value]
        # process reportportal configuration
        if self.reportportal and self.reportportal.get("suite_description", None):
            # we are intentionally using suite_description, not launch description
            # as due to SUITE_PER_PLAN enabled the launch description will end up
            # in suite description as well once
            # https://github.com/teemtee/tmt/issues/2990 is implemented
            command += [
                '--tmt-environment',
                f"""TMT_PLUGIN_REPORT_REPORTPORTAL_LAUNCH_DESCRIPTION='{
                    self.reportportal.get("suite_description")}'"""]
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

    batch_id: str
    state: Optional[str] = None
    result: Optional[str] = None
    request_uuid: Optional[str] = None
    request_api: Optional[str] = None
    artifacts_url: Optional[str] = None
    command: Optional[str] = None

    def fetch_details(self) -> None:
        raise NotImplementedError


@define
class EventJob(Cloneable, Serializable):
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
class NVRParser:

    nvr: str
    name: str = field(init=False)
    version: str = field(init=False)
    release: str = field(init=False)

    def __attrs_post_init__(self) -> None:
        self.name, self.version, self.release = self.nvr.rsplit("-", 2)


@define
class NSVCParser:

    nsvc: str
    name: str = field(init=False)
    stream: str = field(init=False)
    version: str = field(init=False)
    context: str = field(init=False)

    def __attrs_post_init__(self) -> None:
        self.name, self.stream, partial = self.nsvc.rsplit("-", 2)
        self.version, self.context = partial.split('.', 1)

    def __str__(self) -> str:
        return f'{self.name}:{self.stream}:{self.version}:{self.context}'


@define
class ArtifactJob(EventJob):
    """ A single *erratum* job """

    erratum: Optional[Erratum] = field(  # type: ignore[var-annotated]
        converter=lambda x: None if x is None else x if isinstance(x, Erratum) else Erratum(**x),
        )

    compose: Optional[Compose] = field(  # type: ignore[var-annotated]
        converter=lambda x: None if x is None else x if isinstance(x, Compose) else Compose(**x),
        )

    @property
    def short_id(self) -> str:
        if self.erratum:
            if self.erratum.content_type == ErratumContentType.RPM:
                return self.erratum.release
            if self.erratum.content_type == ErratumContentType.MODULE:
                return self.erratum.release
            if self.erratum.content_type == ErratumContentType.DOCKER:
                # docker type ArtifactJob is identified by the container name
                return NVRParser(self.erratum.builds[0]).name
        if self.compose:
            return self.compose.id
        return ""

    @property
    def id(self) -> str:
        return f'E: {self.event.id} @ {self.short_id}'


@define
class JiraJob(ArtifactJob):
    """ A single *jira* job """

    jira: Issue = field(  # type: ignore[var-annotated]
        converter=lambda x: x if isinstance(x, Issue) else Issue(**x),
        )

    recipe: Recipe = field(  # type: ignore[var-annotated]
        converter=lambda x: x if isinstance(x, Recipe) else Recipe(**x),
        )

    @property
    def id(self) -> str:
        return f'J: {self.event.id} @ {self.short_id} - {self.jira.id}'


@define
class ScheduleJob(JiraJob):
    """ A single *request* to be scheduled for execution """

    request = field(  # type: ignore[var-annotated]
        converter=lambda x: x if isinstance(x, Request) else Request(**x),
        )

    @property
    def id(self) -> str:
        return f'S: {self.event.id} @ {self.short_id} - {self.jira.id} / {self.request.id}'


@define
class ExecuteJob(ScheduleJob):
    """ A single *request* to be scheduled for execution """

    execution = field(  # type: ignore[var-annotated]
        converter=lambda x: x if isinstance(x, Execution) else Execution(**x),
        )

    @property
    def id(self) -> str:
        return f'X: {self.event.id} @ {self.short_id} - {self.jira.id} / {self.request.id}'


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
    type: IssueType = field(converter=IssueType)
    on_respin: OnRespinAction = field(  # type: ignore[var-annotated]
        converter=lambda value: OnRespinAction(value), default=OnRespinAction.CLOSE)
    assignee: Optional[str] = None
    parent_id: Optional[str] = None
    job_recipe: Optional[str] = None
    when: Optional[str] = None
    newa_id: Optional[str] = None
    fields: Optional[dict[str, str | float | list[str]]] = None


@define
class IssueConfig(Serializable):  # type: ignore[no-untyped-def]

    project: str = field()
    transitions: dict[str, list[str]] = field()
    issues: list[IssueAction] = field(  # type: ignore[var-annotated]
        factory=list, converter=lambda issues: [
            IssueAction(**issue) for issue in issues])
    group: Optional[str] = None


@define
class JiraField:
    id_: str
    name: str
    type_: Optional[str]
    items: Optional[str]


@frozen
class IssueHandler:
    """ An interface to Jira instance handling a specific ArtifactJob """

    artifact_job: ArtifactJob = field()
    url: str = field()
    token: str = field()
    project: str = field()

    # Each project can have different semantics of issue status.
    transitions: dict[str, list[str]] = field()

    # field name=>JiraField mapping will be obtained from Jira later
    # see https://JIRASERVER/rest/api/2/field
    field_map: ClassVar[dict[str, JiraField]] = {}

    # Actual Jira connection.
    connection: jira.JIRA = field(init=False)

    # Cache of Jira user names mapped to e-mail addresses.
    user_names: dict[str, str] = field(init=False, default={})

    # NEWA label
    newa_label: ClassVar[str] = "NEWA"
    group: Optional[str] = None

    @connection.default  # pyright: ignore [reportAttributeAccessIssue]
    def connection_factory(self) -> jira.JIRA:
        conn = jira.JIRA(self.url, token_auth=self.token)
        # try connection first
        try:
            conn.myself()
            # read field map from Jira and store its simplified version
            fields = conn.fields()
            for f in fields:
                self.field_map[f['name']] = JiraField(
                    name=f['name'],
                    id_=f['id'],
                    type_=f['schema']['type'] if 'schema' in f else None,
                    items=f['schema']['items']
                    if ('schema' in f and 'items' in f['schema'])
                    else None)
        except jira.JIRAError as e:
            raise Exception('Could not authenticate to Jira. Wrong token?') from e
        return conn

    def newa_id(self, action: IssueAction, partial: bool = False) -> str:
        """
        NEWA identifier

        Construct so-called NEWA identifier - it identifies all issues of given
        action for errata. By default it defines issues related to the current
        respin. If 'partial' is defined it defines issues relevant for all respins.
        """

        if action.newa_id:
            return f"::: {IssueHandler.newa_label} {action.newa_id}"
        newa_id = f"::: {IssueHandler.newa_label} {action.id}: {self.artifact_job.id}"
        # for ERRATUM event type update ID with sorted builds
        if (not partial and
            self.artifact_job.event.type_ is EventType.ERRATUM and
                self.artifact_job.erratum):
            newa_id += f" ({', '.join(sorted(self.artifact_job.erratum.builds))}) :::"

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

    def get_related_issues(self,
                           action: IssueAction,
                           all_respins: bool = False,
                           closed: bool = False) -> dict[str, dict[str, str]]:
        """
        Get issues related to erratum job with given summary

        Unless 'all_respins' is defined only issues related to the current respin are returned.
        Unless 'closed' is defined, only opened issues are returned.
        Result is a dictionary such that keys are found Jira issue keys (ID) and values
        are dictionaries such that there is always 'description' key and if the issues has
        parent then there is also 'parent' key. For instance:

        {
            "NEWA-123": {
                "description": "description of first issue",
                "parent": "NEWA-456"
                "status": "closed"
            }
            "NEWA-456": {
                "description": "description of second issue"
                "status": "opened"
            }
        }
        """

        fields = ["description", "parent", "status"]

        newa_description = f"{self.newa_id(action, True) if all_respins else self.newa_id(action)}"
        if closed:
            query = \
                f"project = '{self.project}' AND " + \
                f"labels in ({IssueHandler.newa_label}) AND " + \
                f"description ~ '{newa_description}'"
        else:
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
        # Additionally, double-check that the description matches since Jira tend to mess up
        # searches containing characters like underscore, space etc. and may return extra issues
        result = {}
        for jira_issue in search_result["issues"]:
            if newa_description in jira_issue["fields"]["description"]:
                result[jira_issue["key"]] = {"description": jira_issue["fields"]["description"]}
                if jira_issue["fields"]["status"]["name"] in self.transitions['closed']:
                    result[jira_issue["key"]] |= {"status": "closed"}
                else:
                    result[jira_issue["key"]] |= {"status": "opened"}
                if "parent" in jira_issue["fields"]:
                    result[jira_issue["key"]] |= {"parent": jira_issue["fields"]["parent"]["key"]}
        return result

    def create_issue(self,
                     action: IssueAction,
                     summary: str,
                     description: str,
                     assignee_email: str | None = None,
                     parent: Issue | None = None,
                     group: Optional[str] = None,
                     fields: Optional[dict[str, str | float | list[str]]] = None) -> Issue:
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
                IssueHandler.field_map["Epic Name"].id_: data["summary"],
                }
        elif action.type == IssueType.TASK:
            data |= {"issuetype": {"name": "Task"}}
            if parent:
                data |= {IssueHandler.field_map["Epic Link"].id_: parent.id}
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
            if fields is None:
                fields = {}
            # always add NEWA label to fields
            if "Labels" in fields and isinstance(fields['Labels'], list):
                fields['Labels'].append(IssueHandler.newa_label)
            else:
                fields['Labels'] = [IssueHandler.newa_label]
            # populate fdata with configuration provided by the user
            fdata: dict[str, str | float | list[Any]] = {}
            for field in fields:
                field_id = IssueHandler.field_map[field].id_
                field_type = IssueHandler.field_map[field].type_
                field_items = IssueHandler.field_map[field].items
                value = fields[field]
                # to ease processing set field_values to be always a list of strings
                if isinstance(value, (float, int, str)):
                    field_values = [str(value)]
                elif isinstance(value, list):
                    field_values = list(map(str, value))
                else:
                    raise Exception(f'Unsupported Jira field conversion for {type(value)}')
                # now we need to distinguish different types of fields and values
                if field_type == 'string':
                    fdata[field_id] = field_values[0]
                elif field_type == 'number':
                    fdata[field_id] = float(field_values[0])
                elif field_type == 'array':
                    if field_items == 'string':
                        fdata[field_id] = field_values
                    elif field_items == 'option':
                        fdata[field_id] = [{"value": v} for v in field_values]
                    else:
                        raise Exception(f'Unsupported Jira field item {field_items}')
                else:
                    raise Exception(f'Unsupported Jira field type {field_type}')

            jira_issue.update(fields=fdata)
            return Issue(jira_issue.key,
                         group=self.group,
                         summary=summary,
                         url=urllib.parse.urljoin(self.url, f'/browse/{jira_issue.key}'))
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
                self.comment_issue(
                    issue, "NEWA refreshed issue ID.")
            except jira.JIRAError as e:
                raise Exception(f"Unable to modify issue {issue}!") from e

    def comment_issue(self, issue: Issue, comment: str) -> None:
        """ Add comment to issue """

        try:
            self.connection.add_comment(
                issue.id, comment, visibility={
                    'type': 'group', 'value': self.group} if self.group else None)
        except jira.JIRAError as e:
            raise Exception(f"Unable to add a comment to issue {issue}!") from e

    def drop_obsoleted_issue(self, issue: Issue, obsoleted_by: Issue) -> None:
        """ Close obsoleted issue and link obsoleting issue to the obsoleted one """

        obsoleting_comment = f"NEWA dropped this issue (obsoleted by {obsoleted_by})."
        try:
            self.connection.create_issue_link(
                type="relates to",
                inwardIssue=issue.id,
                outwardIssue=obsoleted_by.id,
                comment={
                    "body": obsoleting_comment,
                    "visibility": {
                        'type': 'group',
                        'value': self.group} if self.group else None,
                    })
            # if the transition has a format status.resolution close with resolution
            if '.' in self.transitions["dropped"][0]:
                status, resolution = self.transitions["dropped"][0].split('.', 1)
                self.connection.transition_issue(issue.id,
                                                 transition=status,
                                                 resolution={'name': resolution})
            # otherwise close just using the status
            else:
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

    def create_launch(self,
                      launch_name: str,
                      description: str,
                      attributes: Optional[dict[str, str]] = None) -> str | None:
        query_data: JSON = {
            "attributes": [],
            "name": launch_name,
            'description': description,
            'startTime': str(int(time.time() * 1000)),
            }
        if attributes:
            for key, value in attributes.items():
                query_data['attributes'].append({"key": key.strip(), "value": value.strip()})
        data = self.post_request('/launch', json=query_data)
        if data:
            return str(data['id'])
        return None

    def finish_launch(self, launch_uuid: str, description: Optional[str] = None) -> str | None:
        query_data: JSON = {
            'endTime': str(int(time.time() * 1000)),
            "status": "PASSED",
            }
        if description:
            query_data['description'] = description
        data = self.put_request(f'/launch/{launch_uuid}/finish', json=query_data)
        if data:
            return launch_uuid
        return None

    def update_launch(self,
                      launch_uuid: str,
                      description: Optional[str] = None,
                      attributes: Optional[dict[str, str]] = None,
                      extend: bool = False) -> str | None:
        # RP API for update requires launch ID, not UUID
        info = self.get_launch_info(launch_uuid)
        if not info:
            raise Exception(
                f"Could not find launch {launch_uuid} in ReportPortal project {self.project}")
        launch_id = info['id']
        query_data: JSON = {
            "mode": "DEFAULT",
            }
        if description:
            if extend:
                query_data['description'] = f"{info['description']}<br>{description}"
            else:
                query_data['description'] = description
        if attributes:
            if extend:
                query_data['attributes'] = info['attributes']
            else:
                query_data['attributes'] = []
            for key, value in attributes.items():
                query_data['attributes'].append({"key": key.strip(), "value": value.strip()})
        data = self.put_request(f'/launch/{launch_id}/update', json=query_data, version=1)
        if data:
            return launch_uuid
        return None

    def get_launch_info(self, launch_uuid: str) -> JSON:
        return self.get_request(f'/launch/uuid/{launch_uuid}')

    def get_launch_url(self, launch_uuid: str) -> str:
        return urllib.parse.urljoin(
            self.url, f"/ui/#{Q(self.project)}/launches/all/{Q(launch_uuid)}")

    def get_request(self,
                    path: str,
                    params: Optional[dict[str, str]] = None,
                    version: int = 1) -> JSON:
        url = urllib.parse.urljoin(
            self.url,
            f'/api/v{version}/{Q(self.project)}/{Q(path.lstrip("/"))}')
        if params:
            url = f'{url}?{urllib.parse.urlencode(params)}'
        headers = {"Authorization": f"bearer {self.token}", "Content-Type": "application/json"}
        req = requests.get(url, headers=headers)
        if req.status_code == 200:
            return req.json()
        return None

    def put_request(self,
                    path: str,
                    json: JSON,
                    version: int = 1) -> JSON:
        url = urllib.parse.urljoin(
            self.url, f'/api/v{version}/{Q(self.project)}/{Q(path.lstrip("/"))}')
        headers = {"Authorization": f"bearer {self.token}", "Content-Type": "application/json"}
        req = requests.put(url, headers=headers, json=json)
        if req.status_code == 200:
            return req.json()
        return None

    def post_request(self,
                     path: str,
                     json: JSON,
                     version: int = 1) -> JSON:
        url = urllib.parse.urljoin(
            self.url,
            f'/api/v{version}/{Q(self.project)}/{Q(path.lstrip("/"))}')
        headers = {"Authorization": f"bearer {self.token}", "Content-Type": "application/json"}
        req = requests.post(url, headers=headers, json=json)
        if req.status_code in [200, 201]:
            return req.json()
        return None


@define
class CLIContext:
    """ State information about one Newa pipeline invocation """

    logger: logging.Logger
    settings: Settings
    # Path to directory with state files
    state_dirpath: Path
    cli_environment: RecipeEnvironment = field(factory=dict)
    cli_context: RecipeContext = field(factory=dict)
    timestamp: str = ''
    continue_execution: bool = False

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

            yield self.load_initial_erratum(child.resolve())

    def load_artifact_job(self, filepath: Path) -> ArtifactJob:
        job = ArtifactJob.from_yaml_file(filepath)

        self.logger.info(f'Discovered erratum job {job.id} in {filepath}')

        return job

    def load_artifact_jobs(self, filename_prefix: str) -> Iterator[ArtifactJob]:
        for child in self.state_dirpath.iterdir():
            if not child.name.startswith(filename_prefix):
                continue

            yield self.load_artifact_job(child.resolve())

    def load_jira_job(self, filepath: Path) -> JiraJob:
        job = JiraJob.from_yaml_file(filepath)

        self.logger.info(f'Discovered jira job {job.id} in {filepath}')

        return job

    def load_jira_jobs(self, filename_prefix: str) -> Iterator[JiraJob]:
        for child in self.state_dirpath.iterdir():
            if not child.name.startswith(filename_prefix):
                continue

            yield self.load_jira_job(child.resolve())

    def load_schedule_job(self, filepath: Path) -> ScheduleJob:
        job = ScheduleJob.from_yaml_file(filepath)

        self.logger.info(f'Discovered schedule job {job.id} in {filepath}')

        return job

    def load_schedule_jobs(self, filename_prefix: str) -> Iterator[ScheduleJob]:
        for child in self.state_dirpath.iterdir():
            if not child.name.startswith(filename_prefix):
                continue

            yield self.load_schedule_job(child.resolve())

    def load_execute_job(self, filepath: Path) -> ExecuteJob:
        job = ExecuteJob.from_yaml_file(filepath)

        self.logger.info(f'Discovered execute job {job.id} in {filepath}')

        return job

    def load_execute_jobs(self, filename_prefix: str) -> Iterator[ExecuteJob]:
        for child in self.state_dirpath.iterdir():
            if not child.name.startswith(filename_prefix):
                continue

            yield self.load_execute_job(child.resolve())

    def save_artifact_job(self, filename_prefix: str, job: ArtifactJob) -> None:
        filepath = self.state_dirpath / \
            f'{filename_prefix}{job.event.id}-{job.short_id}.yaml'

        job.to_yaml_file(filepath)
        self.logger.info(f'Artifact job {job.id} written to {filepath}')

    def save_artifact_jobs(self, filename_prefix: str, jobs: Iterable[ArtifactJob]) -> None:
        for job in jobs:
            self.save_artifact_job(filename_prefix, job)

    def save_jira_job(self, filename_prefix: str, job: JiraJob) -> None:
        filepath = self.state_dirpath / \
            f'{filename_prefix}{job.event.id}-{job.short_id}-{job.jira.id}.yaml'

        job.to_yaml_file(filepath)
        self.logger.info(f'Jira job {job.id} written to {filepath}')

    def save_schedule_job(self, filename_prefix: str, job: ScheduleJob) -> None:
        filepath = self.state_dirpath / \
            f'{filename_prefix}{job.event.id}-{job.short_id}-{job.jira.id}-{job.request.id}.yaml'

        job.to_yaml_file(filepath)
        self.logger.info(f'Schedule job {job.id} written to {filepath}')

    def save_execute_job(self, filename_prefix: str, job: ExecuteJob) -> None:
        filepath = self.state_dirpath / \
            f'{filename_prefix}{job.event.id}-{job.short_id}-{job.jira.id}-{job.request.id}.yaml'

        job.to_yaml_file(filepath)
        self.logger.info(f'Execute job {job.id} written to {filepath}')
