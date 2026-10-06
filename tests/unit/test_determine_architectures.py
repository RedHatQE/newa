"""Tests for architecture resolution and arch-based skipping in scheduling."""

from types import SimpleNamespace
from unittest import mock

import pytest

from newa import Arch
from newa.cli.schedule_helpers import _determine_architectures, _process_jira_job

DEFAULT_ARCHS = [Arch.X86_64, Arch.S390X, Arch.PPC64LE, Arch.AARCH64]


def _erratum_job(archs):
    """A minimal jira_job stand-in carrying an erratum with the given archs."""
    return SimpleNamespace(erratum=SimpleNamespace(archs=archs), rog=None)


def _rog_job(archs):
    """A minimal jira_job stand-in carrying a RoG MR with the given archs."""
    return SimpleNamespace(erratum=None, rog=SimpleNamespace(archs=archs))


def _plain_job():
    """A jira_job with neither erratum nor RoG metadata."""
    return SimpleNamespace(erratum=None, rog=None)


class TestDetermineArchitectures:
    """_determine_architectures across erratum, RoG and compose-default paths."""

    def test_arch_options_override_everything(self):
        # explicit --arch options win over any artifact metadata
        job = _erratum_job([Arch.S390X])
        result = _determine_architectures(None, ['x86_64', 'riscv64'], job, None)
        # riscv64 is filtered out, x86_64 kept
        assert result == [Arch.X86_64]

    # --- erratum ---------------------------------------------------------

    def test_erratum_defined_arch(self):
        job = _erratum_job([Arch.X86_64, Arch.S390X])
        assert _determine_architectures(None, [], job, None) == [Arch.X86_64, Arch.S390X]

    def test_erratum_noarch_resolves_to_defaults(self):
        # noarch builds are expanded to the default set upstream, so the
        # erratum carries the full default list - it must be honored as-is
        job = _erratum_job(list(DEFAULT_ARCHS))
        assert _determine_architectures(None, [], job, None) == DEFAULT_ARCHS

    def test_erratum_excluded_only_is_empty(self):
        # a riscv64-only erratum resolves to an empty supported-arch set
        job = _erratum_job([])
        assert _determine_architectures(None, [], job, None) == []

    # --- RoG -------------------------------------------------------------

    def test_rog_defined_arch(self):
        job = _rog_job([Arch.AARCH64])
        assert _determine_architectures(None, [], job, None) == [Arch.AARCH64]

    def test_rog_noarch_resolves_to_defaults(self):
        job = _rog_job(list(DEFAULT_ARCHS))
        assert _determine_architectures(None, [], job, None) == DEFAULT_ARCHS

    def test_rog_excluded_only_is_empty(self):
        job = _rog_job([])
        assert _determine_architectures(None, [], job, None) == []

    # --- fallback --------------------------------------------------------

    def test_no_artifact_falls_back_to_compose_defaults(self):
        job = _plain_job()
        assert _determine_architectures(None, [], job, None) == DEFAULT_ARCHS


class TestProcessJiraJobArchSkip:
    """_process_jira_job skips artifacts with an empty supported-arch set."""

    def _make_jira_job(self, erratum=None, rog=None):
        return SimpleNamespace(
            recipe=SimpleNamespace(auto_schedule=True),
            jira=SimpleNamespace(id='TEST-1'),
            compose=None,
            erratum=erratum,
            rog=rog,
            )

    @pytest.mark.parametrize('job_factory', [
        lambda self: self._make_jira_job(erratum=SimpleNamespace(archs=[])),
        lambda self: self._make_jira_job(rog=SimpleNamespace(archs=[])),
        ])
    def test_skips_when_only_excluded_archs(self, tmp_path, job_factory):
        ctx = mock.MagicMock()
        ctx.state_dirpath = tmp_path
        ctx.action_id_filter_pattern = None
        ctx.issue_id_filter_pattern = None
        ctx.action_tag_filter_pattern = None
        jira_job = job_factory(self)

        _process_jira_job(ctx, jira_job, arch_options=[], fixtures=[],
                          no_reportportal=True)

        # job was skipped: an informative message was logged and no schedule
        # files were produced
        assert ctx.logger.info.called
        assert 'unsupported architectures' in str(ctx.logger.info.call_args)
        assert list(tmp_path.glob('*.yaml')) == []
