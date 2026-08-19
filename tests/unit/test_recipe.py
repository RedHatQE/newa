from pathlib import Path

from newa import Compose, RecipeConfig


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

    # null overrides an existing dict; other keys still merge normally
    merged = config.merge_combination_data((
        {
            'reportportal': {'launch_name': 'test'},
            'environment': {'name': 'dev'},
            },
        {
            'reportportal': None,
            'environment': {'name': 'prod'},
            },
        ))
    assert merged['reportportal'] is None
    assert merged['environment'] == {'name': 'prod'}

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


def test_reportportal_null_in_fixtures():
    """reportportal: null in fixtures disables RP for all combinations."""
    config = RecipeConfig.from_yaml_file(
        Path('tests/unit/data/recipe_rp_null_fixtures.yaml').absolute())
    reqs = list(config.build_requests(initial_config={}, cli_config={}))

    assert len(reqs) == 2
    for r in reqs:
        assert r.reportportal is None


def test_description_attribute():
    """description attribute is set on requests and merged correctly."""
    config = RecipeConfig.from_yaml_file(
        Path('tests/unit/data/recipe_description.yaml').absolute())
    reqs = list(config.build_requests(initial_config={}, cli_config={}))

    # 3 scenarios = 3 requests
    assert len(reqs) == 3

    full_req = next(r for r in reqs if r.context.get('scenario') == 'full')
    smoke_req = next(r for r in reqs if r.context.get('scenario') == 'smoke')
    minimal_req = next(r for r in reqs if r.context.get('scenario') == 'minimal')

    # description is set from dimensions
    assert full_req.description == 'full scenario'
    assert smoke_req.description == 'smoke scenario'
    # minimal has no description
    assert minimal_req.description is None

    # smoke has RP disabled, full and minimal have RP enabled
    assert smoke_req.reportportal is None
    assert full_req.reportportal is not None
    assert minimal_req.reportportal is not None

    # minimal has explicit suite_description in reportportal
    assert minimal_req.reportportal['suite_description'] == 'explicit suite desc'


def test_description_missing_backward_compat():
    """Requests from old YAMLs without description field work correctly."""
    config = RecipeConfig.from_yaml_file(
        Path('tests/unit/data/sample_recipe.yaml').absolute())
    reqs = list(config.build_requests(initial_config={}, cli_config={}))

    # old recipe has no description attribute — defaults to None
    for r in reqs:
        assert r.description is None


def test_description_merge_as_string():
    """description merges as a string — later value overrides earlier."""
    config = RecipeConfig(fixtures={}, dimensions={})

    merged = config.merge_combination_data((
        {'description': 'from fixtures'},
        {'description': 'from dimension'},
        ))
    assert merged['description'] == 'from dimension'

    # description alongside other keys
    merged = config.merge_combination_data((
        {'description': 'test', 'compose': 'Fedora-1'},
        {'compose': 'Fedora-2'},
        ))
    assert merged['description'] == 'test'
    assert merged['compose'] == 'Fedora-2'


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


def test_when_original_namespace_matches_untransformed_compose():
    """A 'when' guard can reference ORIGINAL.COMPOSE even when the combination
    transforms COMPOSE into a derived value."""
    config = RecipeConfig.from_yaml_file(
        Path('tests/unit/data/recipe_original_namespace.yaml').absolute())

    # RHEL compose: both the standard and the image-mode combinations apply
    reqs = list(config.build_requests(
        initial_config={},
        cli_config={},
        jinja_vars={'COMPOSE': Compose(id='RHEL-9.4.0-Nightly')}))
    modes = sorted(r.context['mode'] for r in reqs)
    assert modes == ['image', 'standard']


def test_when_original_namespace_filters_non_matching_compose():
    """The image-mode combination is dropped when ORIGINAL.COMPOSE does not match."""
    config = RecipeConfig.from_yaml_file(
        Path('tests/unit/data/recipe_original_namespace.yaml').absolute())

    # non-RHEL compose: only the standard combination applies
    reqs = list(config.build_requests(
        initial_config={},
        cli_config={},
        jinja_vars={'COMPOSE': Compose(id='CentOS-Stream-9-Nightly')}))
    modes = sorted(r.context['mode'] for r in reqs)
    assert modes == ['standard']
