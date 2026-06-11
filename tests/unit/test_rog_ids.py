"""Tests for RoG ID generation (short_id and newa_id)."""

from unittest import mock

import pytest

from newa import (
    ArtifactJob,
    Compose,
    Event,
    EventType,
    IssueAction,
    RoG,
    )
from newa.models.artifacts import ErratumContentType
from newa.services.jira_connection import JiraConnection
from newa.services.jira_service import IssueHandler


class TestRoGShortId:
    """Test suite for RoG ArtifactJob.short_id property."""

    def test_rog_short_id_returns_build_target(self):
        """Test that RoG short_id returns build_target."""
        rog = RoG(
            id='https://gitlab.com/redhat/centos-stream/rpms/bash/-/merge_requests/123',
            content_type=ErratumContentType.RPM,
            title='Test MR',
            build_task_id='54321',
            build_target='c9s-candidate',
            archs=[],
            builds=['bash-5.1.8-9.el9'],
            components=['bash'],
            )

        event = Event(
            type_=EventType.ROG,
            id='https://gitlab.com/redhat/centos-stream/rpms/bash/-/merge_requests/123',
            )

        job = ArtifactJob(
            event=event,
            erratum=None,
            compose=None,
            rog=rog,
            )

        assert job.short_id == 'c9s-candidate'

    def test_rog_short_id_preserves_draft_suffix(self):
        """Test that RoG short_id keeps -draft suffix."""
        rog = RoG(
            id='https://gitlab.com/redhat/centos-stream/rpms/bash/-/merge_requests/123',
            content_type=ErratumContentType.RPM,
            title='Test MR',
            build_task_id='54321',
            build_target='c9s-candidate-draft',
            archs=[],
            builds=['bash-5.1.8-9.el9'],
            components=['bash'],
            )

        event = Event(
            type_=EventType.ROG,
            id='https://gitlab.com/redhat/centos-stream/rpms/bash/-/merge_requests/123',
            )

        job = ArtifactJob(
            event=event,
            erratum=None,
            compose=None,
            rog=rog,
            )

        assert job.short_id == 'c9s-candidate-draft'

    def test_rog_short_id_takes_precedence_over_compose(self):
        """Test that RoG short_id uses build_target even when compose is set."""
        rog = RoG(
            id='https://gitlab.com/redhat/centos-stream/rpms/bash/-/merge_requests/123',
            content_type=ErratumContentType.RPM,
            title='Test MR',
            build_task_id='54321',
            build_target='c9s-candidate',
            archs=[],
            builds=['bash-5.1.8-9.el9'],
            components=['bash'],
            )

        event = Event(
            type_=EventType.ROG,
            id='https://gitlab.com/redhat/centos-stream/rpms/bash/-/merge_requests/123',
            )

        # Both rog and compose are set (as happens in real code)
        job = ArtifactJob(
            event=event,
            erratum=None,
            compose=Compose(id='RHEL-9.9.0-Nightly'),
            rog=rog,
            )

        # Should use rog.build_target, not compose.id
        assert job.short_id == 'c9s-candidate'

    def test_rog_artifact_job_id_format(self):
        """Test that RoG ArtifactJob.id has correct format."""
        rog = RoG(
            id='https://gitlab.com/redhat/centos-stream/rpms/bash/-/merge_requests/123',
            content_type=ErratumContentType.RPM,
            title='Test MR',
            build_task_id='54321',
            build_target='c9s-candidate',
            archs=[],
            builds=['bash-5.1.8-9.el9'],
            components=['bash'],
            )

        event = Event(
            type_=EventType.ROG,
            id='https://gitlab.com/redhat/centos-stream/rpms/bash/-/merge_requests/123',
            )

        job = ArtifactJob(
            event=event,
            erratum=None,
            compose=None,
            rog=rog,
            )

        # Format should be: "E: {event.short_id} @ {job.short_id}"
        assert job.id == 'E: bash_MR_123 @ c9s-candidate'


class TestRoGNewaId:
    """Test suite for RoG newa_id generation."""

    @pytest.fixture
    def rog_artifact_job(self):
        """Return a RoG ArtifactJob for testing."""
        rog = RoG(
            id='https://gitlab.com/redhat/centos-stream/rpms/bash/-/merge_requests/123',
            content_type=ErratumContentType.RPM,
            title='Test MR',
            build_task_id='54321',
            build_target='c9s-candidate',
            archs=[],
            builds=['bash-5.1.8-9.el9'],
            components=['bash'],
            )

        event = Event(
            type_=EventType.ROG,
            id='https://gitlab.com/redhat/centos-stream/rpms/bash/-/merge_requests/123',
            )

        return ArtifactJob(
            event=event,
            erratum=None,
            compose=Compose(id='RHEL-9.9.0-Nightly'),
            rog=rog,
            )

    @pytest.fixture
    def issue_handler(self, rog_artifact_job):
        """Return an IssueHandler for testing newa_id."""
        jira_conn = JiraConnection(
            url='http://jira.example.com',
            token='dummy_token',
            )

        # Mock the underlying connection to avoid actual Jira connection
        jira_conn._connection = mock.MagicMock()

        return IssueHandler(
            artifact_job=rog_artifact_job,
            jira_connection=jira_conn,
            project='TEST',
            transitions={'closed': ['Closed'], 'dropped': ['Dropped']},
            )

    def test_rog_newa_id_full_includes_task_id(self, issue_handler):
        """Test that full newa_id (partial=False) includes task ID."""
        action = IssueAction(id='test-action')

        newa_id = issue_handler.newa_id(action, partial=False)

        # Should include task ID in format: (task {build_task_id})
        assert '(task 54321)' in newa_id
        assert ':::' in newa_id  # Should have closing :::
        expected = '::: NEWA test-action: E: bash_MR_123 @ c9s-candidate (task 54321) :::'
        assert newa_id == expected

    def test_rog_newa_id_partial_excludes_task_id(self, issue_handler):
        """Test that partial newa_id (partial=True) excludes task ID."""
        action = IssueAction(id='test-action')

        newa_id = issue_handler.newa_id(action, partial=True)

        # Should NOT include task ID or closing :::
        assert '(task' not in newa_id
        assert newa_id.endswith('c9s-candidate')
        expected = '::: NEWA test-action: E: bash_MR_123 @ c9s-candidate'
        assert newa_id == expected

    def test_rog_newa_id_differentiates_different_builds(self):
        """Test that different build task IDs create different newa_ids."""
        # Create two artifact jobs with same MR but different build task IDs
        def create_job(task_id):
            rog = RoG(
                id='https://gitlab.com/redhat/centos-stream/rpms/bash/-/merge_requests/123',
                content_type=ErratumContentType.RPM,
                title='Test MR',
                build_task_id=task_id,
                build_target='c9s-candidate',
                archs=[],
                builds=['bash-5.1.8-9.el9'],
                components=['bash'],
                )

            event = Event(
                type_=EventType.ROG,
                id='https://gitlab.com/redhat/centos-stream/rpms/bash/-/merge_requests/123',
                )

            return ArtifactJob(
                event=event,
                erratum=None,
                compose=Compose(id='RHEL-9.9.0-Nightly'),
                rog=rog,
                )

        job1 = create_job('54321')
        job2 = create_job('54322')

        jira_conn = JiraConnection(
            url='http://jira.example.com',
            token='dummy_token',
            )
        jira_conn._connection = mock.MagicMock()

        handler1 = IssueHandler(
            artifact_job=job1,
            jira_connection=jira_conn,
            project='TEST',
            transitions={'closed': ['Closed'], 'dropped': ['Dropped']},
            )

        handler2 = IssueHandler(
            artifact_job=job2,
            jira_connection=jira_conn,
            project='TEST',
            transitions={'closed': ['Closed'], 'dropped': ['Dropped']},
            )

        action = IssueAction(id='test-action')

        newa_id1 = handler1.newa_id(action, partial=False)
        newa_id2 = handler2.newa_id(action, partial=False)

        # newa_ids should be different
        assert newa_id1 != newa_id2
        assert '(task 54321)' in newa_id1
        assert '(task 54322)' in newa_id2

        # But partial newa_ids should be the same (used for finding all respins)
        partial_id1 = handler1.newa_id(action, partial=True)
        partial_id2 = handler2.newa_id(action, partial=True)
        assert partial_id1 == partial_id2

    def test_rog_newa_id_with_custom_action_newa_id(self, issue_handler):
        """Test that custom action.newa_id is used if provided."""
        action = IssueAction(id='test-action', newa_id='custom-id')

        newa_id = issue_handler.newa_id(action, partial=False)

        # Should use custom newa_id instead of action.id
        assert newa_id == '::: NEWA custom-id'
        # Should NOT include task ID when custom newa_id is provided
        assert '(task' not in newa_id

    def test_rog_newa_id_no_action(self, issue_handler):
        """Test newa_id without action returns just label."""
        newa_id = issue_handler.newa_id(None)

        assert newa_id == '::: NEWA'


class TestRoGEventShortId:
    """Test suite for RoG Event.short_id property."""

    def test_event_short_id_extracts_component_and_mr_number(self):
        """Test that Event.short_id extracts component and MR number from GitLab URL."""
        event = Event(
            type_=EventType.ROG,
            id='https://gitlab.com/redhat/centos-stream/rpms/bash/-/merge_requests/123',
            )

        assert event.short_id == 'bash_MR_123'

    def test_event_short_id_with_trailing_slash(self):
        """Test Event.short_id handles trailing slash in URL."""
        event = Event(
            type_=EventType.ROG,
            id='https://gitlab.com/redhat/centos-stream/rpms/keylime/-/merge_requests/85/',
            )

        assert event.short_id == 'keylime_MR_85'

    def test_event_short_id_with_different_project_structure(self):
        """Test Event.short_id with different GitLab project structure."""
        event = Event(
            type_=EventType.ROG,
            id='https://gitlab.com/group/subgroup/project/rpms/systemd/-/merge_requests/456',
            )

        # Should extract component (4th from end) and MR number (last)
        assert event.short_id == 'systemd_MR_456'
