"""Event-related models."""

from enum import Enum
from typing import TYPE_CHECKING

try:
    from attrs import define, field
except ModuleNotFoundError:
    from attr import define, field

from newa.models.base import Serializable

if TYPE_CHECKING:
    from typing import TypeAlias

    EventId: TypeAlias = str


class EventType(Enum):
    """Event types."""

    ERRATUM = 'erratum'
    COMPOSE = 'compose'
    ROG = 'rog'
    JIRA = 'jira'


@define
class Event(Serializable):
    """A triggering event of Newa pipeline."""

    type_: EventType = field(converter=EventType)
    id: 'EventId'

    @property
    def short_id(self) -> str:
        if self.id.startswith('https://gitlab.com'):
            parts = self.id.strip('/').split('/')
            # format: {COMPONENT}_MR_{NUMBER}
            return f"{parts[-4]}_MR_{parts[-1]}"
        return self.id


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
