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


class TestActionTagFilter:
    """Tests for _should_filter_by_action_tags method."""

    @pytest.fixture
    def ctx_no_tag_filter(self, tmp_path, mock_logger):
        """Return a CLIContext without tag filter."""
        return CLIContext(
            logger=mock_logger,
            settings=Settings(),
            state_dirpath=tmp_path,
            cli_environment={},
            cli_context={},
            action_tag_filter_pattern=None)

    @pytest.fixture
    def ctx_with_tag_filter(self, tmp_path, mock_logger):
        """Return a CLIContext with action_tag filter."""
        return CLIContext(
            logger=mock_logger,
            settings=Settings(),
            state_dirpath=tmp_path,
            cli_environment={},
            cli_context={},
            action_tag_filter_pattern=re.compile(r'tier.*'))

    def test_no_filter_returns_false(self, ctx_no_tag_filter):
        """When no filter pattern is set, should not filter."""
        result = ctx_no_tag_filter._should_filter_by_action_tags(['tier1', 'regression'])
        assert result is False

    def test_matching_single_tag_returns_false(self, ctx_with_tag_filter):
        """When one tag matches pattern, should not filter (return False)."""
        result = ctx_with_tag_filter._should_filter_by_action_tags(['tier1'])
        assert result is False
        # Check debug log was called
        ctx_with_tag_filter.logger.debug.assert_called_once()

    def test_matching_tag_in_list_returns_false(self, ctx_with_tag_filter):
        """When any tag in list matches pattern, should not filter (return False)."""
        result = ctx_with_tag_filter._should_filter_by_action_tags(
            ['performance', 'tier2', 'nightly'])
        assert result is False
        # Check debug log was called
        ctx_with_tag_filter.logger.debug.assert_called_once()

    def test_non_matching_tags_returns_true(self, ctx_with_tag_filter):
        """When no tags match pattern, should filter (return True)."""
        result = ctx_with_tag_filter._should_filter_by_action_tags(
            ['performance', 'nightly'])
        assert result is True
        # Check info log was called (log_message=True by default)
        ctx_with_tag_filter.logger.info.assert_called_once()

    def test_none_action_tags_returns_true(self, ctx_with_tag_filter):
        """When action_tags is None, should filter (return True)."""
        result = ctx_with_tag_filter._should_filter_by_action_tags(None)
        assert result is True

    def test_empty_action_tags_returns_true(self, ctx_with_tag_filter):
        """When action_tags is empty list, should filter (return True)."""
        result = ctx_with_tag_filter._should_filter_by_action_tags([])
        assert result is True

    def test_log_message_false_uses_debug(self, ctx_with_tag_filter):
        """When log_message=False, should use debug log for skips."""
        result = ctx_with_tag_filter._should_filter_by_action_tags(
            ['performance', 'nightly'], log_message=False)
        assert result is True
        # Check debug log was called, not info
        # Note: debug is called for the filter check
        ctx_with_tag_filter.logger.info.assert_not_called()

    def test_pattern_matches_full_tag_only(self, ctx_with_tag_filter):
        """Pattern should match full tag, not partial."""
        # Pattern is 'tier.*', so 'tier1' matches but 'mytier' doesn't
        result_match = ctx_with_tag_filter._should_filter_by_action_tags(['tier1'])
        assert result_match is False

        # Reset logger mock for second call
        ctx_with_tag_filter.logger.reset_mock()

        result_no_match = ctx_with_tag_filter._should_filter_by_action_tags(['mytier'])
        assert result_no_match is True


class TestBuildActionTagFilteredList:
    """Tests for _build_action_tag_filtered_list function."""

    def test_empty_action_list(self):
        """Test with empty action list."""
        from newa.cli.jira_helpers import _build_action_tag_filtered_list

        result = _build_action_tag_filtered_list([], re.compile(r'tier.*'))
        assert result == []

    def test_no_matching_tags(self):
        """Test when no tags match the pattern."""
        from newa.cli.jira_helpers import _build_action_tag_filtered_list
        from newa.models.issues import IssueAction

        actions = [
            IssueAction(id='action1', action_tags=['performance', 'nightly']),
            IssueAction(id='action2', action_tags=['regression']),
            ]

        result = _build_action_tag_filtered_list(actions, re.compile(r'tier.*'))
        assert result == []

    def test_single_matching_tag(self):
        """Test when one action has a matching tag."""
        from newa.cli.jira_helpers import _build_action_tag_filtered_list
        from newa.models.issues import IssueAction

        actions = [
            IssueAction(id='action1', action_tags=['tier1']),
            IssueAction(id='action2', action_tags=['performance']),
            ]

        result = _build_action_tag_filtered_list(actions, re.compile(r'tier.*'))
        assert result == ['action1']

    def test_multiple_matching_tags(self):
        """Test when multiple actions have matching tags."""
        from newa.cli.jira_helpers import _build_action_tag_filtered_list
        from newa.models.issues import IssueAction

        actions = [
            IssueAction(id='action1', action_tags=['tier1']),
            IssueAction(id='action2', action_tags=['tier2', 'performance']),
            IssueAction(id='action3', action_tags=['regression']),
            ]

        result = _build_action_tag_filtered_list(actions, re.compile(r'tier.*'))
        assert set(result) == {'action1', 'action2'}

    def test_includes_parent_actions(self):
        """Test that parent actions are included when child matches."""
        from newa.cli.jira_helpers import _build_action_tag_filtered_list
        from newa.models.issues import IssueAction

        actions = [
            IssueAction(id='parent', action_tags=['setup']),
            IssueAction(id='child1', parent_id='parent', action_tags=['tier1']),
            IssueAction(id='child2', parent_id='parent', action_tags=['performance']),
            ]

        result = _build_action_tag_filtered_list(actions, re.compile(r'tier.*'))
        # Should include child1 (matches) and parent (parent of child1)
        assert set(result) == {'child1', 'parent'}

    def test_includes_grandparent_actions(self):
        """Test that grandparent actions are included when grandchild matches."""
        from newa.cli.jira_helpers import _build_action_tag_filtered_list
        from newa.models.issues import IssueAction

        actions = [
            IssueAction(id='grandparent', action_tags=['setup']),
            IssueAction(id='parent', parent_id='grandparent', action_tags=['prepare']),
            IssueAction(id='child', parent_id='parent', action_tags=['tier1']),
            ]

        result = _build_action_tag_filtered_list(actions, re.compile(r'tier.*'))
        # Should include all three: child (matches), parent, and grandparent
        assert set(result) == {'child', 'parent', 'grandparent'}

    def test_actions_without_tags(self):
        """Test that actions without tags are not included unless they're parents."""
        from newa.cli.jira_helpers import _build_action_tag_filtered_list
        from newa.models.issues import IssueAction

        actions = [
            IssueAction(id='action1'),  # No tags
            IssueAction(id='action2', action_tags=['tier1']),
            IssueAction(id='action3'),  # No tags
            ]

        result = _build_action_tag_filtered_list(actions, re.compile(r'tier.*'))
        assert result == ['action2']

    def test_actions_with_explicit_id(self):
        """Test that only actions with matching tags are included."""
        from newa.cli.jira_helpers import _build_action_tag_filtered_list
        from newa.models.issues import IssueAction

        actions = [
            IssueAction(id='action1', action_tags=['performance']),
            IssueAction(id='action2', action_tags=['tier2']),
            IssueAction(id='action3', action_tags=['nightly']),
            ]

        result = _build_action_tag_filtered_list(actions, re.compile(r'tier.*'))
        assert result == ['action2']
