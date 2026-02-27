"""Tests for action_id and issue_id filtering functionality."""
import re
from unittest import mock

import pytest

from newa import CLIContext, Settings


@pytest.fixture
def mock_logger():
    """Return a mocked logger."""
    return mock.MagicMock()


@pytest.fixture
def ctx_no_filter(tmp_path, mock_logger):
    """Return a CLIContext without any filters."""
    return CLIContext(
        logger=mock_logger,
        settings=Settings(),
        state_dirpath=tmp_path,
        cli_environment={},
        cli_context={},
        action_id_filter_pattern=None,
        issue_id_filter_pattern=None)


@pytest.fixture
def ctx_with_action_filter(tmp_path, mock_logger):
    """Return a CLIContext with action_id filter."""
    return CLIContext(
        logger=mock_logger,
        settings=Settings(),
        state_dirpath=tmp_path,
        cli_environment={},
        cli_context={},
        action_id_filter_pattern=re.compile(r'tier.*'),
        issue_id_filter_pattern=None)


@pytest.fixture
def ctx_with_issue_filter(tmp_path, mock_logger):
    """Return a CLIContext with issue_id filter."""
    return CLIContext(
        logger=mock_logger,
        settings=Settings(),
        state_dirpath=tmp_path,
        cli_environment={},
        cli_context={},
        action_id_filter_pattern=None,
        issue_id_filter_pattern=re.compile(r'RHEL-123.*'))


@pytest.fixture
def ctx_with_both_filters(tmp_path, mock_logger):
    """Return a CLIContext with both filters."""
    return CLIContext(
        logger=mock_logger,
        settings=Settings(),
        state_dirpath=tmp_path,
        cli_environment={},
        cli_context={},
        action_id_filter_pattern=re.compile(r'tier.*'),
        issue_id_filter_pattern=re.compile(r'RHEL-123.*'))


class TestActionIdFilter:
    """Tests for _should_filter_by_action_id method."""

    def test_no_filter_returns_false(self, ctx_no_filter):
        """When no filter pattern is set, should not filter."""
        result = ctx_no_filter._should_filter_by_action_id('any_action_id')
        assert result is False

    def test_matching_action_returns_false(self, ctx_with_action_filter):
        """When action_id matches pattern, should not filter (return False)."""
        result = ctx_with_action_filter._should_filter_by_action_id('tier1')
        assert result is False
        # Check debug log was called
        ctx_with_action_filter.logger.debug.assert_called_once()

    def test_non_matching_action_returns_true(self, ctx_with_action_filter):
        """When action_id doesn't match pattern, should filter (return True)."""
        result = ctx_with_action_filter._should_filter_by_action_id('build_x86')
        assert result is True
        # Check info log was called (log_message=True by default)
        ctx_with_action_filter.logger.info.assert_called_once()

    def test_none_action_id_returns_true(self, ctx_with_action_filter):
        """When action_id is None, should filter (return True)."""
        result = ctx_with_action_filter._should_filter_by_action_id(None)
        assert result is True

    def test_log_message_false_uses_debug(self, ctx_with_action_filter):
        """When log_message=False, should use debug log for skips."""
        result = ctx_with_action_filter._should_filter_by_action_id(
            'build_x86', log_message=False)
        assert result is True
        # Check debug log was called, not info
        ctx_with_action_filter.logger.debug.assert_called_once()
        ctx_with_action_filter.logger.info.assert_not_called()


class TestIssueIdFilter:
    """Tests for _should_filter_by_issue_id method."""

    def test_no_filter_returns_false(self, ctx_no_filter):
        """When no filter pattern is set, should not filter."""
        result = ctx_no_filter._should_filter_by_issue_id('RHEL-12345')
        assert result is False

    def test_matching_issue_returns_false(self, ctx_with_issue_filter):
        """When issue_id matches pattern, should not filter (return False)."""
        result = ctx_with_issue_filter._should_filter_by_issue_id('RHEL-12345')
        assert result is False
        # Check debug log was called
        ctx_with_issue_filter.logger.debug.assert_called_once()

    def test_non_matching_issue_returns_true(self, ctx_with_issue_filter):
        """When issue_id doesn't match pattern, should filter (return True)."""
        result = ctx_with_issue_filter._should_filter_by_issue_id('RHEL-99999')
        assert result is True
        # Check info log was called (log_message=True by default)
        ctx_with_issue_filter.logger.info.assert_called_once()

    def test_none_issue_id_returns_true(self, ctx_with_issue_filter):
        """When issue_id is None, should filter (return True)."""
        result = ctx_with_issue_filter._should_filter_by_issue_id(None)
        assert result is True

    def test_log_message_false_uses_debug(self, ctx_with_issue_filter):
        """When log_message=False, should use debug log for skips."""
        result = ctx_with_issue_filter._should_filter_by_issue_id(
            'RHEL-99999', log_message=False)
        assert result is True
        # Check debug log was called, not info
        ctx_with_issue_filter.logger.debug.assert_called_once()
        ctx_with_issue_filter.logger.info.assert_not_called()


class TestBothFilters:
    """Tests for using both filters together."""

    def test_both_match(self, ctx_with_both_filters):
        """When both filters match, both should return False."""
        action_result = ctx_with_both_filters._should_filter_by_action_id('tier1')
        issue_result = ctx_with_both_filters._should_filter_by_issue_id('RHEL-12345')
        assert action_result is False
        assert issue_result is False

    def test_action_matches_issue_doesnt(self, ctx_with_both_filters):
        """When action matches but issue doesn't, issue filter should return True."""
        action_result = ctx_with_both_filters._should_filter_by_action_id('tier1')
        issue_result = ctx_with_both_filters._should_filter_by_issue_id('RHEL-99999')
        assert action_result is False
        assert issue_result is True

    def test_issue_matches_action_doesnt(self, ctx_with_both_filters):
        """When issue matches but action doesn't, action filter should return True."""
        action_result = ctx_with_both_filters._should_filter_by_action_id('build_x86')
        issue_result = ctx_with_both_filters._should_filter_by_issue_id('RHEL-12345')
        assert action_result is True
        assert issue_result is False

    def test_both_dont_match(self, ctx_with_both_filters):
        """When both filters don't match, both should return True."""
        action_result = ctx_with_both_filters._should_filter_by_action_id('build_x86')
        issue_result = ctx_with_both_filters._should_filter_by_issue_id('RHEL-99999')
        assert action_result is True
        assert issue_result is True


class TestEventFilter:
    """Tests for event filter parsing and filtering."""

    def test_parse_compose_id_filter(self):
        """Test parsing compose.id filter."""
        from newa.cli.event_helpers import parse_event_filter

        filter_obj = parse_event_filter('compose.id=RHEL-8.*')
        assert filter_obj.object_type == 'compose'
        assert filter_obj.attribute == 'id'
        assert filter_obj.pattern.pattern == 'RHEL-8.*'

    def test_parse_erratum_id_filter(self):
        """Test parsing erratum.id filter."""
        from newa.cli.event_helpers import parse_event_filter

        filter_obj = parse_event_filter('erratum.id=RHBA-.*')
        assert filter_obj.object_type == 'erratum'
        assert filter_obj.attribute == 'id'
        assert filter_obj.pattern.pattern == 'RHBA-.*'

    def test_parse_erratum_release_filter(self):
        """Test parsing erratum.release filter."""
        from newa.cli.event_helpers import parse_event_filter

        filter_obj = parse_event_filter('erratum.release=RHEL-9.5')
        assert filter_obj.object_type == 'erratum'
        assert filter_obj.attribute == 'release'
        assert filter_obj.pattern.pattern == 'RHEL-9.5'

    def test_parse_rog_id_filter(self):
        """Test parsing rog.id filter."""
        from newa.cli.event_helpers import parse_event_filter

        filter_obj = parse_event_filter('rog.id=https://.*')
        assert filter_obj.object_type == 'rog'
        assert filter_obj.attribute == 'id'
        assert filter_obj.pattern.pattern == 'https://.*'

    def test_parse_invalid_format(self):
        """Test parsing invalid filter format."""
        import click

        from newa.cli.event_helpers import parse_event_filter

        with pytest.raises(click.ClickException, match='Invalid --event-filter format'):
            parse_event_filter('invalid_format')

    def test_parse_invalid_regex(self):
        """Test parsing invalid regex pattern."""
        import click

        from newa.cli.event_helpers import parse_event_filter

        with pytest.raises(click.ClickException, match='Cannot compile --event-filter'):
            parse_event_filter('compose.id=[invalid')

    def test_parse_unsupported_object_type(self):
        """Test parsing unsupported object type."""
        import click

        from newa.cli.event_helpers import parse_event_filter

        with pytest.raises(click.ClickException, match='Unsupported object type'):
            parse_event_filter('invalid.id=test')

    def test_parse_unsupported_attribute(self):
        """Test parsing unsupported attribute."""
        import click

        from newa.cli.event_helpers import parse_event_filter

        with pytest.raises(click.ClickException, match='Unsupported attribute'):
            parse_event_filter('compose.release=test')

    def test_should_filter_compose_matching(self, mock_logger):
        """Test filtering compose job that matches."""
        from newa.cli.event_helpers import parse_event_filter, should_filter_by_event
        from newa.models.artifacts import Compose
        from newa.models.events import Event, EventType
        from newa.models.jobs import ArtifactJob

        filter_obj = parse_event_filter('compose.id=RHEL-8.*')
        job = ArtifactJob(
            event=Event(type_=EventType.COMPOSE, id='test'),
            compose=Compose(id='RHEL-8.10-Nightly'),
            erratum=None,
            )

        result = should_filter_by_event(filter_obj, job, mock_logger)
        assert result is False  # Should NOT be filtered (matches)

    def test_should_filter_compose_not_matching(self, mock_logger):
        """Test filtering compose job that doesn't match."""
        from newa.cli.event_helpers import parse_event_filter, should_filter_by_event
        from newa.models.artifacts import Compose
        from newa.models.events import Event, EventType
        from newa.models.jobs import ArtifactJob

        filter_obj = parse_event_filter('compose.id=RHEL-8.*')
        job = ArtifactJob(
            event=Event(type_=EventType.COMPOSE, id='test'),
            compose=Compose(id='RHEL-9.5-Nightly'),
            erratum=None,
            )

        result = should_filter_by_event(filter_obj, job, mock_logger)
        assert result is True  # Should be filtered (doesn't match)

    def test_should_filter_erratum_release_matching(self, mock_logger):
        """Test filtering erratum job by release that matches."""
        from newa.cli.event_helpers import parse_event_filter, should_filter_by_event
        from newa.models.artifacts import Erratum, ErratumContentType
        from newa.models.events import Event, EventType
        from newa.models.jobs import ArtifactJob

        filter_obj = parse_event_filter('erratum.release=RHEL-9.5')
        job = ArtifactJob(
            event=Event(type_=EventType.ERRATUM, id='RHBA-2024:1234'),
            erratum=Erratum(
                id='RHBA-2024:1234',
                content_type=ErratumContentType.RPM,
                respin_count=0,
                summary='Test advisory',
                release='RHEL-9.5',
                url='https://example.com',
                ),
            compose=None,
            )

        result = should_filter_by_event(filter_obj, job, mock_logger)
        assert result is False  # Should NOT be filtered (matches)

    def test_should_filter_erratum_release_not_matching(self, mock_logger):
        """Test filtering erratum job by release that doesn't match."""
        from newa.cli.event_helpers import parse_event_filter, should_filter_by_event
        from newa.models.artifacts import Erratum, ErratumContentType
        from newa.models.events import Event, EventType
        from newa.models.jobs import ArtifactJob

        filter_obj = parse_event_filter('erratum.release=RHEL-9.5.*')
        job = ArtifactJob(
            event=Event(type_=EventType.ERRATUM, id='RHBA-2024:1234'),
            erratum=Erratum(
                id='RHBA-2024:1234',
                content_type=ErratumContentType.RPM,
                respin_count=0,
                summary='Test advisory',
                release='RHEL-8.10',
                url='https://example.com',
                ),
            compose=None,
            )

        result = should_filter_by_event(filter_obj, job, mock_logger)
        assert result is True  # Should be filtered (doesn't match)

    def test_should_filter_erratum_empty_release(self, mock_logger):
        """Test filtering erratum job with empty release value."""
        from newa.cli.event_helpers import parse_event_filter, should_filter_by_event
        from newa.models.artifacts import Erratum, ErratumContentType
        from newa.models.events import Event, EventType
        from newa.models.jobs import ArtifactJob

        filter_obj = parse_event_filter('erratum.release=RHEL-9.5')
        job = ArtifactJob(
            event=Event(type_=EventType.ERRATUM, id='RHBA-2024:1234'),
            erratum=Erratum(
                id='RHBA-2024:1234',
                content_type=ErratumContentType.RPM,
                respin_count=0,
                summary='Test advisory',
                release='',  # Empty release
                url='https://example.com',
                ),
            compose=None,
            )

        result = should_filter_by_event(filter_obj, job, mock_logger)
        assert result is True  # Should be filtered (has no value)

    def test_should_filter_rog_id_matching(self, mock_logger):
        """Test filtering rog job that matches."""
        from newa.cli.event_helpers import parse_event_filter, should_filter_by_event
        from newa.models.artifacts import RoG
        from newa.models.events import Event, EventType
        from newa.models.jobs import ArtifactJob

        filter_obj = parse_event_filter('rog.id=https://gitlab.com/.*')
        job = ArtifactJob(
            event=Event(type_=EventType.ROG, id='test'),
            compose=None,
            erratum=None,
            rog=RoG(
                id='https://gitlab.com/redhat/centos-stream/rpms/bash/-/merge_requests/1',
                content_type=None,
                title='Test MR',
                build_task_id='12345',
                build_target='centos-stream-9',
                ),
            )

        result = should_filter_by_event(filter_obj, job, mock_logger)
        assert result is False  # Should NOT be filtered (matches)

    def test_should_filter_rog_id_not_matching(self, mock_logger):
        """Test filtering rog job that doesn't match."""
        from newa.cli.event_helpers import parse_event_filter, should_filter_by_event
        from newa.models.artifacts import RoG
        from newa.models.events import Event, EventType
        from newa.models.jobs import ArtifactJob

        filter_obj = parse_event_filter('rog.id=https://gitlab.com/.*')
        job = ArtifactJob(
            event=Event(type_=EventType.ROG, id='test'),
            compose=None,
            erratum=None,
            rog=RoG(
                id='https://github.com/organization/repo/pull/123',
                content_type=None,
                title='Test MR',
                build_task_id='12345',
                build_target='centos-stream-9',
                ),
            )

        result = should_filter_by_event(filter_obj, job, mock_logger)
        assert result is True  # Should be filtered (doesn't match)

    def test_should_filter_wrong_artifact_type(self, mock_logger):
        """Test filtering when artifact type doesn't match filter."""
        from newa.cli.event_helpers import parse_event_filter, should_filter_by_event
        from newa.models.artifacts import Compose
        from newa.models.events import Event, EventType
        from newa.models.jobs import ArtifactJob

        # Filter is for erratum, but job has compose
        filter_obj = parse_event_filter('erratum.id=RHBA-.*')
        job = ArtifactJob(
            event=Event(type_=EventType.COMPOSE, id='test'),
            compose=Compose(id='RHEL-8.10-Nightly'),
            erratum=None,
            )

        result = should_filter_by_event(filter_obj, job, mock_logger)
        assert result is True  # Should be filtered (wrong artifact type)
