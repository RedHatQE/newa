"""RoG (GitLab) service integration."""

import re
from typing import TYPE_CHECKING

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

    def get_mr(self, url: str) -> RoG:
        (project, number) = self.parse_mr_project_and_number(url)
        # get project object
        gp = self.connection.projects.get(project)
        # git merge request object
        gm = gp.mergerequests.get(number)
        title = gm.title
        # get job log
        job = self.get_mr_build_rpm_pipeline_job(url)
        log = job.trace().decode("utf-8")
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
        build_reqs = re.findall(
            r'buildArch \((.*?.src.rpm), (.*?)\): open.*-> closed', log)
        if not build_reqs:
            raise Exception(
                r'Failed to parse SRPM and build tasks from "build_rpm" job "{job.web_url}"')
        archs = []
        for r in build_reqs:
            if r:
                archs.append(Arch(r[1]))
                srpm = r[0]
        if not archs:
            raise Exception(
                r'Failed to parse SRPM and build tasks from "build_rpm" job "{job.web_url}"')
        # parse NVR and component from SRPM
        build = srpm.replace('.src.rpm', '')
        nvr = NVRParser(build)
        builds = [build]
        components = [nvr.name]
        return RoG(
            id=url,
            content_type=ErratumContentType.RPM,
            title=title,
            build_task_id=task_id,
            build_target=target,
            archs=Arch.architectures(archs),
            builds=builds,
            components=components)
