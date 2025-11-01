"""Job models for different stages of the pipeline."""

from typing import Optional

try:
    from attrs import define, field
except ModuleNotFoundError:
    from attr import define, field

from newa.models.artifacts import Compose, Erratum, ErratumContentType, RoG
from newa.models.base import Cloneable, Serializable
from newa.models.events import Event
from newa.models.execution import Execution, Request
from newa.models.issues import Issue
from newa.models.recipes import Recipe
from newa.utils.parsers import NVRParser


@define
class EventJob(Cloneable, Serializable):
    """A single job"""

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


@define(kw_only=True)
class ArtifactJob(EventJob):
    """A single *erratum* job"""

    erratum: Optional[Erratum] = field(  # type: ignore[var-annotated]
        converter=lambda x: None if x is None else x if isinstance(x, Erratum) else Erratum(**x),
        )

    compose: Optional[Compose] = field(  # type: ignore[var-annotated]
        converter=lambda x: None if x is None else x if isinstance(x, Compose) else Compose(**x),
        )

    rog: Optional[RoG] = field(  # type: ignore[var-annotated]
        converter=lambda x: None if x is None else x if isinstance(x, RoG) else RoG(**x),
        default=None,
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
        elif self.compose:
            return self.compose.id
        return ""

    @property
    def id(self) -> str:
        return f'E: {self.event.short_id} @ {self.short_id}'


@define(kw_only=True)
class JiraJob(ArtifactJob):
    """A single *jira* job"""

    jira: Issue = field(  # type: ignore[var-annotated]
        converter=lambda x: x if isinstance(x, Issue) else Issue(**x),
        )

    recipe: Recipe = field(  # type: ignore[var-annotated]
        converter=lambda x: x if isinstance(x, Recipe) else Recipe(**x),
        )

    @property
    def id(self) -> str:
        return f'J: {self.event.short_id} @ {self.short_id} - {self.jira.id}'


@define(kw_only=True)
class ScheduleJob(JiraJob):
    """A single *request* to be scheduled for execution"""

    request = field(  # type: ignore[var-annotated]
        converter=lambda x: x if isinstance(x, Request) else Request(**x),
        )

    @property
    def id(self) -> str:
        return f'S: {self.event.short_id} @ {self.short_id} - {self.jira.id} / {self.request.id}'


@define(kw_only=True)
class ExecuteJob(ScheduleJob):
    """A single *request* to be scheduled for execution"""

    execution = field(  # type: ignore[var-annotated]
        converter=lambda x: x if isinstance(x, Execution) else Execution(**x),
        )

    @property
    def id(self) -> str:
        return f'X: {self.event.short_id} @ {self.short_id} - {self.jira.id} / {self.request.id}'
