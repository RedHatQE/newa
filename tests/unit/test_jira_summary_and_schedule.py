"""Tests for Jira issue summary population and schedule/recipe functionality."""

import re
from pathlib import Path
from unittest import mock

import pytest

from newa import (
    ArtifactJob,
    CLIContext,
    Event,
    EventType,
    Issue,
    IssueAction,
    IssueConfig,
    JiraJob,
    Settings,
    )
from newa.cli.jira_helpers import _create_jira_job_from_action, _process_issue_action


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


@pytest.fixture
def mock_jira_handler():
    """Return a mock IssueHandler."""
    handler = mock.MagicMock()
    handler.transitions.closed = ['Closed']
    handler.transitions.passed = ['Verified']
    handler.transitions.processed = ['In Progress']
    return handler


@pytest.fixture
def mock_issue_config():
    """Return a basic IssueConfig."""
    return IssueConfig(
        project='TEST',
        transitions={
            'closed': ['Closed'],
            'dropped': ['Dropped'],
            'processed': ['In Progress'],
            'passed': ['Verified'],
            },
        issues=[],
        )


class TestSummaryFieldPopulation:
    """Test suite for summary field population in Issue objects."""

    def test_summary_in_get_related_issues_result(self):
        """Test that get_related_issues includes summary in results."""
        from newa.services.jira_connection import JiraConnection
        from newa.services.jira_service import IssueHandler

        # Create a real JiraConnection (won't connect since we mock the underlying connection)
        # Use a non-Cloud URL so is_cloud=False and sanitize_comment returns text unchanged
        real_connection = JiraConnection(
            url='http://jira.example.com',
            token='dummy_token',
            )

        # Mock the underlying _connection to avoid actual Jira connection
        mock_jira = mock.MagicMock()
        # Mock search result
        search_result = {
            "issues": [{
                "key": "TEST-123",
                "fields": {
                    "description": "::: NEWA test_action: job_id",
                    "status": {"name": "Open"},
                    "updated": "2026-03-31T10:00:00.000+0000",
                    "summary": "Test Issue Summary",
                    },
                }],
            }
        mock_jira.search_issues.return_value = search_result

        # Set the _connection directly to bypass lazy initialization
        real_connection._connection = mock_jira

        # Create handler
        mock_artifact_job = mock.MagicMock()
        mock_artifact_job.id = "job_id"
        mock_artifact_job.event.type_ = EventType.ERRATUM
        mock_artifact_job.erratum = None

        handler = IssueHandler(
            artifact_job=mock_artifact_job,
            jira_connection=real_connection,
            project='TEST',
            transitions={'closed': ['Closed'], 'dropped': ['Dropped']},
            )

        action = IssueAction(id='test_action', summary='Test')

        # Get related issues
        result = handler.get_related_issues(action, all_respins=True, closed=True)

        # Verify summary is in result
        assert 'TEST-123' in result
        assert 'summary' in result['TEST-123']
        assert result['TEST-123']['summary'] == "Test Issue Summary"

    def test_issue_created_with_summary(self, mock_ctx, mock_artifact_job):
        """Test that Issue objects are created with summary from search results."""
        # Mock search result with summary
        mock_jira_issue = {
            "description": "::: NEWA test_action: job_id",
            "status": "opened",
            "summary": "Existing Issue Summary",
            "updated": "2026-03-31T10:00:00.000+0000",
            }

        # We'll test the Issue creation directly
        from newa.models.issues import Issue

        issue = Issue(
            "TEST-123",
            group="test-group",
            summary=mock_jira_issue.get("summary", ""),
            closed=False,
            )

        assert issue.summary == "Existing Issue Summary"
        assert issue.id == "TEST-123"


class TestScheduleAndRecipeFunctionality:
    """Test suite for schedule attribute and recipe saving functionality."""

    def test_jira_job_created_with_recipe_when_schedule_true(
            self, mock_ctx, mock_artifact_job):
        """Test that JiraJob is created with recipe when schedule=True."""
        action = IssueAction(
            id='test_action',
            summary='Test Action',
            job_recipe='http://example.com/recipe.yaml',
            schedule=True,
            )

        mock_issue = Issue('TEST-123')

        # Call _create_jira_job_from_action with save_recipe=True
        _create_jira_job_from_action(
            ctx=mock_ctx,
            action=action,
            artifact_job=mock_artifact_job,
            jira_event_fields={},
            new_issue=mock_issue,
            save_recipe=True,
            )

        # Verify jira job file was created
        jira_job_files = list(Path(mock_ctx.state_dirpath).glob('jira-*'))
        assert len(jira_job_files) == 1

        # Load and verify the jira job has a recipe
        jira_job = JiraJob.from_yaml_file(jira_job_files[0])
        assert jira_job.recipe is not None
        assert jira_job.recipe.url == 'http://example.com/recipe.yaml'

    def test_jira_job_created_without_recipe_when_schedule_false(
            self, mock_ctx, mock_artifact_job):
        """Test that JiraJob is created without recipe when schedule=False."""
        action = IssueAction(
            id='test_action',
            summary='Test Action',
            job_recipe='http://example.com/recipe.yaml',
            schedule=False,
            )

        mock_issue = Issue('TEST-123')

        # Call _create_jira_job_from_action with save_recipe=False
        _create_jira_job_from_action(
            ctx=mock_ctx,
            action=action,
            artifact_job=mock_artifact_job,
            jira_event_fields={},
            new_issue=mock_issue,
            save_recipe=False,
            )

        # Verify jira job file was created
        jira_job_files = list(Path(mock_ctx.state_dirpath).glob('jira-*'))
        assert len(jira_job_files) == 1

        # Load and verify the jira job has NO recipe
        jira_job = JiraJob.from_yaml_file(jira_job_files[0])
        assert jira_job.recipe is None

    def test_schedule_false_logs_correct_message(
            self, mock_ctx, mock_artifact_job, mock_jira_handler, mock_issue_config):
        """Test that schedule=False logs the correct message."""
        action = IssueAction(
            id='test_action',
            summary='Test Action',
            job_recipe='http://example.com/recipe.yaml',
            schedule=False,
            )

        mock_issue = Issue('TEST-123')

        with mock.patch('newa.cli.jira_helpers._render_action_fields') as mock_render, \
                mock.patch('newa.cli.jira_helpers._find_or_create_issue') as mock_find_create:

            mock_render.return_value = ('summary', 'description', None, {}, {}, False)
            mock_find_create.return_value = (mock_issue, [], False)

            # Process the action
            _process_issue_action(
                ctx=mock_ctx,
                action=action,
                artifact_job=mock_artifact_job,
                jira_handler=mock_jira_handler,
                config=mock_issue_config,
                jira_event_fields={},
                issue_mapping={},
                no_newa_id=False,
                recreate=False,
                assignee=None,
                unassigned=False,
                processed_actions={},
                created_action_ids=[],
                et=None,
                rog=None,
                )

            # Verify the correct log message
            mock_ctx.logger.info.assert_any_call(
                "Issue TEST-123 will not be scheduled automatically.",
                )

    def test_schedule_false_overridden_by_action_id_filter(
            self, mock_ctx, mock_artifact_job, mock_jira_handler, mock_issue_config):
        """Test that schedule=False is overridden when action_id_filter matches."""
        # Set action_id_filter_pattern on context
        mock_ctx.action_id_filter_pattern = re.compile(r'test_action')

        action = IssueAction(
            id='test_action',
            summary='Test Action',
            job_recipe='http://example.com/recipe.yaml',
            schedule=False,
            )

        mock_issue = Issue('TEST-123')

        with mock.patch('newa.cli.jira_helpers._render_action_fields') as mock_render, \
                mock.patch('newa.cli.jira_helpers._find_or_create_issue') as mock_find_create:

            mock_render.return_value = ('summary', 'description', None, {}, {}, False)
            mock_find_create.return_value = (mock_issue, [], False)

            # Process the action
            _process_issue_action(
                ctx=mock_ctx,
                action=action,
                artifact_job=mock_artifact_job,
                jira_handler=mock_jira_handler,
                config=mock_issue_config,
                jira_event_fields={},
                issue_mapping={},
                no_newa_id=False,
                recreate=False,
                assignee=None,
                unassigned=False,
                processed_actions={},
                created_action_ids=[],
                et=None,
                rog=None,
                )

            # Verify the override log message
            mock_ctx.logger.info.assert_any_call(
                "Issue TEST-123 will be scheduled (overridden by filter).",
                )

        # Verify jira job has recipe (despite schedule=False)
        jira_job_files = list(Path(mock_ctx.state_dirpath).glob('jira-*'))
        assert len(jira_job_files) == 1
        jira_job = JiraJob.from_yaml_file(jira_job_files[0])
        assert jira_job.recipe is not None

    def test_schedule_false_overridden_by_issue_id_filter(
            self, mock_ctx, mock_artifact_job, mock_jira_handler, mock_issue_config):
        """Test that schedule=False is overridden when issue_id_filter matches."""
        # Set issue_id_filter_pattern on context
        mock_ctx.issue_id_filter_pattern = re.compile(r'TEST-123')

        action = IssueAction(
            id='test_action',
            summary='Test Action',
            job_recipe='http://example.com/recipe.yaml',
            schedule=False,
            )

        mock_issue = Issue('TEST-123')

        with mock.patch('newa.cli.jira_helpers._render_action_fields') as mock_render, \
                mock.patch('newa.cli.jira_helpers._find_or_create_issue') as mock_find_create:

            mock_render.return_value = ('summary', 'description', None, {}, {}, False)
            mock_find_create.return_value = (mock_issue, [], False)

            # Process the action
            _process_issue_action(
                ctx=mock_ctx,
                action=action,
                artifact_job=mock_artifact_job,
                jira_handler=mock_jira_handler,
                config=mock_issue_config,
                jira_event_fields={},
                issue_mapping={},
                no_newa_id=False,
                recreate=False,
                assignee=None,
                unassigned=False,
                processed_actions={},
                created_action_ids=[],
                et=None,
                rog=None,
                )

            # Verify the override log message
            mock_ctx.logger.info.assert_any_call(
                "Issue TEST-123 will be scheduled (overridden by filter).",
                )

        # Verify jira job has recipe (despite schedule=False)
        jira_job_files = list(Path(mock_ctx.state_dirpath).glob('jira-*'))
        assert len(jira_job_files) == 1
        jira_job = JiraJob.from_yaml_file(jira_job_files[0])
        assert jira_job.recipe is not None

    def test_schedule_command_skips_jobs_without_recipe(self, mock_ctx, tmp_path):
        """Test that schedule command skips jira jobs without recipes."""
        from newa.cli.schedule_helpers import _process_jira_job

        # Create a jira job without recipe
        jira_job = JiraJob(
            event=Event(id='12345', type_=EventType.ERRATUM),
            erratum=None,
            compose=None,
            rog=None,
            jira=Issue('TEST-123', summary='Test Issue'),
            recipe=None,  # No recipe
            )

        # Process should return early and log skip message
        _process_jira_job(
            ctx=mock_ctx,
            jira_job=jira_job,
            arch_options=[],
            fixtures=[],
            no_reportportal=False,
            )

        # Verify log message
        mock_ctx.logger.info.assert_called_with(
            'Skipping jira job TEST-123 - no recipe specified',
            )

        # Verify no schedule job files were created
        schedule_job_files = list(Path(mock_ctx.state_dirpath).glob('schedule-*'))
        assert len(schedule_job_files) == 0

    def test_jira_job_always_created_regardless_of_schedule(
            self, mock_ctx, mock_artifact_job):
        """Test that JiraJob YAML is always created, even when schedule=False."""
        action_with_schedule_true = IssueAction(
            id='action_true',
            summary='Action True',
            job_recipe='http://example.com/recipe.yaml',
            schedule=True,
            )

        action_with_schedule_false = IssueAction(
            id='action_false',
            summary='Action False',
            job_recipe='http://example.com/recipe.yaml',
            schedule=False,
            )

        # Create jobs for both actions
        _create_jira_job_from_action(
            ctx=mock_ctx,
            action=action_with_schedule_true,
            artifact_job=mock_artifact_job,
            jira_event_fields={},
            new_issue=Issue('TEST-123'),
            save_recipe=True,
            )

        _create_jira_job_from_action(
            ctx=mock_ctx,
            action=action_with_schedule_false,
            artifact_job=mock_artifact_job,
            jira_event_fields={},
            new_issue=Issue('TEST-124'),
            save_recipe=False,
            )

        # Verify both jira job files were created
        jira_job_files = list(Path(mock_ctx.state_dirpath).glob('jira-*'))
        assert len(jira_job_files) == 2

    def test_action_without_job_recipe_creates_job_without_recipe(
            self, mock_ctx, mock_artifact_job):
        """Test that action without job_recipe creates JiraJob with recipe=None."""
        action = IssueAction(
            id='test_action',
            summary='Test Action',
            job_recipe=None,  # No recipe
            schedule=True,
            )

        mock_issue = Issue('TEST-123')

        # Create jira job
        _create_jira_job_from_action(
            ctx=mock_ctx,
            action=action,
            artifact_job=mock_artifact_job,
            jira_event_fields={},
            new_issue=mock_issue,
            save_recipe=True,
            )

        # Verify jira job file was created
        jira_job_files = list(Path(mock_ctx.state_dirpath).glob('jira-*'))
        assert len(jira_job_files) == 1

        # Load and verify the jira job has NO recipe
        jira_job = JiraJob.from_yaml_file(jira_job_files[0])
        assert jira_job.recipe is None
