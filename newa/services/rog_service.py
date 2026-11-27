"""RoG (GitLab) service integration."""

import logging
import re
from typing import TYPE_CHECKING, Optional

import gitlab

try:
    from attrs import field, frozen
except ModuleNotFoundError:
    from attr import field, frozen

from newa.models.artifacts import ErratumContentType, RoG
from newa.models.base import Arch
from newa.utils.parsers import NVRParser

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


def _extract_build(text: str) -> str:
    """Extract a build name from the source side of a 'source -> target' line.

    Ignores any markdown decoration around the build name (bullets, bold
    labels, links): when the text contains a markdown link, its label is
    used; otherwise the last whitespace-separated token is used, stripped
    of markdown decoration characters.
    """
    # a markdown link label, e.g. "[build](url)"
    match = re.search(r'\[([^\[\]]+)\]', text)
    if match:
        return match.group(1).strip('*` ').strip()
    tokens = text.split()
    if not tokens:
        return ''
    return tokens[-1].strip('*`').strip()


@frozen
class RoGTool:
    """Interface to RoG instance."""

    token: str = field()
    url: str = 'https://gitlab.com'
    # actual GitLab connection.
    connection: gitlab.Gitlab = field(init=False)

    @connection.default
    def connection_factory(self) -> gitlab.Gitlab:
        return gitlab.Gitlab(self.url, private_token=self.token)

    def add_comment(self, mr_url: str, comment: str) -> None:
        """Add a private/internal comment to a GitLab merge request.

        Args:
            mr_url: The URL of the merge request
            comment: The comment text to add
        """
        (project, number) = self.parse_mr_project_and_number(mr_url)
        gp = self.connection.projects.get(project)
        gm = gp.mergerequests.get(number)
        gm.notes.create({'body': comment, 'internal': True})

    def parse_mr_project_and_number(self, url: str) -> tuple[str, str]:
        if not url.startswith(self.url):
            raise Exception(f'Merge-request URL "{url}" does not start with "{self.url}"')
        r = re.match(f'^{re.escape(self.url)}/(.*)/-/merge_requests/([0-9]+)', url)
        if not r:
            raise Exception(f'Failed parsing project from MR "{url}", incorrect URL?')
        project = r.group(1)
        number = r.group(2)
        return (project, number)

    def get_mr_build_rpm_pipeline_job(self, url: str) -> 'gitlab.v4.objects.ProjectJob':
        (project, number) = self.parse_mr_project_and_number(url)
        # get project object
        gp = self.connection.projects.get(project)
        # git merge request object
        gm = gp.mergerequests.get(number)
        # get the 2 latest pipelines
        pipelines = gm.pipelines.list(order_by='id', sort='desc', per_page=2, get_all=False)
        # for unmerged mr choose the latest (1st) pipeline, otherwise the previous one (2nd one)
        if len(pipelines) >= 2 and gm.state == 'merged':
            pipeline = pipelines[1]
        elif len(pipelines) >= 1 and gm.state != 'merged':
            pipeline = pipelines[0]
        else:
            raise Exception(f'Failed to identify proper pipeline from MR "{url}".')
        # find the build_rpm job
        gpi = gp.pipelines.get(pipeline.id)
        job = None
        for j in gpi.jobs.list():
            if j.name == 'build_rpm':
                job = j
                break
        if not job:
            raise Exception(f'Failed to find pipeline job "build_rpm" in pipeline "{gpi.web_url}"')
        # return job
        return gp.jobs.get(job.id)

    @staticmethod
    def _konflux_arch_skipped(
            job: 'gitlab.v4.objects.ProjectJob',
            arch: str) -> Optional[bool]:
        """Tell whether a Konflux per-arch rpmbuild task was skipped.

        Reads the ``build_logs/rpmbuild-<arch>.log`` artifact of the job.
        Returns ``None`` when the artifact does not exist (e.g. it has expired),
        other GitLab errors are propagated.
        """
        try:
            content = job.artifact(f'build_logs/rpmbuild-{arch}.log')
        except gitlab.exceptions.GitlabGetError as e:
            if e.response_code == 404:
                return None
            raise
        if isinstance(content, bytes):
            content = content.decode('utf-8', errors='replace')
        if not isinstance(content, str):
            return None
        return 'Step skipped - architecture' in content

    def get_mr_promoted_builds(
            self, gm: 'gitlab.v4.objects.ProjectMergeRequest') -> set[str]:
        """Extract promoted builds from OSCI Bot comments on a merge request.

        Once a merge request is merged and its build is promoted, OSCI Bot
        leaves a comment similar to this one:

            Promoted Build(s)

                rhel: opencryptoki-3.27.0-1.el10_3.1,draft_4170018
                    -> opencryptoki-3.27.0-1.el10_3.1

        The draft build gets renamed, the ',draft_N' suffix is dropped.
        Returns the set of draft builds that were promoted. The promoted
        name itself is intentionally not taken from the comment - callers
        should derive it locally by stripping the draft suffix.
        """
        promotions: set[str] = set()
        try:
            notes = gm.notes.list(get_all=True)
        except Exception as error:
            # promotion data is an optional enhancement, do not let a
            # transient failure to read MR notes abort event processing,
            # fall back to the build parsed from the pipeline log
            logging.warning(
                'Failed to read notes from MR %s (%s), ignoring possible build promotions',
                gm.web_url, error)
            return promotions
        for note in notes:
            body = getattr(note, 'body', '') or ''
            if 'Promoted Build' not in body:
                continue
            logging.debug('Found promotion comment on MR %s', gm.web_url)
            # limit parsing to the "Promoted Build(s)" section only, so that
            # other sections of the comment (e.g. "Moving Build(s)" renaming
            # build targets) are not mistaken for build promotions
            split = re.split(r'Promoted Build\(s\)', body, maxsplit=1)
            if len(split) < 2:
                continue
            section = re.split(r'Moving Build', split[1], maxsplit=1)[0]
            # the section consists of "source -> target" lines; do not rely
            # on the exact markdown decoration (bullets, bold labels,
            # links...) around the build names, just look for the build
            # tokens themselves
            for line in section.splitlines():
                if '->' not in line:
                    continue
                (left, _, right) = line.partition('->')
                source = _extract_build(left)
                if source:
                    promotions.add(source)
        logging.debug('Promoted builds found on MR %s: %s', gm.web_url, promotions)
        return promotions

    def get_mr(self, url: str) -> RoG:
        (project, number) = self.parse_mr_project_and_number(url)
        # get project object
        gp = self.connection.projects.get(project)
        # git merge request object
        gm = gp.mergerequests.get(number)
        title = gm.title
        # MR labels, exposed in the event metadata for downstream tooling
        labels = list(gm.labels or [])
        # get job log
        job = self.get_mr_build_rpm_pipeline_job(url)
        log = job.trace().decode("utf-8")
        konflux_build = bool(re.search(
            'Preparing Konflux build \\(click to expand\\)', log))
        if konflux_build:
            r = re.search(r'Build imported as https://.*taskID=([0-9]+)', log)
        else:
            r = re.search(r'Created task: ([0-9]+)', log)
        # parse task id
        if not r:
            raise Exception(f'Failed to parse build Task ID from "build_rpm" job "{job.web_url}"')
        task_id = r.group(1)
        # parse build target
        r = re.search(r'using target (rhel.*?),', log)
        if not r:
            raise Exception(f'Failed to parse build target from "build_rpm" job "{job.web_url}"')
        target = r.group(1)
        # parse architectures
        if konflux_build:
            build_reqs = re.findall(
                r'(rpmbuild)-(\S+).*Succeeded', log)
        else:
            # try Brew build approach
            build_reqs = re.findall(
                r'buildArch \((.*?.src.rpm), (.*?)\): open.*-> closed', log)
        if not build_reqs:
            raise Exception(
                f'Failed to parse SRPM and build tasks from "build_rpm" job "{job.web_url}"')
        # architectures the Konflux pipeline was asked to build for, used when
        # the per-arch rpmbuild log is not available; None when unknown
        requested_archs: Optional[list[str]] = None
        requested = re.search(
            r'- name: build-architectures\s*\n\s*value: \[([^\]]*)\]', log)
        if requested:
            requested_archs = [
                a.strip().strip('\'"').replace('-', '_')
                for a in requested.group(1).split(',') if a.strip()] or None
        archs: list[Arch] = []
        srpm = ''
        build = ''
        for r in build_reqs:
            # for Brew build read SRPM right away
            if not konflux_build:
                srpm = r[0]
            # replace x86-64 with x86_64
            arch_name = r[1].replace('-', '_')
            # the Konflux task status table lists every per-arch rpmbuild
            # task as Succeeded, including those skipped for arches not
            # selected for the build; consult the task log to tell them apart
            if konflux_build:
                skipped = self._konflux_arch_skipped(job, r[1])
                if skipped is None:
                    skipped = (requested_archs is not None
                               and arch_name not in requested_archs)
                    logger.warning(
                        f'Log of rpmbuild-{r[1]} is not available in "build_rpm" '
                        f'job "{job.web_url}", treating {arch_name} as '
                        f'{"skipped" if skipped else "built"}')
                elif skipped:
                    logger.debug(f'Konflux task rpmbuild-{r[1]} was skipped')
                if skipped:
                    continue
            arch = Arch(arch_name)
            if arch not in archs:
                archs.append(arch)
        if not archs:
            raise Exception(
                f'Failed to parse architectures from "build_rpm" job "{job.web_url}"')
        if konflux_build:
            # parse NVR from the pipeline log
            # it is mentioned multiple times in the log and we need its last occurence
            nvr_matches = re.findall(r'DRAFT_BUILD_NVR=(\S+)', log)
            if nvr_matches:
                build = nvr_matches[-1]
        else:
            # parse NVR and component from SRPM
            build = srpm.replace('.src.rpm', '')
        if not build:
            raise Exception(
                f'Failed to parse build NVR from "build_rpm" job "{job.web_url}"')
        # once the MR is merged and the build is promoted, the ',draft_N'
        # suffix is dropped; OSCI Bot leaves a comment about the promotion.
        # The comment is used only as a signal that the promotion happened -
        # the promoted build name is derived locally by stripping the
        # draft suffix, so that a forged comment cannot introduce an
        # arbitrary build name
        promotions = self.get_mr_promoted_builds(gm)
        if build in promotions:
            promoted_build = re.sub(r',draft_[0-9]+$', '', build)
            if promoted_build != build:
                logging.debug('Using promoted build "%s" instead of "%s"',
                              promoted_build, build)
                build = promoted_build
        nvr = NVRParser(build)
        builds = [build]
        components = [nvr.name]
        # Architectures are returned as built; release-specific filtering
        # (Arch.architectures) needs the Testing Farm compose and is done by
        # the caller once the compose has been derived from the build target.
        return RoG(
            id=url,
            content_type=ErratumContentType.RPM,
            title=title,
            build_task_id=task_id,
            build_target=target,
            archs=archs,
            builds=builds,
            components=components,
            labels=labels)
