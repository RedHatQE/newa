"""Tests for combined action-id and action-tag filtering."""
import re
from unittest import mock

import pytest

from newa import CLIContext, Settings
from newa.cli.jira_helpers import _build_combined_action_filtered_list
from newa.cli.tag_filter import parse_tag_filter
from newa.models.issues import IssueAction


@pytest.fixture
def mock_logger():
    """Return a mocked logger."""
    return mock.MagicMock()


@pytest.fixture
def sample_actions():
    """Return a sample list of IssueAction objects for testing."""
    return [
        # Parent action with tier1 tag
        IssueAction(id='setup', action_tags=['tier1', 'setup']),

        # Child actions with various tags and IDs
        IssueAction(id='test_tier1_security', parent_id='setup',
                    action_tags=['tier1', 'security']),
        IssueAction(id='test_tier2_security', parent_id='setup',
                    action_tags=['tier2', 'security']),
        IssueAction(id='test_tier1_performance', parent_id='setup',
                    action_tags=['tier1', 'performance']),
        IssueAction(id='test_tier2_performance', parent_id='setup',
                    action_tags=['tier2', 'performance']),

        # Actions with tier1 in ID but not in tags
        IssueAction(id='build_tier1', action_tags=['build', 'nightly']),

        # Actions with tier1 in tags but not in ID
        IssueAction(id='regression_tests', action_tags=['tier1', 'regression']),

        # Actions with neither tier1 in ID nor tags
        IssueAction(id='cleanup', action_tags=['maintenance']),

        # Grandparent-parent-child hierarchy
        IssueAction(id='grandparent', action_tags=['base']),
        IssueAction(id='parent', parent_id='grandparent',
                    action_tags=['tier2']),
        IssueAction(id='child_tier1_smoke', parent_id='parent',
                    action_tags=['tier1', 'smoke']),
        ]


class TestCombinedFilters:
    """Tests for _build_combined_action_filtered_list with various filter combinations."""

    def test_no_filters_returns_none(self, tmp_path, mock_logger, sample_actions):
        """When no filters are specified, should return None."""
        ctx = CLIContext(
            logger=mock_logger,
            settings=Settings(),
            state_dirpath=tmp_path,
            cli_environment={},
            cli_context={},
            action_id_filter_pattern=None,
            action_tag_filter_pattern=None)

        result = _build_combined_action_filtered_list(ctx, sample_actions)
        assert result is None

    def test_only_action_id_filter(self, tmp_path, mock_logger, sample_actions):
        """When only action-id filter is specified, should return matching actions."""
        ctx = CLIContext(
            logger=mock_logger,
            settings=Settings(),
            state_dirpath=tmp_path,
            cli_environment={},
            cli_context={},
            action_id_filter_pattern=re.compile(r'test_tier1_.*'),
            action_tag_filter_pattern=None)

        result = _build_combined_action_filtered_list(ctx, sample_actions)

        # Should match test_tier1_security and test_tier1_performance, plus parent
        assert result is not None
        assert set(result) == {'test_tier1_security', 'test_tier1_performance', 'setup'}

    def test_only_action_tag_filter(self, tmp_path, mock_logger, sample_actions):
        """When only action-tag filter is specified, should return matching actions."""
        ctx = CLIContext(
            logger=mock_logger,
            settings=Settings(),
            state_dirpath=tmp_path,
            cli_environment={},
            cli_context={},
            action_id_filter_pattern=None,
            action_tag_filter_pattern=parse_tag_filter(r'security'))

        result = _build_combined_action_filtered_list(ctx, sample_actions)

        # Should match test_tier1_security and test_tier2_security, plus parent
        assert result is not None
        assert set(result) == {'test_tier1_security', 'test_tier2_security', 'setup'}

    def test_both_filters_intersection(self, tmp_path, mock_logger, sample_actions):
        """When both filters are specified, should return intersection."""
        ctx = CLIContext(
            logger=mock_logger,
            settings=Settings(),
            state_dirpath=tmp_path,
            cli_environment={},
            cli_context={},
            action_id_filter_pattern=re.compile(r'test_tier1_.*'),
            action_tag_filter_pattern=parse_tag_filter(r'security'))

        result = _build_combined_action_filtered_list(ctx, sample_actions)

        # Should match only test_tier1_security (matches both filters), plus parent
        assert result is not None
        assert set(result) == {'test_tier1_security', 'setup'}

    def test_both_filters_no_intersection(self, tmp_path, mock_logger, sample_actions):
        """When both filters specified but no intersection, should return empty list."""
        ctx = CLIContext(
            logger=mock_logger,
            settings=Settings(),
            state_dirpath=tmp_path,
            cli_environment={},
            cli_context={},
            action_id_filter_pattern=re.compile(r'test_tier1_.*'),
            action_tag_filter_pattern=parse_tag_filter(r'maintenance'))

        result = _build_combined_action_filtered_list(ctx, sample_actions)

        # No actions match both criteria
        assert result is not None
        assert result == []

    def test_id_filter_matches_tag_filter_doesnt(self, tmp_path, mock_logger, sample_actions):
        """When ID filter matches but tag filter doesn't, should return empty."""
        ctx = CLIContext(
            logger=mock_logger,
            settings=Settings(),
            state_dirpath=tmp_path,
            cli_environment={},
            cli_context={},
            action_id_filter_pattern=re.compile(r'build_tier1'),
            action_tag_filter_pattern=parse_tag_filter(r'tier1'))

        result = _build_combined_action_filtered_list(ctx, sample_actions)

        # build_tier1 matches ID filter but has tags ['build', 'nightly'], not 'tier1'
        assert result is not None
        assert result == []

    def test_tag_filter_matches_id_filter_doesnt(self, tmp_path, mock_logger, sample_actions):
        """When tag filter matches but ID filter doesn't, should return empty."""
        ctx = CLIContext(
            logger=mock_logger,
            settings=Settings(),
            state_dirpath=tmp_path,
            cli_environment={},
            cli_context={},
            action_id_filter_pattern=re.compile(r'test_tier2_.*'),
            action_tag_filter_pattern=parse_tag_filter(r'tier1'))

        result = _build_combined_action_filtered_list(ctx, sample_actions)

        # Tag filter matches setup (has tier1 tag) and regression_tests (has tier1 tag)
        # ID filter matches test_tier2_security and test_tier2_performance
        # Since tier2 tests have 'setup' as parent, setup is in id_filtered_list too
        # Intersection will include 'setup' (appears in both lists)
        assert result is not None
        assert set(result) == {'setup'}

    def test_both_filters_with_pattern_matching(self, tmp_path, mock_logger, sample_actions):
        """Test both filters with regex patterns."""
        ctx = CLIContext(
            logger=mock_logger,
            settings=Settings(),
            state_dirpath=tmp_path,
            cli_environment={},
            cli_context={},
            action_id_filter_pattern=re.compile(r'test_tier[12]_.*'),
            action_tag_filter_pattern=parse_tag_filter(r'tier1'))

        result = _build_combined_action_filtered_list(ctx, sample_actions)

        # Should match test_tier1_security and test_tier1_performance
        # (match ID pattern test_tier[12]_.* AND have tier1 tag)
        # Plus parent 'setup'
        assert result is not None
        assert set(result) == {'test_tier1_security', 'test_tier1_performance', 'setup'}

    def test_grandparent_included_when_grandchild_matches(
            self, tmp_path, mock_logger, sample_actions):
        """Test that grandparents are included when grandchild matches both filters."""
        ctx = CLIContext(
            logger=mock_logger,
            settings=Settings(),
            state_dirpath=tmp_path,
            cli_environment={},
            cli_context={},
            action_id_filter_pattern=re.compile(r'.*tier1.*'),
            action_tag_filter_pattern=parse_tag_filter(r'smoke'))

        result = _build_combined_action_filtered_list(ctx, sample_actions)

        # child_tier1_smoke matches both (has tier1 in ID and smoke tag)
        # Should include child, parent, and grandparent
        assert result is not None
        assert set(result) == {'child_tier1_smoke', 'parent', 'grandparent'}

    def test_multiple_tags_any_matches(self, tmp_path, mock_logger, sample_actions):
        """Test that tag filter matches if ANY tag matches the pattern."""
        ctx = CLIContext(
            logger=mock_logger,
            settings=Settings(),
            state_dirpath=tmp_path,
            cli_environment={},
            cli_context={},
            action_id_filter_pattern=None,
            action_tag_filter_pattern=parse_tag_filter(r'performance'))

        result = _build_combined_action_filtered_list(ctx, sample_actions)

        # Should match both tier1 and tier2 performance tests, plus parent
        assert result is not None
        assert set(result) == {'test_tier1_performance', 'test_tier2_performance', 'setup'}

    def test_combined_filters_with_or_pattern(self, tmp_path, mock_logger, sample_actions):
        """Test combined filters where tag filter uses OR pattern."""
        ctx = CLIContext(
            logger=mock_logger,
            settings=Settings(),
            state_dirpath=tmp_path,
            cli_environment={},
            cli_context={},
            action_id_filter_pattern=re.compile(r'test_tier1_.*'),
            action_tag_filter_pattern=parse_tag_filter(r'security|performance'))

        result = _build_combined_action_filtered_list(ctx, sample_actions)

        # Should match test_tier1_security and test_tier1_performance
        # (both match ID pattern AND have security OR performance tag)
        assert result is not None
        assert set(result) == {'test_tier1_security', 'test_tier1_performance', 'setup'}

    def test_parent_included_even_if_no_tags(self, tmp_path, mock_logger):
        """Test that parent is included even if it doesn't match filters itself."""
        actions = [
            IssueAction(id='parent_no_tags'),  # No tags, wouldn't match tag filter
            IssueAction(id='test_child', parent_id='parent_no_tags',
                        action_tags=['tier1']),
            ]

        ctx = CLIContext(
            logger=mock_logger,
            settings=Settings(),
            state_dirpath=tmp_path,
            cli_environment={},
            cli_context={},
            action_id_filter_pattern=re.compile(r'test_.*'),
            action_tag_filter_pattern=parse_tag_filter(r'tier1'))

        result = _build_combined_action_filtered_list(ctx, actions)

        # Parent should be included even though it has no tags
        assert result is not None
        assert set(result) == {'test_child', 'parent_no_tags'}

    def test_exact_match_required(self, tmp_path, mock_logger, sample_actions):
        """Test that filters require full match, not partial."""
        ctx = CLIContext(
            logger=mock_logger,
            settings=Settings(),
            state_dirpath=tmp_path,
            cli_environment={},
            cli_context={},
            action_id_filter_pattern=re.compile(r'test'),  # Partial match
            action_tag_filter_pattern=parse_tag_filter(r'tier'))  # Partial match

        result = _build_combined_action_filtered_list(ctx, sample_actions)

        # Should not match anything (patterns don't fully match)
        assert result is not None
        assert result == []

    def test_logging_behavior(self, tmp_path, mock_logger, sample_actions):
        """Test that appropriate debug logs are generated."""
        ctx = CLIContext(
            logger=mock_logger,
            settings=Settings(),
            state_dirpath=tmp_path,
            cli_environment={},
            cli_context={},
            action_id_filter_pattern=re.compile(r'test_tier1_.*'),
            action_tag_filter_pattern=parse_tag_filter(r'security'))

        _build_combined_action_filtered_list(ctx, sample_actions)

        # Should log filtered lists and intersection
        assert mock_logger.debug.call_count >= 3

        # Check that intersection log was called
        call_args = [str(call) for call in mock_logger.debug.call_args_list]
        intersection_logged = any('intersection' in arg.lower() for arg in call_args)
        assert intersection_logged
