"""Tests for _create_simple_jira_job functionality with multiple --job-recipe support."""

from pathlib import Path
from unittest import mock

import pytest

from newa import ArtifactJob, CLIContext, Event, EventType, Settings
from newa.cli.jira_helpers import _create_jira_fake_id_generator, _create_simple_jira_job


@pytest.fixture
def mock_ctx(tmp_path):
    """Return a CLIContext object with mocked logger and temp dirpath."""
    return CLIContext(
        logger=mock.MagicMock(),
        settings=Settings(
            jira_url='http://dummy.jira.url.com',
            ),
        state_dirpath=tmp_path,
        cli_environment={},
        cli_context={},
        action_id_filter_pattern=None,
        )


@pytest.fixture
def mock_artifact_job():
    """Return a mock ArtifactJob."""
    return ArtifactJob(
        event=Event(id='12345', type_=EventType.ERRATUM),
        erratum=None,
        compose=None,
        rog=None,
        )


class TestCreateSimpleJiraJob:
    """Test suite for _create_simple_jira_job with multiple recipe support."""

    def test_single_recipe_without_issue(self, mock_ctx, mock_artifact_job):
        """Test creating a single job without issue generates one fake ID."""
        jira_none_id = _create_jira_fake_id_generator()

        _create_simple_jira_job(
            ctx=mock_ctx,
            artifact_job=mock_artifact_job,
            issue=(),
            prev_issue=False,
            job_recipe=('http://example.com/recipe.yaml',),
            jira_none_id=jira_none_id)

        # Verify one jira job file was created
        jira_job_files = list(Path(mock_ctx.state_dirpath).glob('jira-*'))
        assert len(jira_job_files) == 1

    def test_multiple_recipes_without_issue(self, mock_ctx, mock_artifact_job):
        """Test creating multiple jobs without issue generates unique fake IDs."""
        jira_none_id = _create_jira_fake_id_generator()

        _create_simple_jira_job(
            ctx=mock_ctx,
            artifact_job=mock_artifact_job,
            issue=(),
            prev_issue=False,
            job_recipe=(
                'http://example.com/recipe1.yaml',
                'http://example.com/recipe2.yaml',
                'http://example.com/recipe3.yaml',
                ),
            jira_none_id=jira_none_id)

        # Verify three jira job files were created
        jira_job_files = list(Path(mock_ctx.state_dirpath).glob('jira-*'))
        assert len(jira_job_files) == 3

        # Load the jobs and verify they have unique fake IDs
        from newa.models.jobs import JiraJob
        jira_ids = []
        for job_file in jira_job_files:
            job = JiraJob.from_yaml_file(job_file)
            jira_ids.append(job.jira.id)

        # All IDs should be unique
        assert len(jira_ids) == len(set(jira_ids))
        # All IDs should start with _NO_ISSUE_
        assert all(jid.startswith('_NO_ISSUE_') for jid in jira_ids)

    def test_multiple_recipes_with_matching_issues(self, mock_ctx, mock_artifact_job):
        """Test creating multiple jobs with matching issue IDs."""
        jira_none_id = _create_jira_fake_id_generator()

        # Mock the Jira connection
        mock_jira_connection = mock.MagicMock()
        mock_jira_issue1 = mock.MagicMock()
        mock_jira_issue1.fields.summary = 'Test Issue 1'
        mock_jira_issue1.key = 'RHEL-123'
        mock_jira_issue2 = mock.MagicMock()
        mock_jira_issue2.fields.summary = 'Test Issue 2'
        mock_jira_issue2.key = 'RHEL-456'

        mock_jira_connection.get_connection.return_value.issue.side_effect = [
            mock_jira_issue1, mock_jira_issue2,
            ]

        with mock.patch.object(
                type(mock_ctx), 'get_jira_connection',
                return_value=mock_jira_connection):
            _create_simple_jira_job(
                ctx=mock_ctx,
                artifact_job=mock_artifact_job,
                issue=('RHEL-123', 'RHEL-456'),
                prev_issue=False,
                job_recipe=(
                    'http://example.com/recipe1.yaml',
                    'http://example.com/recipe2.yaml',
                    ),
                jira_none_id=jira_none_id)

        # Verify two jira job files were created
        jira_job_files = list(Path(mock_ctx.state_dirpath).glob('jira-*'))
        assert len(jira_job_files) == 2

        # Load the jobs and verify they have the correct issue IDs
        from newa.models.jobs import JiraJob
        jira_ids = []
        for job_file in jira_job_files:
            job = JiraJob.from_yaml_file(job_file)
            jira_ids.append(job.jira.id)

        assert set(jira_ids) == {'RHEL-123', 'RHEL-456'}

    def test_mismatched_issue_and_recipe_counts(self, mock_ctx, mock_artifact_job):
        """Test that mismatched issue and recipe counts raises an exception."""
        jira_none_id = _create_jira_fake_id_generator()

        with pytest.raises(Exception, match="Number of --issue arguments.*must match"):
            _create_simple_jira_job(
                ctx=mock_ctx,
                artifact_job=mock_artifact_job,
                issue=('RHEL-123',),  # Only 1 issue
                prev_issue=False,
                job_recipe=(
                    'http://example.com/recipe1.yaml',
                    'http://example.com/recipe2.yaml',  # But 2 recipes
                    ),
                jira_none_id=jira_none_id)

    def test_more_issues_than_recipes(self, mock_ctx, mock_artifact_job):
        """Test that having more issues than recipes raises an exception."""
        jira_none_id = _create_jira_fake_id_generator()

        with pytest.raises(Exception, match="Number of --issue arguments.*must match"):
            _create_simple_jira_job(
                ctx=mock_ctx,
                artifact_job=mock_artifact_job,
                issue=('RHEL-123', 'RHEL-456'),  # 2 issues
                prev_issue=False,
                job_recipe=('http://example.com/recipe1.yaml',),  # But only 1 recipe
                jira_none_id=jira_none_id)

    def test_duplicate_issue_keys(self, mock_ctx, mock_artifact_job):
        """Test that duplicate issue keys raises an exception."""
        jira_none_id = _create_jira_fake_id_generator()

        with pytest.raises(Exception, match="Duplicate Jira issue keys found"):
            _create_simple_jira_job(
                ctx=mock_ctx,
                artifact_job=mock_artifact_job,
                issue=('RHEL-123', 'RHEL-123'),  # Duplicate
                prev_issue=False,
                job_recipe=(
                    'http://example.com/recipe1.yaml',
                    'http://example.com/recipe2.yaml',
                    ),
                jira_none_id=jira_none_id)

    def test_prev_issue_with_multiple_recipes(self, mock_ctx, mock_artifact_job, tmp_path):
        """Test that --prev-issue with multiple recipes raises an exception."""
        # Set up a previous state directory with a jira job
        prev_state_dir = tmp_path / 'prev_state'
        prev_state_dir.mkdir()

        # Create a mock previous jira job
        from newa.models.issues import Issue
        from newa.models.jobs import JiraJob
        from newa.models.recipes import Recipe
        prev_job = JiraJob(
            event=Event(id='12345', type_=EventType.ERRATUM),
            erratum=None,
            compose=None,
            rog=None,
            jira=Issue('RHEL-999', summary='Previous Issue', url='http://example.com/RHEL-999'),
            recipe=Recipe(url='http://example.com/prev_recipe.yaml'),
            )
        prev_job.to_yaml_file(prev_state_dir / 'jira-001.yaml')

        # Update mock_ctx to have prev_state_dirpath
        mock_ctx.prev_state_dirpath = prev_state_dir
        mock_ctx.new_state_dir = True

        jira_none_id = _create_jira_fake_id_generator()

        with pytest.raises(Exception, match="--prev-issue can only be used with a single"):
            _create_simple_jira_job(
                ctx=mock_ctx,
                artifact_job=mock_artifact_job,
                issue=(),
                prev_issue=True,
                job_recipe=(
                    'http://example.com/recipe1.yaml',
                    'http://example.com/recipe2.yaml',
                    ),
                jira_none_id=jira_none_id)

    def test_no_job_recipe_raises_exception(self, mock_ctx, mock_artifact_job):
        """Test that missing --job-recipe raises an exception."""
        jira_none_id = _create_jira_fake_id_generator()

        with pytest.raises(Exception, match="Option --job-recipe is mandatory"):
            _create_simple_jira_job(
                ctx=mock_ctx,
                artifact_job=mock_artifact_job,
                issue=(),
                prev_issue=False,
                job_recipe=(),  # Empty
                jira_none_id=jira_none_id)

    def test_prev_issue_with_single_recipe(self, mock_ctx, mock_artifact_job, tmp_path):
        """Test that --prev-issue works correctly with a single recipe."""
        # Set up a previous state directory with a jira job
        prev_state_dir = tmp_path / 'prev_state'
        prev_state_dir.mkdir()

        # Create a mock previous jira job
        from newa.models.issues import Issue
        from newa.models.jobs import JiraJob
        from newa.models.recipes import Recipe
        prev_job = JiraJob(
            event=Event(id='12345', type_=EventType.ERRATUM),
            erratum=None,
            compose=None,
            rog=None,
            jira=Issue('RHEL-999', summary='Previous Issue', url='http://example.com/RHEL-999'),
            recipe=Recipe(url='http://example.com/prev_recipe.yaml'),
            )
        prev_job.to_yaml_file(prev_state_dir / 'jira-001.yaml')

        # Update mock_ctx to have prev_state_dirpath
        mock_ctx.prev_state_dirpath = prev_state_dir
        mock_ctx.new_state_dir = True

        # Mock the Jira connection
        mock_jira_connection = mock.MagicMock()
        mock_jira_issue = mock.MagicMock()
        mock_jira_issue.fields.summary = 'Previous Issue'
        mock_jira_issue.key = 'RHEL-999'
        mock_jira_connection.get_connection.return_value.issue.return_value = mock_jira_issue

        jira_none_id = _create_jira_fake_id_generator()

        with mock.patch.object(
                type(mock_ctx), 'get_jira_connection',
                return_value=mock_jira_connection):
            _create_simple_jira_job(
                ctx=mock_ctx,
                artifact_job=mock_artifact_job,
                issue=(),
                prev_issue=True,
                job_recipe=('http://example.com/recipe.yaml',),
                jira_none_id=jira_none_id)

        # Verify one jira job file was created with the previous issue
        jira_job_files = list(Path(mock_ctx.state_dirpath).glob('jira-*'))
        assert len(jira_job_files) == 1

        from newa.models.jobs import JiraJob
        job = JiraJob.from_yaml_file(jira_job_files[0])
        assert job.jira.id == 'RHEL-999'
