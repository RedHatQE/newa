"""Tests for architecture detection of RoG merge-request builds."""

import logging
from pathlib import Path
from unittest import mock

import gitlab
import pytest

from newa import Arch
from newa.cli.utils import derive_compose
from newa.services.rog_service import RoGTool

DATA = Path(__file__).parent / 'data'
MR_URL = 'https://gitlab.com/redhat/rhel/rpms/httpd/-/merge_requests/45'

# Trimmed copies of real rpmbuild-<arch>.log artifacts of a Konflux build
SKIPPED_LOG = """\
+ test localhost = localhost
+ echo 'Step skipped - architecture {arch} not selected for build'
+ exit 0
Step skipped - architecture {arch} not selected for build
Prepared artifact from /var/workdir/results
"""
BUILT_LOG = """\
Wrote: /builddir/build/SRPMS/httpd-2.2.15-71.el6_10.3.src.rpm
Wrote: /builddir/build/RPMS/httpd-2.2.15-71.el6_10.3.{arch}.rpm
"""

# Konflux table lists these; the httpd RHEL-6 build really built only some
KONFLUX_TABLE_ARCHS = [
    'x86-64', 'i686', 'aarch64', 'riscv64', 's390x', 's390', 'ppc64le', 'noarch']

# Trimmed real build_rpm trace of a Konflux build; every table row is 'Succeeded'
KONFLUX_TRACE = (DATA / 'rog-konflux-build-rpm-trace.txt').read_text()

BREW_TRACE = """\
Submitting build of bash#0123abcd, using target rhel-9.6.0-candidate, target label latest
Created task: 123456
Task info: https://brewweb.engineering.redhat.com/brew/taskinfo?taskID=123456
  123457 buildArch (bash-5.1.8-9.el9.src.rpm, x86_64): open (x86-01) -> closed
  123458 buildArch (bash-5.1.8-9.el9.src.rpm, aarch64): open (arm-01) -> closed
  123459 buildArch (bash-5.1.8-9.el9.src.rpm, s390x): open (s390-01) -> closed
  123460 buildArch (bash-5.1.8-9.el9.src.rpm, ppc64le): open (ppc-01) -> closed
  123461 buildArch (bash-5.1.8-9.el9.src.rpm, i686): open (x86-02) -> closed
123456 build (rhel-9.6.0-candidate, bash.git:0123abcd) completed successfully
"""


def _job(trace: str, built: set[str], missing: frozenset[str] = frozenset()) -> mock.Mock:
    """A fake build_rpm job serving the trace and per-arch rpmbuild logs."""
    job = mock.Mock()
    job.web_url = 'https://gitlab.com/redhat/rhel/rpms/httpd/-/jobs/1'
    job.trace.return_value = trace.encode()

    def artifact(path: str) -> bytes:
        arch = path.removeprefix('build_logs/rpmbuild-').removesuffix('.log')
        if arch in missing:
            raise gitlab.exceptions.GitlabGetError('404 Not Found', 404)
        template = BUILT_LOG if arch in built else SKIPPED_LOG
        return template.format(arch=arch).encode()

    job.artifact.side_effect = artifact
    return job


def _get_mr(job: mock.Mock):
    with mock.patch('newa.services.rog_service.gitlab.Gitlab') as gl:
        mr = gl.return_value.projects.get.return_value.mergerequests.get.return_value
        mr.title = 'Test MR'
        mr.labels = []
        tool = RoGTool(token='dummy')
        with mock.patch.object(RoGTool, 'get_mr_build_rpm_pipeline_job', return_value=job):
            return tool.get_mr(MR_URL)


class TestKonfluxArchs:
    """Konflux builds: skipped per-arch rpmbuild tasks must be ignored."""

    def test_skipped_archs_are_dropped(self):
        built = {'x86-64', 'i686', 's390x', 's390'}
        rog = _get_mr(_job(KONFLUX_TRACE, built))
        assert rog.archs == [Arch.X86_64, Arch.I686, Arch.S390X, Arch.S390]
        assert rog.build_target == 'rhel-6.10-z-draft'
        assert rog.build_task_id == '72431196'
        assert rog.builds == ['httpd-2.2.15-71.el6_10.3,draft_4161030']

    def test_only_rpmbuild_logs_are_fetched(self):
        built = {'x86-64'}
        job = _job(KONFLUX_TRACE, built)
        _get_mr(job)
        fetched = sorted(c.args[0] for c in job.artifact.call_args_list)
        assert fetched == sorted(f'build_logs/rpmbuild-{a}.log' for a in KONFLUX_TABLE_ARCHS)

    def test_noarch_only_build(self):
        built = {'noarch'}
        rog = _get_mr(_job(KONFLUX_TRACE, built))
        assert rog.archs == [Arch.NOARCH]

    def test_unavailable_log_requested_arch_kept(self, caplog):
        # artifacts may have expired; an arch the pipeline was asked to build
        # (build-architectures parameter) is kept
        built = {'x86-64'}
        with caplog.at_level(logging.WARNING, logger='newa.services.rog_service'):
            rog = _get_mr(_job(KONFLUX_TRACE, built, missing=frozenset({'s390x'})))
        assert rog.archs == [Arch.X86_64, Arch.S390X]
        assert len(caplog.records) == 1
        assert 'treating s390x as built' in caplog.records[0].getMessage()

    def test_unavailable_log_unrequested_arch_dropped(self):
        built = {'x86-64'}
        rog = _get_mr(_job(KONFLUX_TRACE, built, missing=frozenset({'ppc64le', 'noarch'})))
        assert rog.archs == [Arch.X86_64]

    def test_all_logs_unavailable_falls_back_to_requested(self):
        rog = _get_mr(_job(KONFLUX_TRACE, set(), missing=frozenset(KONFLUX_TABLE_ARCHS)))
        assert rog.archs == [Arch.X86_64, Arch.I686, Arch.S390X, Arch.S390]

    def test_unavailable_log_without_requested_list_keeps_arch(self):
        trace = KONFLUX_TRACE.replace('build-architectures', 'something-else')
        built = {'x86-64'}
        rog = _get_mr(_job(trace, built, missing=frozenset({'ppc64le'})))
        assert rog.archs == [Arch.X86_64, Arch.PPC64LE]

    @pytest.mark.parametrize('value', ['[]', '[ ]'])
    def test_empty_requested_list_means_unknown(self, value):
        # an empty build-architectures list carries no information
        trace = KONFLUX_TRACE.replace('[x86_64, s390x, s390, i686]', value)
        rog = _get_mr(_job(trace, set(), missing=frozenset({'x86-64', 'ppc64le'})))
        assert rog.archs == [Arch.X86_64, Arch.PPC64LE]

    def test_requested_list_is_normalized(self):
        trace = KONFLUX_TRACE.replace('[x86_64, s390x, s390, i686]', "['x86-64', 's390x']")
        rog = _get_mr(_job(trace, set(), missing=frozenset(KONFLUX_TABLE_ARCHS)))
        assert rog.archs == [Arch.X86_64, Arch.S390X]

    def test_str_artifact_content(self):
        job = _job(KONFLUX_TRACE, {'x86-64'})
        bytes_artifact = job.artifact.side_effect
        job.artifact.side_effect = lambda path: bytes_artifact(path).decode()
        assert _get_mr(job).archs == [Arch.X86_64]

    def test_other_artifact_errors_propagate(self):
        job = _job(KONFLUX_TRACE, {'x86-64'})
        job.artifact.side_effect = gitlab.exceptions.GitlabGetError('403 Forbidden', 403)
        with pytest.raises(gitlab.exceptions.GitlabGetError):
            _get_mr(job)

    def test_nothing_built_raises(self):
        with pytest.raises(Exception, match='Failed to parse architectures'):
            _get_mr(_job(KONFLUX_TRACE, set()))


class TestBrewArchs:
    """Brew (non-Konflux) builds keep using the buildArch task lines."""

    def test_brew_trace(self):
        job = _job(BREW_TRACE, set())
        rog = _get_mr(job)
        assert rog.archs == [
            Arch.X86_64, Arch.AARCH64, Arch.S390X, Arch.PPC64LE, Arch.I686]
        assert rog.build_task_id == '123456'
        assert rog.build_target == 'rhel-9.6.0-candidate'
        assert rog.builds == ['bash-5.1.8-9.el9']
        job.artifact.assert_not_called()


class TestComposeFiltering:
    """Built archs narrowed for the compose derived from the build target."""

    def test_rhel6_konflux_build(self):
        built = {'x86-64', 'i686', 's390x', 's390'}
        rog = _get_mr(_job(KONFLUX_TRACE, built))
        compose = derive_compose(rog.build_target)
        archs = Arch.architectures(rog.archs, compose=compose)
        assert archs == [Arch.X86_64, Arch.I386, Arch.S390X]

    def test_rhel6_noarch_build_uses_rhel6_defaults(self):
        archs = Arch.architectures([Arch.NOARCH], compose='RHEL-6-LatestUpdated')
        assert set(archs) == {Arch.X86_64, Arch.S390X, Arch.I386}

    def test_i686_dropped_without_rhel6(self):
        archs = Arch.architectures([Arch.X86_64, Arch.I686], compose='RHEL-9.6.0-Nightly')
        assert archs == [Arch.X86_64]

    def test_i686_and_i386_deduplicated(self):
        archs = Arch.architectures([Arch.I686, Arch.I386], compose='RHEL-6.10-ZStream')
        assert archs == [Arch.I386]


class TestEventCommand:
    """newa event --rog-mr stores archs filtered for the derived compose."""

    def test_process_event_rog_urls_filters_by_compose(self):
        from newa.cli.commands.event_cmd import process_event_rog_urls

        built = {'x86-64', 'i686', 's390x', 's390'}
        rog = _get_mr(_job(KONFLUX_TRACE, built))
        ctx = mock.Mock()
        ctx.settings.rog_token = 'dummy'
        ctx.should_filter_job.return_value = False
        with mock.patch('newa.cli.commands.event_cmd.RoGTool') as tool:
            tool.return_value.get_mr.return_value = rog
            process_event_rog_urls(
                ctx, [MR_URL], ['rhel-6.10-z-draft=RHEL-6-LatestUpdated'])
        saved = ctx.save_artifact_job.call_args.args[0]
        assert saved.compose.id == 'RHEL-6-LatestUpdated'
        assert saved.rog.archs == [Arch.X86_64, Arch.I386, Arch.S390X]

    def test_process_event_rog_urls_logs_compose_and_warns_on_no_archs(self):
        from newa.cli.commands.event_cmd import process_event_rog_urls

        built = {'s390'}  # not a Testing Farm architecture
        rog = _get_mr(_job(KONFLUX_TRACE, built))
        ctx = mock.Mock()
        ctx.settings.rog_token = 'dummy'
        ctx.should_filter_job.return_value = False
        with mock.patch('newa.cli.commands.event_cmd.RoGTool') as tool:
            tool.return_value.get_mr.return_value = rog
            process_event_rog_urls(
                ctx, [MR_URL], ['rhel-6.10-z-draft=RHEL-6.10-ZStream'])
        info = ctx.logger.info.call_args.args[0]
        assert 'transformed to a compose RHEL-6.10-ZStream' in info
        assert "built archs ['s390'] narrowed to []" in info
        ctx.logger.warning.assert_called_once()
        assert ctx.save_artifact_job.call_args.args[0].rog.archs == []

    def test_process_event_rog_urls_skips_empty_compose(self):
        from newa.cli.commands.event_cmd import process_event_rog_urls

        built = {'x86-64'}
        rog = _get_mr(_job(KONFLUX_TRACE, built))
        ctx = mock.Mock()
        ctx.settings.rog_token = 'dummy'
        with mock.patch('newa.cli.commands.event_cmd.RoGTool') as tool:
            tool.return_value.get_mr.return_value = rog
            process_event_rog_urls(ctx, [MR_URL], ['rhel-6.10-z-draft='])
        ctx.save_artifact_job.assert_not_called()
