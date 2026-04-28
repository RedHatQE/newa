"""Tests for --action-tag-filter CLI option and YAML file filtering."""
import re
from unittest import mock

import pytest

from newa import CLIContext, Settings
from newa.cli.main import _should_filter_yaml_file


@pytest.fixture
def mock_logger():
    """Return a mocked logger."""
    return mock.MagicMock()


@pytest.fixture
def temp_yaml_files(tmp_path):
    """Create temporary YAML files for testing."""
    # Create YAML file with action_tags
    yaml_with_tags = tmp_path / 'jira-001.yaml'
    yaml_with_tags.write_text("""
jira:
  id: RHEL-12345
  action_id: tier1_tests
  action_tags:
    - tier1
    - regression
  summary: Test Issue 1
""")

    # Create YAML file with different action_tags
    yaml_with_other_tags = tmp_path / 'jira-002.yaml'
    yaml_with_other_tags.write_text("""
jira:
  id: RHEL-12346
  action_id: tier2_tests
  action_tags:
    - tier2
    - performance
  summary: Test Issue 2
""")

    # Create YAML file without action_tags
    yaml_without_tags = tmp_path / 'jira-003.yaml'
    yaml_without_tags.write_text("""
jira:
  id: RHEL-12347
  action_id: build_tests
  summary: Test Issue 3
""")

    # Create YAML file with empty action_tags
    yaml_with_empty_tags = tmp_path / 'jira-004.yaml'
    yaml_with_empty_tags.write_text("""
jira:
  id: RHEL-12348
  action_id: other_tests
  action_tags: []
  summary: Test Issue 4
""")

    return {
        'with_tags': yaml_with_tags,
        'with_other_tags': yaml_with_other_tags,
        'without_tags': yaml_without_tags,
        'with_empty_tags': yaml_with_empty_tags,
        }


class TestShouldFilterYamlFile:
    """Tests for _should_filter_yaml_file with action_tag_filter."""

    def test_no_filters_keeps_all_files(self, temp_yaml_files, mock_logger):
        """When no filters are specified, all files should be kept."""
        for yaml_file in temp_yaml_files.values():
            result = _should_filter_yaml_file(
                yaml_file, None, None, None, None, mock_logger)
            assert result is False

    def test_tag_filter_matches_keeps_file(self, temp_yaml_files, mock_logger):
        """When tag filter matches, file should be kept."""
        tag_pattern = re.compile(r'tier1')
        result = _should_filter_yaml_file(
            temp_yaml_files['with_tags'],
            None, None, None, tag_pattern, mock_logger)
        assert result is False

    def test_tag_filter_no_match_filters_file(self, temp_yaml_files, mock_logger):
        """When tag filter doesn't match, file should be filtered."""
        tag_pattern = re.compile(r'tier1')
        result = _should_filter_yaml_file(
            temp_yaml_files['with_other_tags'],
            None, None, None, tag_pattern, mock_logger)
        assert result is True

    def test_tag_filter_pattern_matches_multiple(self, temp_yaml_files, mock_logger):
        """When tag filter pattern matches multiple tags, file should be kept."""
        tag_pattern = re.compile(r'tier.*')
        # Should match both tier1 and tier2
        result1 = _should_filter_yaml_file(
            temp_yaml_files['with_tags'],
            None, None, None, tag_pattern, mock_logger)
        assert result1 is False

        result2 = _should_filter_yaml_file(
            temp_yaml_files['with_other_tags'],
            None, None, None, tag_pattern, mock_logger)
        assert result2 is False

    def test_tag_filter_with_no_tags_filters_file(self, temp_yaml_files, mock_logger):
        """When file has no action_tags field, it should be filtered."""
        tag_pattern = re.compile(r'tier.*')
        result = _should_filter_yaml_file(
            temp_yaml_files['without_tags'],
            None, None, None, tag_pattern, mock_logger)
        assert result is True

    def test_tag_filter_with_empty_tags_filters_file(self, temp_yaml_files, mock_logger):
        """When file has empty action_tags list, it should be filtered."""
        tag_pattern = re.compile(r'tier.*')
        result = _should_filter_yaml_file(
            temp_yaml_files['with_empty_tags'],
            None, None, None, tag_pattern, mock_logger)
        assert result is True

    def test_tag_filter_matches_any_tag(self, temp_yaml_files, mock_logger):
        """When any tag matches pattern, file should be kept."""
        # Pattern matches 'regression' in the tag list ['tier1', 'regression']
        tag_pattern = re.compile(r'regression')
        result = _should_filter_yaml_file(
            temp_yaml_files['with_tags'],
            None, None, None, tag_pattern, mock_logger)
        assert result is False

    def test_combined_action_id_and_tag_filters(self, temp_yaml_files, mock_logger):
        """When both action_id and tag filters are specified, both must match."""
        action_id_pattern = re.compile(r'tier1.*')
        tag_pattern = re.compile(r'regression')

        # File with matching action_id and matching tag
        result = _should_filter_yaml_file(
            temp_yaml_files['with_tags'],
            action_id_pattern, None, None, tag_pattern, mock_logger)
        assert result is False

        # File with non-matching action_id but matching tag - should be filtered
        result2 = _should_filter_yaml_file(
            temp_yaml_files['with_other_tags'],
            action_id_pattern, None, None, tag_pattern, mock_logger)
        assert result2 is True


class TestActionTagFilterWithCLIContext:
    """Integration tests for action_tag_filter with CLIContext."""

    def test_ctx_with_tag_filter_loads_matching_jobs(self, tmp_path, mock_logger):
        """Test that CLIContext only loads jobs matching tag filter."""
        from newa.models.events import Event, EventType
        from newa.models.issues import Issue
        from newa.models.jobs import JiraJob
        from newa.models.recipes import Recipe

        # Create two jira jobs with different tags
        job1 = JiraJob(
            event=Event(id='test', type_=EventType.ERRATUM),
            erratum=None,
            compose=None,
            jira=Issue(
                'RHEL-123',
                summary='Test 1',
                url='http://test.com/RHEL-123',
                action_tags=['tier1', 'regression']),
            recipe=Recipe(url='http://example.com/recipe.yaml'))
        job1.to_yaml_file(tmp_path / 'jira-001.yaml')

        job2 = JiraJob(
            event=Event(id='test', type_=EventType.ERRATUM),
            erratum=None,
            compose=None,
            jira=Issue(
                'RHEL-456',
                summary='Test 2',
                url='http://test.com/RHEL-456',
                action_tags=['tier2', 'performance']),
            recipe=Recipe(url='http://example.com/recipe.yaml'))
        job2.to_yaml_file(tmp_path / 'jira-002.yaml')

        job3 = JiraJob(
            event=Event(id='test', type_=EventType.ERRATUM),
            erratum=None,
            compose=None,
            jira=Issue(
                'RHEL-789',
                summary='Test 3',
                url='http://test.com/RHEL-789'),
            recipe=Recipe(url='http://example.com/recipe.yaml'))
        job3.to_yaml_file(tmp_path / 'jira-003.yaml')

        # Create context with tag filter for 'tier1'
        ctx = CLIContext(
            logger=mock_logger,
            settings=Settings(),
            state_dirpath=tmp_path,
            cli_environment={},
            cli_context={},
            action_tag_filter_pattern=re.compile(r'tier1'))

        # Load jira jobs with filter_actions=True - should only get job1
        jobs = list(ctx.load_jira_jobs('jira', filter_actions=True))
        assert len(jobs) == 1
        assert jobs[0].jira.id == 'RHEL-123'

    def test_ctx_with_tag_filter_pattern_matches_multiple(self, tmp_path, mock_logger):
        """Test that tag filter pattern can match multiple jobs."""
        from newa.models.events import Event, EventType
        from newa.models.issues import Issue
        from newa.models.jobs import JiraJob
        from newa.models.recipes import Recipe

        job1 = JiraJob(
            event=Event(id='test', type_=EventType.ERRATUM),
            erratum=None,
            compose=None,
            jira=Issue(
                'RHEL-123',
                summary='Test 1',
                url='http://test.com/RHEL-123',
                action_tags=['tier1']),
            recipe=Recipe(url='http://example.com/recipe.yaml'))
        job1.to_yaml_file(tmp_path / 'jira-001.yaml')

        job2 = JiraJob(
            event=Event(id='test', type_=EventType.ERRATUM),
            erratum=None,
            compose=None,
            jira=Issue(
                'RHEL-456',
                summary='Test 2',
                url='http://test.com/RHEL-456',
                action_tags=['tier2']),
            recipe=Recipe(url='http://example.com/recipe.yaml'))
        job2.to_yaml_file(tmp_path / 'jira-002.yaml')

        job3 = JiraJob(
            event=Event(id='test', type_=EventType.ERRATUM),
            erratum=None,
            compose=None,
            jira=Issue(
                'RHEL-789',
                summary='Test 3',
                url='http://test.com/RHEL-789',
                action_tags=['performance']),
            recipe=Recipe(url='http://example.com/recipe.yaml'))
        job3.to_yaml_file(tmp_path / 'jira-003.yaml')

        # Create context with tag filter for 'tier.*'
        ctx = CLIContext(
            logger=mock_logger,
            settings=Settings(),
            state_dirpath=tmp_path,
            cli_environment={},
            cli_context={},
            action_tag_filter_pattern=re.compile(r'tier.*'))

        # Load jira jobs with filter_actions=True - should get job1 and job2
        jobs = list(ctx.load_jira_jobs('jira', filter_actions=True))
        assert len(jobs) == 2
        assert {job.jira.id for job in jobs} == {'RHEL-123', 'RHEL-456'}
