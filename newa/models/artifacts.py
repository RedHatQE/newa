"""Artifact-related models (Erratum, Compose, RoG)."""

import re
from enum import Enum
from typing import TYPE_CHECKING, Optional

try:
    from attrs import define, field
except ModuleNotFoundError:
    from attr import define, field

from newa.models.base import Arch, Cloneable, Serializable

if TYPE_CHECKING:
    from typing_extensions import TypeAlias

    ComposeId: TypeAlias = str
    ErratumId: TypeAlias = str
    EventId: TypeAlias = str


UNDEFINED_COMPOSE = '_undefined_'


class ErratumContentType(Enum):
    """Supported erratum content types."""

    RPM = 'rpm'
    DOCKER = 'docker'
    MODULE = 'module'


@define
class Compose(Cloneable, Serializable):
    """
    A distribution compose.

    Represents a single distribution compose.
    """

    id: 'ComposeId' = field()

    @property
    def prev_minor(self) -> 'ComposeId':
        r = re.match(r'^RHEL-([0-9]+)\.([0-9]+)', self.id)
        if r:
            major, minor = map(int, r.groups())
            if major in {8, 9} and minor > 0:
                return f'RHEL-{major}.{minor - 1}.0-Nightly'
            if major == 10 and minor > 0:
                return f'RHEL-{major}.{minor - 1}-Nightly'
        return UNDEFINED_COMPOSE

    @property
    def prev_major(self) -> 'ComposeId':
        r = re.match(r'^(Fedora|RHEL)-([0-9]+)', self.id)
        if r:
            distro = r.group(1)
            major = int(r.group(2))
            if distro == 'RHEL':
                if major == 8:
                    return 'RHEL-7-LatestUpdated'
                if major in {9, 10}:
                    return f'RHEL-{major - 1}-Nightly'
            if distro == 'Fedora' and major > 36:
                return f'Fedora-{major - 1}-Updated'
        return UNDEFINED_COMPOSE


@define
class Erratum(Cloneable, Serializable):  # type: ignore[no-untyped-def]
    """
    An eratum.

    Represents a set of builds targetting a single release.
    """

    id: 'ErratumId' = field()
    content_type: Optional[ErratumContentType] = field(  # type: ignore[var-annotated]
        converter=lambda value: ErratumContentType(value) if value else None)
    respin_count: int = field(repr=False)
    summary: str = field(repr=False)
    release: str = field()
    url: str = field()
    archs: list[Arch] = field(factory=list,  # type: ignore[var-annotated]
                              converter=lambda arch_list: [
                                  (a if isinstance(a, Arch) else Arch(a))
                                  for a in arch_list])
    builds: list[str] = field(factory=list)
    blocking_builds: list[str] = field(factory=list)
    blocking_errata: list['ErratumId'] = field(factory=list)
    components: list[str] = field(factory=list)
    people_assigned_to: Optional[str] = None
    people_package_owner: Optional[str] = None
    people_qe_group: Optional[str] = None
    people_devel_group: Optional[str] = None
    revision: Optional[int] = field(repr=False, default=0)


@define
class RoG(Cloneable, Serializable):  # type: ignore[no-untyped-def]
    """
    A RoG merge-request.

    Represents a merge-request associated with a particular Brew taskID.
    """

    id: 'EventId' = field()
    content_type: Optional[ErratumContentType] = field(  # type: ignore[var-annotated]
        converter=lambda value: ErratumContentType(value) if value else None)
    # respin_count: int = field(repr=False)
    title: str = field(repr=False)
    build_task_id: str = field()
    build_target: str = field()
    archs: list[Arch] = field(factory=list,  # type: ignore[var-annotated]
                              converter=lambda arch_list: [
                                  (a if isinstance(a, Arch) else Arch(a))
                                  for a in arch_list])
    builds: list[str] = field(factory=list)
    components: list[str] = field(factory=list)
