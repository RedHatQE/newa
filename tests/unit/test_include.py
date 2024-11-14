from newa import IssueConfig, IssueType

c = IssueConfig.read_file('tests/unit/data/issue-config-include-parent.yaml')


def test_include_on_defaults():
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


def test_defaults_override_on_issue():
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
