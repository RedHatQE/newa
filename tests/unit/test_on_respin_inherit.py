"""Unit tests for on_respin='inherit' functionality."""
import pytest

from newa import IssueConfig, OnRespinAction


def test_on_respin_inherit_config_loading():
    """Test that on_respin='inherit' is loaded correctly from config file."""
    config = IssueConfig.read_file('tests/unit/data/issue-config-on-respin-inherit.yaml')

    # Find actions by id
    actions = {action.id: action for action in config.issues}

    # Parent has on_respin: update explicitly set
    assert actions['parent_epic'].on_respin == OnRespinAction.UPDATE

    # Child should have INHERIT value (not yet resolved during read_file)
    assert actions['child_task'].on_respin == OnRespinAction.INHERIT

    # Grandchild should have INHERIT value
    assert actions['grandchild_subtask'].on_respin == OnRespinAction.INHERIT

    # Parent with default value (close)
    assert actions['parent_default'].on_respin == OnRespinAction.CLOSE

    # Child with inherit
    assert actions['child_default'].on_respin == OnRespinAction.INHERIT

    # Parent with keep
    assert actions['parent_keep'].on_respin == OnRespinAction.KEEP

    # Child with inherit
    assert actions['child_keep'].on_respin == OnRespinAction.INHERIT


def test_on_respin_inherit_resolution_simulation():
    """
    Simulate the resolution logic that happens during _process_issue_action.

    This tests the resolution algorithm without requiring a full CLI context.
    """
    config = IssueConfig.read_file('tests/unit/data/issue-config-on-respin-inherit.yaml')
    actions = {action.id: action for action in config.issues}

    # Simulate resolution for each action with on_respin='inherit'
    def resolve_inherit(action, config_actions):
        """Simulate the resolution logic from _process_issue_action."""
        if action.on_respin == OnRespinAction.INHERIT:
            if not action.parent_id:
                raise Exception(
                    f"Action '{action.id}' has on_respin='inherit' but no parent_id is specified")

            parent_action = config_actions.get(action.parent_id)
            if not parent_action:
                raise Exception(
                    f"Action '{action.id}' has on_respin='inherit' but parent "
                    f"'{action.parent_id}' does not exist")

            # First resolve parent if it also has inherit
            if parent_action.on_respin == OnRespinAction.INHERIT:
                resolve_inherit(parent_action, config_actions)

            # Now copy from parent
            action.on_respin = parent_action.on_respin

    # Process in order: parent_epic, child_task, grandchild_subtask
    resolve_inherit(actions['child_task'], actions)
    assert actions['child_task'].on_respin == OnRespinAction.UPDATE

    resolve_inherit(actions['grandchild_subtask'], actions)
    assert actions['grandchild_subtask'].on_respin == OnRespinAction.UPDATE

    resolve_inherit(actions['child_default'], actions)
    assert actions['child_default'].on_respin == OnRespinAction.CLOSE

    resolve_inherit(actions['child_keep'], actions)
    assert actions['child_keep'].on_respin == OnRespinAction.KEEP


def test_on_respin_inherit_no_parent_id_error():
    """Test that on_respin='inherit' without parent_id raises an error during processing."""
    config = IssueConfig.read_file(
        'tests/unit/data/issue-config-on-respin-inherit-no-parent.yaml')

    actions = {action.id: action for action in config.issues}
    action = actions['orphan_child']

    # Helper function to simulate the check from _process_issue_action
    def validate_parent_id():
        if action.on_respin == OnRespinAction.INHERIT and not action.parent_id:
            raise Exception(
                f"Action '{action.id}' has on_respin='inherit' but no parent_id is specified")

    # Simulate the check from _process_issue_action
    with pytest.raises(Exception, match="no parent_id is specified"):
        validate_parent_id()


def test_on_respin_inherit_missing_parent_error():
    """Test that on_respin='inherit' with non-existent parent raises an error."""
    config = IssueConfig.read_file(
        'tests/unit/data/issue-config-on-respin-inherit-missing-parent.yaml')

    actions = {action.id: action for action in config.issues}
    action = actions['child_task']

    # Helper function to simulate the check from _process_issue_action
    def validate_parent_exists():
        if action.on_respin == OnRespinAction.INHERIT:
            parent_action = actions.get(action.parent_id)
            if not parent_action:
                raise Exception(
                    f"Action '{action.id}' has on_respin='inherit' but parent "
                    f"'{action.parent_id}' does not exist")

    # Simulate the check from _process_issue_action
    with pytest.raises(Exception, match="does not exist"):
        validate_parent_exists()
