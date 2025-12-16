"""Tests for IssueAction schedule attribute functionality."""

import re
from pathlib import Path
from unittest import mock

import pytest

from newa import ArtifactJob, CLIContext, Event, EventType, IssueAction, IssueConfig, Settings
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


class TestActionScheduleAttribute:
    """Test suite for the schedule attribute on IssueAction."""

    def test_action_schedule_default_true(self):
        """Test that schedule attribute defaults to True."""
        action = IssueAction(
            id='test_action',
            summary='Test Action',
            )
        assert action.schedule is True

    def test_action_schedule_false(self):
        """Test that schedule attribute can be set to False."""
        action = IssueAction(
            id='test_action',
            summary='Test Action',
            schedule=False,
            )
        assert action.schedule is False

    def test_create_jira_job_not_called_when_schedule_false(
            self, mock_ctx, mock_artifact_job, mock_jira_handler, mock_issue_config,
            ):
        """Test that _create_jira_job_from_action is not called when schedule=False."""
        action = IssueAction(
            id='test_action',
            summary='Test Action',
            job_recipe='http://example.com/recipe.yaml',
            schedule=False,
            )

        # Mock the issue object that would be created/found
        mock_issue = mock.MagicMock()
        mock_issue.id = 'TEST-123'

        # Mock necessary dependencies
        with mock.patch('newa.cli.jira_helpers._render_action_fields') as mock_render, \
                mock.patch('newa.cli.jira_helpers._find_or_create_issue') as mock_find_create, \
                mock.patch('newa.cli.jira_helpers._create_jira_job_from_action') as \
                mock_create_job:

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
                )

            # Verify that _create_jira_job_from_action was NOT called
            mock_create_job.assert_not_called()

            # Verify the log message was generated
            mock_ctx.logger.info.assert_any_call(
                "Not scheduling action 'test_action' as requested.",
                )

    def test_create_jira_job_called_when_schedule_true(
            self, mock_ctx, mock_artifact_job, mock_jira_handler, mock_issue_config,
            ):
        """Test that _create_jira_job_from_action is called when schedule=True."""
        action = IssueAction(
            id='test_action',
            summary='Test Action',
            job_recipe='http://example.com/recipe.yaml',
            schedule=True,
            )

        # Mock the issue object
        mock_issue = mock.MagicMock()
        mock_issue.id = 'TEST-123'

        with mock.patch('newa.cli.jira_helpers._render_action_fields') as mock_render, \
                mock.patch('newa.cli.jira_helpers._find_or_create_issue') as mock_find_create, \
                mock.patch('newa.cli.jira_helpers._create_jira_job_from_action') as \
                mock_create_job:

            mock_render.return_value = ('summary', 'description', None, {}, {}, True)
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
                )

            # Verify that _create_jira_job_from_action WAS called
            mock_create_job.assert_called_once()

    def test_schedule_false_overridden_by_action_id_filter(
            self, mock_ctx, mock_artifact_job, mock_jira_handler, mock_issue_config,
            ):
        """Test that schedule=False is overridden when action_id_filter matches."""
        # Set action_id_filter_pattern on context
        mock_ctx.action_id_filter_pattern = re.compile(r'test_action')

        action = IssueAction(
            id='test_action',
            summary='Test Action',
            job_recipe='http://example.com/recipe.yaml',
            schedule=False,  # Normally would not schedule
            )

        # Mock the issue object
        mock_issue = mock.MagicMock()
        mock_issue.id = 'TEST-123'

        with mock.patch('newa.cli.jira_helpers._render_action_fields') as mock_render, \
                mock.patch('newa.cli.jira_helpers._find_or_create_issue') as mock_find_create, \
                mock.patch('newa.cli.jira_helpers._create_jira_job_from_action') as \
                mock_create_job:

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
                )

            # Verify that _create_jira_job_from_action WAS called despite schedule=False
            mock_create_job.assert_called_once()

    def test_action_without_job_recipe_no_job_created(
            self, mock_ctx, mock_artifact_job,
            ):
        """Test that no job is created when action has no job_recipe."""
        action = IssueAction(
            id='test_action',
            summary='Test Action',
            job_recipe=None,  # No recipe
            schedule=True,
            )

        mock_issue = mock.MagicMock()
        mock_issue.id = 'TEST-123'

        # Call _create_jira_job_from_action directly
        _create_jira_job_from_action(
            ctx=mock_ctx,
            action=action,
            artifact_job=mock_artifact_job,
            jira_event_fields={},
            new_issue=mock_issue,
            )

        # Verify no jira job file was created
        jira_job_files = list(Path(mock_ctx.state_dirpath).glob('jira-*'))
        assert len(jira_job_files) == 0

    def test_schedule_attribute_in_yaml_config(self, tmp_path):
        """Test that schedule attribute can be loaded from YAML config."""
        config_content = """
project: TEST
transitions:
  closed: [Closed]
  dropped: [Dropped]
  processed: [In Progress]
  passed: [Verified]
issues:
  - id: action_with_schedule_false
    summary: Test Action 1
    schedule: false
  - id: action_with_schedule_true
    summary: Test Action 2
    schedule: true
  - id: action_default_schedule
    summary: Test Action 3
"""
        config_file = tmp_path / 'test-config.yaml'
        config_file.write_text(config_content)

        config = IssueConfig.read_file(str(config_file))

        assert config.issues[0].schedule is False
        assert config.issues[1].schedule is True
        assert config.issues[2].schedule is True  # Default

    def test_schedule_null_treated_as_true(self, tmp_path):
        """Test that schedule: null in YAML is treated as True (default)."""
        config_content = """
project: TEST
transitions:
  closed: [Closed]
  dropped: [Dropped]
  processed: [In Progress]
  passed: [Verified]
issues:
  - id: action_with_null_schedule
    summary: Test Action
    schedule: null
"""
        config_file = tmp_path / 'test-config.yaml'
        config_file.write_text(config_content)

        config = IssueConfig.read_file(str(config_file))

        # None should be present in the raw config
        assert config.issues[0].schedule is None

        # But when rendered, it should be treated as True
        from newa.cli.jira_helpers import _render_action_fields
        artifact_job = ArtifactJob(
            event=Event(id='12345', type_=EventType.ERRATUM),
            erratum=None,
            compose=None,
            rog=None,
            )
        _, _, _, _, _, rendered_schedule = _render_action_fields(
            config.issues[0], artifact_job, {}, None, False)
        assert rendered_schedule is True

    def test_schedule_string_whitespace_handling(self):
        """Test that schedule strings with whitespace are handled correctly."""
        from newa.cli.jira_helpers import _render_action_fields

        artifact_job = ArtifactJob(
            event=Event(id='12345', type_=EventType.ERRATUM),
            erratum=None,
            compose=None,
            rog=None,
            )

        # Test with leading/trailing whitespace
        action = IssueAction(
            id='test_action',
            summary='Test Action',
            schedule=' true ',
            )
        _, _, _, _, _, rendered_schedule = _render_action_fields(
            action, artifact_job, {}, None, False)
        assert rendered_schedule is True

        # Test with newline
        action = IssueAction(
            id='test_action',
            summary='Test Action',
            schedule='YES\n',
            )
        _, _, _, _, _, rendered_schedule = _render_action_fields(
            action, artifact_job, {}, None, False)
        assert rendered_schedule is True

        # Test with tab and mixed case
        action = IssueAction(
            id='test_action',
            summary='Test Action',
            schedule='\tTrUe\t',
            )
        _, _, _, _, _, rendered_schedule = _render_action_fields(
            action, artifact_job, {}, None, False)
        assert rendered_schedule is True

        # Test false value with whitespace
        action = IssueAction(
            id='test_action',
            summary='Test Action',
            schedule=' false ',
            )
        _, _, _, _, _, rendered_schedule = _render_action_fields(
            action, artifact_job, {}, None, False)
        assert rendered_schedule is False
