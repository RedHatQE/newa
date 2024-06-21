from pathlib import Path

from newa import RecipeConfig


def test_recipeconfig_ok():
    config = RecipeConfig.from_yaml_file(Path('tests/unit/data/sample_recipe.yaml').absolute())
    reqs = list(config.build_requests({}))

    # Check generated requests are correct
    assert len(reqs) == 4
    assert all('arch' in r.context for r in reqs)
    assert all('fips' in r.context for r in reqs)
    assert all('FIPS' in r.environment for r in reqs)
    assert all(r.testingfarm['cli_args'] == "-c trigger=newa" for r in reqs)
    # Assert recipe id uniqueness
    assert len(reqs) == len({r.id for r in reqs})
