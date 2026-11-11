"""Tests for the execute --arch request filtering helper."""

import pytest

from newa import Arch, Event, Issue, Request, ScheduleJob
from newa.cli.execute_helpers import filter_schedule_jobs_by_arch


def _schedule_job(request_id: str, arch: str) -> ScheduleJob:
    return ScheduleJob(
        event=Event(type_='erratum', id='RHSA-2024:12345'),
        erratum=None,
        compose=None,
        jira=Issue(id='TEST-1'),
        request=Request(id=request_id, arch=arch),
        )


JOBS = [
    _schedule_job('1.1', 'x86_64'),
    _schedule_job('1.2', 'aarch64'),
    _schedule_job('1.3', 's390x'),
    ]


def test_no_arch_returns_all_jobs():
    result = filter_schedule_jobs_by_arch(JOBS, [])
    assert result == JOBS


def test_single_arch_filters():
    result = filter_schedule_jobs_by_arch(JOBS, ['x86_64'])
    assert [j.request.id for j in result] == ['1.1']


def test_multiple_arches_filter():
    result = filter_schedule_jobs_by_arch(JOBS, ['x86_64', 'aarch64'])
    assert [j.request.id for j in result] == ['1.1', '1.2']


def test_arch_values_are_stripped():
    result = filter_schedule_jobs_by_arch(JOBS, [' aarch64 '])
    assert [j.request.id for j in result] == ['1.2']


def test_unknown_arch_returns_empty():
    result = filter_schedule_jobs_by_arch(JOBS, ['riscv64'])
    assert result == []


def test_single_exclusion():
    result = filter_schedule_jobs_by_arch(JOBS, ['!x86_64'])
    assert [j.request.id for j in result] == ['1.2', '1.3']


def test_multiple_exclusions():
    result = filter_schedule_jobs_by_arch(JOBS, ['!x86_64', '!aarch64'])
    assert [j.request.id for j in result] == ['1.3']


def test_include_and_exclude_combined():
    result = filter_schedule_jobs_by_arch(JOBS, ['x86_64', 'aarch64', '!aarch64'])
    assert [j.request.id for j in result] == ['1.1']


def test_exclusion_whitespace_stripped():
    result = filter_schedule_jobs_by_arch(JOBS, [' ! aarch64 '])
    assert [j.request.id for j in result] == ['1.1', '1.3']


def test_invalid_arch_raises():
    with pytest.raises(ValueError, match='Unknown architecture'):
        filter_schedule_jobs_by_arch(JOBS, ['!bogus'])


class TestParseFilters:
    def test_includes_only(self):
        includes, excludes = Arch.parse_filters(['x86_64', 'aarch64'])
        assert includes == [Arch.X86_64, Arch.AARCH64]
        assert excludes == set()

    def test_excludes_only(self):
        includes, excludes = Arch.parse_filters(['!x86_64', '!s390x'])
        assert includes == []
        assert excludes == {Arch.X86_64, Arch.S390X}

    def test_mixed_and_whitespace(self):
        includes, excludes = Arch.parse_filters([' x86_64 ', '! aarch64', '', '  '])
        assert includes == [Arch.X86_64]
        assert excludes == {Arch.AARCH64}

    def test_unknown_raises(self):
        with pytest.raises(ValueError, match='Unknown architecture'):
            Arch.parse_filters(['nope'])
