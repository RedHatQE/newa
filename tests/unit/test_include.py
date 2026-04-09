import pytest

from newa import IssueConfig, IssueType, RecipeConfig

c = IssueConfig.read_file('tests/unit/data/issue-config-include-parent.yaml')
r = RecipeConfig.from_yaml_with_includes('tests/unit/data/recipe-include-parent.yaml')
print(r)


def test_issue_config_include_on_defaults():
    # issues come from all 3 files
    assert len(c.issues) == 3
    # project comes form child2
    assert c.project == 'CHILD2PROJECT'
    # transitions are defined in child1
    assert 'Closed' in c.transitions.closed
    # assignee is unset in parent
    assert c.defaults.assignee is None
    # auto_transition comes from child2
    assert c.defaults.auto_transition is True
    # default fields are defined
    assert c.defaults.fields is not None
    # Pool team comes from the parent
    assert c.defaults.fields["Pool Team"] == "parents_team"
    # Story points comes from the child1
    assert c.defaults.fields["Story Points"] == 1


def test_issue_config_defaults_override_on_issue():
    i = c.issues[0]
    # 1st issue action overrides some defaults
    assert i.type == IssueType.EPIC
    assert i.assignee == 'me'
    assert i.fields['Story Points'] == 3
    assert i.fields["Pool Team"] == "parents_team"
    # other issue action does not override defaults
    i = c.issues[1]
    assert i.type == IssueType.TASK
    assert i.assignee is None
    assert i.fields['Story Points'] == 1


def test_recipe_include():
    # LAST_CHILD comes from child2
    assert r.fixtures["environment"]["LAST_CHILD"] == 2
    # DESCRIPTION comes from parent
    assert r.fixtures["environment"]["DESCRIPTION"] == "parent description"
    # tier comes from parent
    assert r.fixtures["context"]["tier"] == 0
    # trigger comes from child1
    assert r.fixtures["context"]["trigger"] == "nightly"
    # verbosity comes from child3
    assert r.fixtures["context"]["verbosity"] == 99


def test_issue_config_conditional_include_enabled():
    # Load config with ENABLE_FEATURE set to True
    config = IssueConfig.read_file(
        'tests/unit/data/issue-config-conditional-parent.yaml',
        variables={'ENABLE_FEATURE': True})

    # Should have 2 issues: 1 from parent + 1 from child1 (always included)
    assert len(config.issues) == 2

    # Project comes from parent
    assert config.project == 'PARENT_PROJECT'

    # Assignee should come from child1 (it overrides the enabled child
    # which has no assignee in defaults). Actually, looking at the order:
    # enabled child is processed first, then child1. Since child1 defines
    # assignee as 'child1', that wins
    assert config.defaults.assignee == 'child1'

    # Environment field should come from enabled child
    assert config.defaults.fields['Environment'] == 'production'

    # Pool Team should come from parent (defined in parent, wins over child1's value)
    assert config.defaults.fields['Pool Team'] == 'parent_team'

    # Story Points should come from child1 (always included)
    assert config.defaults.fields['Story Points'] == 1


def test_issue_config_conditional_include_disabled():
    # Load config with ENABLE_FEATURE set to False
    config = IssueConfig.read_file(
        'tests/unit/data/issue-config-conditional-parent.yaml',
        variables={'ENABLE_FEATURE': False})

    # Should have 2 issues: 1 from parent + 1 from child1
    assert len(config.issues) == 2

    # Project comes from parent
    assert config.project == 'PARENT_PROJECT'

    # Assignee should come from child1 (overrides disabled child)
    assert config.defaults.assignee == 'child1'

    # Environment field should come from disabled child
    assert config.defaults.fields['Environment'] == 'development'

    # Pool Team should come from parent
    assert config.defaults.fields['Pool Team'] == 'parent_team'

    # Story Points should still come from child1 (always included)
    assert config.defaults.fields['Story Points'] == 1


def test_issue_config_conditional_include_with_compose():
    from newa.models.artifacts import Compose
    from newa.models.events import Event

    # Create test data with COMPOSE matching the condition
    config = IssueConfig.read_file(
        'tests/unit/data/issue-config-conditional-multi.yaml',
        variables={
            'COMPOSE': Compose(id='CentOS-Stream-9'),
            'EVENT': Event(type_='compose', id='CentOS-Stream-9'),
            'ERRATUM': None,
            'ROG': None,
            'CONTEXT': {},
            'ENVIRONMENT': {},
            })

    # Should have 1 issue from parent
    assert len(config.issues) == 1
    assert config.project == 'MULTI_TEST_PROJECT'

    # Compose-specific defaults should be loaded
    assert config.defaults.assignee == 'compose_assignee'
    assert config.defaults.fields['Compose Type'] == 'compose-based'

    # Base field should still be present
    assert config.defaults.fields['Base Field'] == 'base_value'

    # Other conditional includes should NOT be loaded
    assert 'Erratum Type' not in config.defaults.fields
    assert 'Context Marker' not in config.defaults.fields
    assert 'Environment Marker' not in config.defaults.fields


def test_issue_config_conditional_include_with_erratum():
    from newa.models.artifacts import Erratum, ErratumContentType
    from newa.models.base import Arch
    from newa.models.events import Event

    # Create test data with ERRATUM matching the condition
    config = IssueConfig.read_file(
        'tests/unit/data/issue-config-conditional-multi.yaml',
        variables={
            'COMPOSE': None,
            'EVENT': Event(type_='erratum', id='RHSA-2024:12345'),
            'ERRATUM': Erratum(
                id='RHSA-2024:12345',
                content_type=ErratumContentType.RPM,
                respin_count=1,
                summary='Test security advisory',
                release='RHEL-9.4.0',
                url='https://errata.example.com/advisory/12345',
                archs=[Arch.X86_64],
                builds=[],
                components=[],
                jira_issues=[],
                people_assigned_to=''),
            'ROG': None,
            'CONTEXT': {},
            'ENVIRONMENT': {},
            })

    # Should have 1 issue from parent
    assert len(config.issues) == 1
    assert config.project == 'MULTI_TEST_PROJECT'

    # Erratum-specific defaults should be loaded
    assert config.defaults.assignee == 'erratum_assignee'
    assert config.defaults.fields['Erratum Type'] == 'security'

    # Base field should still be present
    assert config.defaults.fields['Base Field'] == 'base_value'

    # Other conditional includes should NOT be loaded
    assert 'Compose Type' not in config.defaults.fields
    assert 'Context Marker' not in config.defaults.fields
    assert 'Environment Marker' not in config.defaults.fields


def test_issue_config_conditional_include_with_context():
    from newa.models.events import Event

    # Create test data with CONTEXT matching the condition
    config = IssueConfig.read_file(
        'tests/unit/data/issue-config-conditional-multi.yaml',
        variables={
            'COMPOSE': None,
            'EVENT': Event(type_='compose', id='test'),
            'ERRATUM': None,
            'ROG': None,
            'CONTEXT': {'test_env': 'staging'},
            'ENVIRONMENT': {},
            })

    # Should have 1 issue from parent
    assert len(config.issues) == 1
    assert config.project == 'MULTI_TEST_PROJECT'

    # Context-specific defaults should be loaded
    assert config.defaults.fields['Context Marker'] == 'context-loaded'

    # Base field should still be present
    assert config.defaults.fields['Base Field'] == 'base_value'

    # Other conditional includes should NOT be loaded
    assert 'Compose Type' not in config.defaults.fields
    assert 'Erratum Type' not in config.defaults.fields
    assert 'Environment Marker' not in config.defaults.fields


def test_issue_config_conditional_include_with_environment():
    from newa.models.events import Event

    # Create test data with ENVIRONMENT matching the condition
    config = IssueConfig.read_file(
        'tests/unit/data/issue-config-conditional-multi.yaml',
        variables={
            'COMPOSE': None,
            'EVENT': Event(type_='compose', id='test'),
            'ERRATUM': None,
            'ROG': None,
            'CONTEXT': {},
            'ENVIRONMENT': {'DEPLOY_MODE': 'production'},
            })

    # Should have 1 issue from parent
    assert len(config.issues) == 1
    assert config.project == 'MULTI_TEST_PROJECT'

    # Environment-specific defaults should be loaded
    assert config.defaults.fields['Environment Marker'] == 'environment-loaded'

    # Base field should still be present
    assert config.defaults.fields['Base Field'] == 'base_value'

    # Other conditional includes should NOT be loaded
    assert 'Compose Type' not in config.defaults.fields
    assert 'Erratum Type' not in config.defaults.fields
    assert 'Context Marker' not in config.defaults.fields


def test_issue_config_conditional_include_multiple_matches():
    from newa.models.artifacts import Compose, Erratum, ErratumContentType
    from newa.models.base import Arch
    from newa.models.events import Event

    # Create test data where multiple conditions match
    config = IssueConfig.read_file(
        'tests/unit/data/issue-config-conditional-multi.yaml',
        variables={
            'COMPOSE': Compose(id='CentOS-Stream-9'),
            'EVENT': Event(type_='erratum', id='RHSA-2024:12345'),
            'ERRATUM': Erratum(
                id='RHSA-2024:12345',
                content_type=ErratumContentType.RPM,
                respin_count=1,
                summary='Test security advisory',
                release='RHEL-9.4.0',
                url='https://errata.example.com/advisory/12345',
                archs=[Arch.X86_64],
                builds=[],
                components=[],
                jira_issues=[],
                people_assigned_to=''),
            'ROG': None,
            'CONTEXT': {'test_env': 'staging'},
            'ENVIRONMENT': {'DEPLOY_MODE': 'production'},
            })

    # Should have 1 issue from parent
    assert len(config.issues) == 1
    assert config.project == 'MULTI_TEST_PROJECT'

    # All matching conditionals should be loaded
    assert config.defaults.fields['Compose Type'] == 'compose-based'
    assert config.defaults.fields['Erratum Type'] == 'security'
    assert config.defaults.fields['Context Marker'] == 'context-loaded'
    assert config.defaults.fields['Environment Marker'] == 'environment-loaded'

    # Base field should still be present
    assert config.defaults.fields['Base Field'] == 'base_value'

    # Assignee should come from the last matching include (erratum)
    # because includes are processed in order
    assert config.defaults.assignee == 'erratum_assignee'


def test_issue_config_conditional_include_no_matches():
    from newa.models.events import Event

    # Create test data where no conditions match
    config = IssueConfig.read_file(
        'tests/unit/data/issue-config-conditional-multi.yaml',
        variables={
            'COMPOSE': None,
            'EVENT': Event(type_='compose', id='test'),
            'ERRATUM': None,
            'ROG': None,
            'CONTEXT': {'test_env': 'development'},  # Doesn't match 'staging'
            'ENVIRONMENT': {'DEPLOY_MODE': 'test'},  # Doesn't match 'production'
            })

    # Should have 1 issue from parent
    assert len(config.issues) == 1
    assert config.project == 'MULTI_TEST_PROJECT'

    # Only base field should be present (no conditional includes loaded)
    assert config.defaults.fields['Base Field'] == 'base_value'
    assert 'Compose Type' not in config.defaults.fields
    assert 'Erratum Type' not in config.defaults.fields
    assert 'Context Marker' not in config.defaults.fields
    assert 'Environment Marker' not in config.defaults.fields

    # No assignee should be set
    assert config.defaults.assignee is None


def test_issue_config_include_dict_missing_url_raises(tmp_path):
    """Test that include entry dict without 'url' key raises an exception."""
    config_path = tmp_path / "issue-config-missing-url.yaml"
    config_path.write_text(
        """
project: TEST_PROJECT
transitions:
  closed:
    - Closed
  dropped:
    - Dropped
include:
  - foo: "bar"
issues:
  - summary: "Test issue"
    description: "Test"
    type: task
    id: test_issue
""",
        encoding="utf-8",
        )

    with pytest.raises(Exception, match=r"Include entry must have 'url' key"):
        IssueConfig.read_file(str(config_path))


def test_issue_config_include_invalid_type_raises(tmp_path):
    """Test that include entry of unsupported type raises an exception."""
    config_path = tmp_path / "issue-config-invalid-type.yaml"
    config_path.write_text(
        """
project: TEST_PROJECT
transitions:
  closed:
    - Closed
  dropped:
    - Dropped
include:
  - 123
issues:
  - summary: "Test issue"
    description: "Test"
    type: task
    id: test_issue
""",
        encoding="utf-8",
        )

    with pytest.raises(Exception, match=r"Include entry must be a string or dict, got"):
        IssueConfig.read_file(str(config_path))


def test_issue_config_dict_style_include_without_when():
    """Test that dict-style include with only 'url' (no 'when') is always loaded."""
    from newa.models.events import Event

    # Dict-style include with only "url" must always be loaded, regardless of
    # variables that would otherwise affect "when" conditions for other includes.
    config = IssueConfig.read_file(
        'tests/unit/data/issue-config-dict-include-no-when.yaml',
        variables={
            # Use a mix of values to simulate a real evaluation context; these
            # should not affect the unconditional dict-style include.
            'COMPOSE': None,
            'EVENT': Event(type_='compose', id='test'),
            'ERRATUM': None,
            'ROG': None,
            'CONTEXT': {},
            'ENVIRONMENT': {},
            },
        )

    # Verify the config loaded correctly
    assert config.project == 'DICT_NO_WHEN_PROJECT'
    assert len(config.issues) == 1

    # Fields from the unconditional dict-style include must always be present
    assert config.defaults.fields['Unconditional Field'] == 'always_loaded'
    assert config.defaults.fields['Another Field'] == 'also_loaded'

    # Base field from parent should also be present
    assert config.defaults.fields['Base Field'] == 'from_parent'
