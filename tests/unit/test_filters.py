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
