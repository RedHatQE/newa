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
