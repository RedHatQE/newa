from pathlib import Path

from newa import RecipeConfig


def test_recipeconfig_ok():
    config = RecipeConfig.from_yaml_file(Path('tests/unit/data/sample_recipe.yaml').absolute())
    reqs = list(config.build_requests({}, {}))

    # Check generated requests are correct
    assert len(reqs) == 4
    assert all('arch' in r.context for r in reqs)
    assert all('distro' in r.context for r in reqs)
    assert all('fips' in r.context for r in reqs)
    assert all('FIPS' in r.environment for r in reqs)
    assert all(r.testingfarm['cli_args'] == "-c trigger=newa" for r in reqs)
    # Assert recipe id uniqueness
    assert len(reqs) == len({r.id for r in reqs})


def test_dimension_override():
    config = RecipeConfig.from_yaml_file(Path('tests/unit/data/sample_recipe.yaml').absolute())
    reqs = list(config.build_requests(initial_config={}, cli_config={}))

    assert reqs[0].environment['DESCRIPTION'] == "adjustments description"
    assert reqs[0].compose == "Fedora-fix"
    assert reqs[2].environment['DESCRIPTION'] == "fixtures description"
    assert reqs[2].compose == "Fedora-fix"
    assert reqs[-1].environment['DESCRIPTION'] == "dimensions description"
    assert reqs[-1].compose == "Fedora-dim"


def test_initial_config_override():
    config = RecipeConfig.from_yaml_file(Path('tests/unit/data/sample_recipe.yaml').absolute())
    reqs = list(
        config.build_requests(
            initial_config={
                'environment': {
                    'DESCRIPTION': 'initial'},
                'compose': 'Fedora-init'},
            cli_config={}))

    assert reqs[0].environment['DESCRIPTION'] == "adjustments description"
    assert reqs[0].compose == "Fedora-fix"
    assert reqs[2].environment['DESCRIPTION'] == "fixtures description"
    assert reqs[-1].environment['DESCRIPTION'] == "dimensions description"
    assert reqs[-1].compose == "Fedora-dim"
    assert all(r.environment['DESCRIPTION'] != "initial" for r in reqs)
    assert all(r.compose != "Fedora-init" for r in reqs)


def test_cli_config_override():
    config = RecipeConfig.from_yaml_file(Path('tests/unit/data/sample_recipe.yaml').absolute())
    reqs = list(
        config.build_requests(
            initial_config={
                'environment': {
                    'DESCRIPTION': 'initial'}},
            cli_config={
                'environment': {
                    'DESCRIPTION': 'cli description'},
                'compose': 'Fedora-cli'}))

    assert all(r.environment['DESCRIPTION'] == "cli description" for r in reqs)
    assert all(r.compose == "Fedora-cli" for r in reqs)


def test_reportportal_null_in_dimension():
    """reportportal: null in a dimension disables RP for those combinations."""
    config = RecipeConfig.from_yaml_file(
        Path('tests/unit/data/recipe_rp_null.yaml').absolute())
    reqs = list(config.build_requests(initial_config={}, cli_config={}))

    # 2 arches * 2 scenarios = 4 requests
    assert len(reqs) == 4

    full_reqs = [r for r in reqs if r.context.get('scenario') == 'full']
    smoke_reqs = [r for r in reqs if r.context.get('scenario') == 'smoke']

    assert len(full_reqs) == 2
    assert len(smoke_reqs) == 2

    # "full" scenario should have RP enabled (inherited from fixtures)
    for r in full_reqs:
        assert r.reportportal is not None
        assert r.reportportal['launch_name'] == 'test-launch'

    # "smoke" scenario should have RP disabled (null overrides fixtures)
    for r in smoke_reqs:
        assert r.reportportal is None


def test_reportportal_null_sticky():
    """Once reportportal is set to null, cli_config cannot override it back."""
    config = RecipeConfig.from_yaml_file(
        Path('tests/unit/data/recipe_rp_null.yaml').absolute())
    reqs = list(config.build_requests(
        initial_config={},
        cli_config={
            'reportportal': {'launch_name': 'cli-launch'},
            }))

    smoke_reqs = [r for r in reqs if r.context.get('scenario') == 'smoke']
    full_reqs = [r for r in reqs if r.context.get('scenario') == 'full']

    # Smoke: null is sticky — cli_config can't un-null it
    for r in smoke_reqs:
        assert r.reportportal is None

    # Full: cli_config merges normally, overriding launch_name
    for r in full_reqs:
        assert r.reportportal is not None
        assert r.reportportal['launch_name'] == 'cli-launch'


def test_merge_combination_data_null_override():
    """Direct test of merge_combination_data with null values."""
    config = RecipeConfig(fixtures={}, dimensions={})

    # null overrides an existing dict
    merged = config.merge_combination_data((
        {'reportportal': {'launch_name': 'test'}},
        {'reportportal': None},
        ))
    assert merged['reportportal'] is None

    # null is sticky — later dict cannot un-null
    merged = config.merge_combination_data((
        {'reportportal': {'launch_name': 'test'}},
        {'reportportal': None},
        {'reportportal': {'launch_name': 'override'}},
        ))
    assert merged['reportportal'] is None

    # null from the start stays null
    merged = config.merge_combination_data((
        {'reportportal': None},
        {'reportportal': {'launch_name': 'override'}},
        ))
    assert merged['reportportal'] is None


def test_merge_combination_data_preserves_existing_behavior():
    """Existing merge behavior is preserved after adding null handling."""
    config = RecipeConfig(fixtures={}, dimensions={})

    # dict merging still works
    merged = config.merge_combination_data((
        {'reportportal': {'launch_name': 'a'}},
        {'reportportal': {'launch_description': 'b'}},
        ))
    assert merged['reportportal'] == {'launch_name': 'a', 'launch_description': 'b'}

    # string override still works
    merged = config.merge_combination_data((
        {'compose': 'Fedora-1'},
        {'compose': 'Fedora-2'},
        ))
    assert merged['compose'] == 'Fedora-2'

    # list extend still works
    merged = config.merge_combination_data((
        {'environment': {'A': '1'}},
        {'environment': {'B': '2'}},
        ))
    assert merged['environment'] == {'A': '1', 'B': '2'}

    # when merging still works
    merged = config.merge_combination_data((
        {'when': 'cond1'},
        {'when': 'cond2'},
        ))
    assert 'cond1' in merged['when']
    assert 'cond2' in merged['when']
    assert 'and' in merged['when']
