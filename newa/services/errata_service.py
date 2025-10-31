"""ErrataTool service integration."""

import urllib.parse
from functools import reduce
from typing import TYPE_CHECKING

try:
    from attrs import field, frozen, validators
except ModuleNotFoundError:
    from attr import field, frozen, validators

from newa.models.artifacts import Erratum, ErratumContentType
from newa.models.base import Arch
from newa.models.events import Event, EventType
from newa.utils.http import ResponseContentType, get_request, post_request
from newa.utils.parsers import NSVCParser, NVRParser

if TYPE_CHECKING:
    import logging
    from typing import Any

    from typing_extensions import TypeAlias

    ErratumId: TypeAlias = str
    JSON: TypeAlias = Any


@frozen
class ErrataTool:
    """Interface to Errata Tool instance."""

    url: str = field(validator=validators.matches_re("^https?://.+$"))

    def add_comment(self, erratum_id: str, comment: str) -> 'JSON':
        query_data: JSON = {"comment": comment}
        return post_request(
            url=urllib.parse.urljoin(
                self.url,
                f"/api/v1/erratum/{erratum_id}/add_comment"),
            json=query_data,
            krb=True,
            response_content=ResponseContentType.JSON)

    def fetch_info(self, erratum_id: str) -> 'JSON':
        from urllib.parse import quote as Q  # noqa: N812
        return get_request(
            url=urllib.parse.urljoin(
                self.url,
                f"/advisory/{Q(erratum_id)}.json"),
            krb=True,
            response_content=ResponseContentType.JSON)

    def fetch_releases(self, erratum_id: str) -> 'JSON':
        from urllib.parse import quote as Q  # noqa: N812
        return get_request(
            url=urllib.parse.urljoin(
                self.url,
                f"/advisory/{Q(erratum_id)}/builds.json"),
            krb=True,
            response_content=ResponseContentType.JSON)

    def fetch_system_info(self) -> 'JSON':
        return get_request(
            url=urllib.parse.urljoin(
                self.url,
                "/system_info.json"),
            # not using krb=True due to an authentization error/bug, we did auth already
            # krb=True,
            response_content=ResponseContentType.JSON)

    def fetch_blocking_errata(self, erratum_id: str) -> 'JSON':
        from urllib.parse import quote as Q  # noqa: N812
        return get_request(
            url=urllib.parse.urljoin(
                self.url,
                f"/errata/blocking_errata_for/{Q(erratum_id)}.json"),
            # not using krb=True due to an authentization error/bug, we did auth already
            # krb=True,
            response_content=ResponseContentType.JSON)

    def check_connection(self, et_url: str, logger: 'logging.Logger') -> None:
        try:
            et_system_info = self.fetch_system_info()
            logger.debug(f"ErrataTool system version is={et_system_info['errata_version']}")
            if not et_system_info:
                raise Exception("Could not get ErrataTool system version info.")
        except Exception as e:
            raise Exception(f"ErrataTool is not available at {et_url}.") from e

    def get_errata(self, event: Event, process_blocking_errata: bool = True) -> list[Erratum]:
        """
        Creates a list of Erratum instances based on given errata ID.

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
            # ignore EUS.EXTENSION releases
            if release.endswith('EUS.EXTENSION'):
                continue
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
                        # on purpose not using event.id since it could look like '2024:0770'
                        id=str(info_json['id']),
                        content_type=content_type,
                        respin_count=int(
                            info_json["respin_count"]),
                        revision=int(
                            info_json["revision"]),
                        summary=info_json["synopsis"],
                        release=release,
                        builds=builds,
                        blocking_builds=blocking_builds,
                        blocking_errata=[e.id for e in blocking_errata],
                        archs=Arch.architectures(list(archs)),
                        components=components,
                        url=urllib.parse.urljoin(self.url, f"/advisory/{event.id}"),
                        people_assigned_to=info_json["people"]["assigned_to"],
                        people_package_owner=info_json["people"]["package_owner"],
                        people_qe_group=info_json["people"]["qe_group"],
                        people_devel_group=info_json["people"]["devel_group"]))

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
