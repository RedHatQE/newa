import io
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, TypeVar

import attrs
import ruamel.yaml
import ruamel.yaml.nodes
import ruamel.yaml.representer
from attrs import define, field

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


class EventType(Enum):
    """ Event types """

    ERRATUM = 'erratum'


@define
class Cloneable:
    """ A class whose instances can be cloned """

    def clone(self) -> 'Self':
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
    id: 'ErratumId'


@define
class Erratum(Cloneable, Serializable):
    """ An eratum """

    release: str
    # builds: list[...] = ...

    def fetch_details(self) -> None:
        raise NotImplementedError


@define
class Job(Cloneable, Serializable):
    """ A single job """

    event: Event = field(  # type: ignore[var-annotated]
        converter=lambda x: x if isinstance(x, Event) else Event(**x),
        )

    # issue: ...
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
