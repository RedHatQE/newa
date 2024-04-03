from __future__ import annotations

import io
import os
import re
import time
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional, TypeVar, Union

import attrs
import jinja2
import requests
import ruamel.yaml
import ruamel.yaml.nodes
import ruamel.yaml.representer
from attrs import define, field, frozen, validators
from requests_kerberos import HTTPKerberosAuth

if TYPE_CHECKING:
    from typing_extensions import Self, TypeAlias

    ErratumId: TypeAlias = str


T = TypeVar('T')
SerializableT = TypeVar('SerializableT', bound='Serializable')


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


def krb_get_request(url: str, attempts: int = 5, delay: int = 5) -> Any:
    """ Generic GET request using Kerberos authentication """

    while attempts:
        r = requests.get(url, auth=HTTPKerberosAuth(delegate=True))
        if r.status_code == 200:
            return r.json()
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

    # TODO: Until we have a dedicated newa config, we'll setup ET instance
    # url via environment variable NEWA_ET_URL
    url: str = field(validator=validators.matches_re("^https?://.+$"))

    @url.default  # pyright: ignore [reportAttributeAccessIssue]
    def _url_factory(self) -> str:
        if "NEWA_ET_URL" in os.environ:
            return os.environ["NEWA_ET_URL"]
        raise Exception("NEWA_ET_URL envvar is required.")

    # TODO: Not used at this point because we only consume builds now
    def fetch_info(self, erratum_id: str) -> Any:
        return krb_get_request(f"{self.url}/advisory/{erratum_id}.json")

    def fetch_releases(self, erratum_id: str) -> Any:
        return krb_get_request(f"{self.url}/advisory/{erratum_id}/builds.json")


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

    @classmethod
    def from_errata_tool(cls, event: Event) -> list[Erratum]:
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

        releases_json = ErrataTool().fetch_releases(event.id)
        for release in releases_json:
            builds = []
            builds_json = releases_json[release]
            for item in builds_json:
                builds += list(item.keys())
            if builds:
                errata.append(cls(release, builds))
            else:
                raise Exception(f"No builds found in ER#{event.id}")

        return errata


@define
class Issue(Cloneable, Serializable):
    """ A Jira issue """

    id: str

    def fetch_details(self) -> None:
        raise NotImplementedError


@define
class Recipe(Cloneable, Serializable):
    """ A job recipe """

    url: str


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
        return f'{self.event.id} @ {self.erratum.release}'


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
        return f'{self.event.id} @ {self.erratum.release}'


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


@define
class ErratumConfig(Serializable):
    issues: list[IssueAction] = field(  # type: ignore[var-annotated]
        factory=list, converter=lambda issues: [
            IssueAction(**issue) for issue in issues])
