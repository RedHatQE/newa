import pytest

from newa import RecipeConfig, render_template


@pytest.fixture
def simple_template():
    return "simple template which replaces {{ TESTVAR }}"


def test_render_template_good(simple_template):
    rendered = render_template(simple_template, TESTVAR='something')
    print(rendered)
    assert 'something' in rendered


def test_render_template_bad():
    # Render template with (intentional) typo and ensures it raises
    with pytest.raises(Exception, match='Could not parse template'):
        render_template(
            "{% if %} {% ednif %}",
            )


def test_render_template_recursive():
    """Test that render_template performs recursive rendering (late rendering / method 1)."""
    # This tests that nested template variables are expanded through multiple iterations
    # Template contains {{ VAR1 }} which expands to "{{ VAR2 }}" which then
    # expands to "final_value"
    template = "Result: {{ VAR1 }}"

    # First iteration: {{ VAR1 }} -> "{{ VAR2 }}"
    # Second iteration: {{ VAR2 }} -> "final_value"
    # Third iteration: no changes, converges
    rendered = render_template(
        template,
        VAR1="{{ VAR2 }}",
        VAR2="final_value",
        )

    assert rendered == "Result: final_value"
    # Verify it went through multiple iterations (not just one)
    # If it only did one iteration, result would be "Result: {{ VAR2 }}"
    assert "{{" not in rendered
    assert "}}" not in rendered


def test_render_template_single_iteration():
    """Test render_template with iterations=1 (early rendering/methods 2-4)."""
    # This tests that early rendering (methods 2-4) only does one iteration
    template = "Result: {{ VAR1 }}"

    # With iterations=1, should only expand VAR1 to "{{ VAR2 }}" and stop
    rendered = render_template(
        template,
        iterations=1,
        VAR1="{{ VAR2 }}",
        VAR2="final_value",
        )

    # Should still contain template syntax because it only did one iteration
    assert rendered == "Result: {{ VAR2 }}"
    assert "{{" in rendered
    assert "}}" in rendered


def test_dimension_template_single_iteration_rendering():
    """Test that dimension templates (methods 2-4) are rendered only once, not recursively.

    This test verifies that early rendering (methods 2-4) for dimensions, fixtures, and
    adjustments only performs ONE iteration of Jinja2 template expansion.

    The test works by having TEMPLATE_VAR contain the string '{{ NESTED_VAR }}' (with quotes).

    - With single iteration (CORRECT for methods 2-4):
      First pass: {{ TEMPLATE_VAR }} -> "{{ NESTED_VAR }}"
      Stops here. Result: the literal string "{{ NESTED_VAR }}"

    - With recursive rendering (INCORRECT for methods 2-4, but correct for method 1):
      First pass: {{ TEMPLATE_VAR }} -> "{{ NESTED_VAR }}"
      Second pass: {{ NESTED_VAR }} -> "final_value"
      Result: "final_value"
    """
    yaml_content = """
fixtures:
    environment:
        NESTED_VAR: final_value
dimensions:
    test: |
       - environment:
             # TEMPLATE_VAR will expand to the string "{{ NESTED_VAR }}" in first iteration
             # With single iteration: stops here, VALUE = "{{ NESTED_VAR }}" (literal)
             # With recursive: would do second iteration, VALUE = "final_value"
             VALUE: {{ TEMPLATE_VAR }}
"""
    # TEMPLATE_VAR contains template syntax as a string (with quotes for YAML)
    config = RecipeConfig.from_yaml(
        yaml_content,
        jinja_vars={'TEMPLATE_VAR': '"{{ NESTED_VAR }}"', 'NESTED_VAR': 'final_value'},
        )
    reqs = list(config.build_requests({}, {}))

    assert len(reqs) == 1

    # CRITICAL TEST: With single iteration (correct for methods 2-4),
    # VALUE should still contain the template syntax "{{ NESTED_VAR }}"
    # because it only did one rendering pass
    assert reqs[0].environment['VALUE'] == '{{ NESTED_VAR }}'

    # Verify it did NOT recurse - if it did, it would have expanded to "final_value"
    assert reqs[0].environment['VALUE'] != 'final_value'


def test_dimension_string_template():
    """Test that dimension values can be Jinja2 template strings."""
    yaml_content = """
fixtures:
    context:
        tier: 1
dimensions:
    arch:
       - context:
             arch: x86_64
       - |
         context:
             arch: aarch64
"""
    config = RecipeConfig.from_yaml(yaml_content)
    reqs = list(config.build_requests({}, {}))

    # Should have 2 requests (one for each arch dimension)
    assert len(reqs) == 2
    assert reqs[0].context['arch'] == 'x86_64'
    assert reqs[1].context['arch'] == 'aarch64'


def test_dimension_string_template_must_render_to_mapping():
    """Dimension item templates that render to a non-dict should raise ValueError."""
    # The second dimension item is a Jinja2 template that renders to a scalar ("1"),
    # which should trigger the error path in `_process_dimension_value`.
    yaml_content = """
fixtures:
    context:
        tier: 1
dimensions:
    arch:
       - context:
             arch: x86_64
       - "{{ tier }}"
"""
    with pytest.raises(ValueError, match=r"Jinja2 template must render to a YAML dict"):
        RecipeConfig.from_yaml(yaml_content)


def test_dimension_template_with_variables():
    """Test that Jinja2 templates can use variables."""
    yaml_content = """
fixtures:
    context:
        tier: 1
dimensions:
    arch:
       - |
         context:
             arch: {{ target_arch }}
"""
    config = RecipeConfig.from_yaml(yaml_content, jinja_vars={'target_arch': 's390x'})
    reqs = list(config.build_requests({}, {}))

    assert len(reqs) == 1
    assert reqs[0].context['arch'] == 's390x'


def test_adjustments_string_template():
    """Test that adjustment values can be Jinja2 template strings."""
    yaml_content = """
fixtures:
    context:
        tier: 1
adjustments:
  - context:
        distro: fedora
  - |
    environment:
        TEST_VAR: "test_value"
dimensions:
    arch:
       - context:
             arch: x86_64
"""
    config = RecipeConfig.from_yaml(yaml_content)
    reqs = list(config.build_requests({}, {}))

    assert len(reqs) == 1
    assert reqs[0].context['distro'] == 'fedora'
    assert reqs[0].environment['TEST_VAR'] == 'test_value'


def test_fixtures_string_template():
    """Test that fixtures can be a Jinja2 template string."""
    yaml_content = """
fixtures: |
    context:
        tier: 1
        env: {{ env_name }}
dimensions:
    arch:
       - context:
             arch: x86_64
"""
    config = RecipeConfig.from_yaml(yaml_content, jinja_vars={'env_name': 'production'})
    reqs = list(config.build_requests({}, {}))

    assert len(reqs) == 1
    assert reqs[0].context['tier'] == 1
    assert reqs[0].context['env'] == 'production'


def test_mixed_dict_and_string_dimensions():
    """Test that dimensions can mix dict and string template formats."""
    yaml_content = """
fixtures:
    context:
        tier: 1
dimensions:
    arch:
       - context:
             arch: x86_64
       - |
         context:
             arch: {{ arch_name }}
    fips:
       - |
         context:
             fips: yes
       - context:
             fips: no
"""
    config = RecipeConfig.from_yaml(yaml_content, jinja_vars={'arch_name': 'aarch64'})
    reqs = list(config.build_requests({}, {}))

    # Should have 4 requests (2 arch x 2 fips)
    assert len(reqs) == 4

    # Check that we have both archs
    archs = {req.context['arch'] for req in reqs}
    assert archs == {'x86_64', 'aarch64'}

    # Check that we have both fips values
    fips_values = {req.context['fips'] for req in reqs}
    assert fips_values == {'yes', 'no'}


def test_dimension_list_as_template():
    """Test that an entire dimension list can be a Jinja2 template string."""
    yaml_content = """
fixtures:
    context:
        tier: 1
dimensions:
    arch: |
       - context:
             arch: x86_64
       - context:
             arch: aarch64
"""
    config = RecipeConfig.from_yaml(yaml_content)
    reqs = list(config.build_requests({}, {}))

    # Should have 2 requests (one for each arch)
    assert len(reqs) == 2
    archs = {req.context['arch'] for req in reqs}
    assert archs == {'x86_64', 'aarch64'}


def test_dimension_list_template_with_variables():
    """Test that dimension list templates can use Jinja2 variables."""
    yaml_content = """
fixtures:
    context:
        tier: 1
dimensions:
    arch: |
       {% for arch in archs %}
       - context:
             arch: {{ arch }}
       {% endfor %}
"""
    config = RecipeConfig.from_yaml(
        yaml_content,
        jinja_vars={'archs': ['x86_64', 's390x', 'ppc64le']},
        )
    reqs = list(config.build_requests({}, {}))

    # Should have 3 requests (one for each arch in the list)
    assert len(reqs) == 3
    archs = {req.context['arch'] for req in reqs}
    assert archs == {'x86_64', 's390x', 'ppc64le'}


def test_mixed_dimension_list_formats():
    """Test that different dimensions can use different formats."""
    yaml_content = """
fixtures:
    context:
        tier: 1
dimensions:
    arch: |
       - context:
             arch: x86_64
       - context:
             arch: aarch64
    fips:
       - context:
             fips: yes
       - context:
             fips: no
"""
    config = RecipeConfig.from_yaml(yaml_content)
    reqs = list(config.build_requests({}, {}))

    # Should have 4 requests (2 arch x 2 fips)
    assert len(reqs) == 4

    # Check that we have both archs
    archs = {req.context['arch'] for req in reqs}
    assert archs == {'x86_64', 'aarch64'}

    # Check that we have both fips values
    fips_values = {req.context['fips'] for req in reqs}
    assert fips_values == {'yes', 'no'}


def test_dimension_list_template_with_nested_strings():
    """Test that dimension list template can contain individual item templates."""
    yaml_content = """
fixtures:
    context:
        tier: 1
dimensions:
    arch: |
       - context:
             arch: x86_64
       - |
         context:
             arch: {{ target_arch }}
"""
    config = RecipeConfig.from_yaml(
        yaml_content,
        jinja_vars={'target_arch': 'aarch64'},
        )
    reqs = list(config.build_requests({}, {}))

    # Should have 2 requests
    assert len(reqs) == 2
    archs = {req.context['arch'] for req in reqs}
    assert archs == {'x86_64', 'aarch64'}


def test_dimension_template_with_environment_from_fixtures():
    """Test that ENVIRONMENT defined in fixtures is available in dimension templates."""
    yaml_content = """
fixtures:
    context:
        tier: 1
    environment:
        ARCHITECTURES: "x86_64,s390x,ppc64le,aarch64"

dimensions:
    arch: |
       {% for arch in ENVIRONMENT.ARCHITECTURES.split(',') %}
       - context:
             arch: {{ arch }}
         environment:
             ARCH_NAME: {{ arch }}
       {% endfor %}
"""
    config = RecipeConfig.from_yaml(yaml_content)
    reqs = list(config.build_requests({}, {}))

    # Should have 4 requests (one for each arch)
    assert len(reqs) == 4

    # Check that we have all 4 archs
    archs = {req.context['arch'] for req in reqs}
    assert archs == {'x86_64', 's390x', 'ppc64le', 'aarch64'}

    # Check that ARCH_NAME matches arch in context for all requests
    arch_name_matches = all(req.environment['ARCH_NAME'] == req.context['arch'] for req in reqs)
    assert arch_name_matches, "ARCH_NAME should match arch in context for all requests"


def test_cli_environment_overrides_fixtures_in_dimension_templates():
    """Test that CLI ENVIRONMENT overrides fixtures when rendering dimension templates."""
    yaml_content = """
fixtures:
    context:
        tier: 1
    environment:
        ARCHITECTURES: "x86_64,s390x,ppc64le,aarch64"

dimensions:
    arch: |
       {% for arch in ENVIRONMENT.ARCHITECTURES.split(',') %}
       - context:
             arch: {{ arch }}
         environment:
             ARCH_NAME: {{ arch }}
       {% endfor %}
"""
    # CLI environment should override fixtures
    cli_environment = {'ARCHITECTURES': 'i686,ppc64'}

    config = RecipeConfig.from_yaml(
        yaml_content,
        jinja_vars={'ENVIRONMENT': cli_environment},
        )
    reqs = list(config.build_requests({}, {}))

    # Should have 2 requests (from CLI archs, not the 4 from fixtures)
    assert len(reqs) == 2

    # Check that we have CLI archs, NOT fixture archs
    archs = {req.context['arch'] for req in reqs}
    assert archs == {'i686', 'ppc64'}

    # Verify fixture archs are NOT present
    assert 'x86_64' not in archs
    assert 's390x' not in archs
    assert 'ppc64le' not in archs
    assert 'aarch64' not in archs

    # Check that ARCH_NAME matches arch in context for all requests
    arch_name_matches = all(req.environment['ARCH_NAME'] == req.context['arch'] for req in reqs)
    assert arch_name_matches, "ARCH_NAME should match arch in context for all requests"


def test_jinja_templates_with_includes():
    """Test that Jinja2 templates work correctly with includes."""
    jinja_vars = {
        'default_tier': 1,
        'child1_value': 'from_child1',
        'adjustment_value': 'test_adj',
        'adjustment_list': ['val1', 'val2', 'val3'],
        }

    config = RecipeConfig.from_yaml_with_includes(
        'tests/unit/data/recipe-jinja-include-parent.yaml',
        jinja_vars=jinja_vars,
        )

    # Check that fixtures were merged correctly
    assert config.fixtures['context']['tier'] == 1  # from child1 via jinja
    assert config.fixtures['context']['from'] == 'parent'  # parent overrides children
    # from child1, not overridden
    assert config.fixtures['context']['child1_only'] == 'from_child1'
    assert config.fixtures['environment']['CHILD1_VAR'] == 'child1_value'

    # Check that adjustments were processed
    assert len(config.adjustments) == 5  # 1 from child1 + 3 from child2 + 1 from child3

    # Verify child1 adjustment (Jinja template)
    assert config.adjustments[0]['context']['adjustment'] == 'test_adj'
    assert config.adjustments[0]['when'] == 'CONTEXT.tier == 1'

    # Verify child2 adjustments (Jinja template list)
    assert config.adjustments[1]['context']['adjustment1'] == 'val1'
    assert config.adjustments[1]['when'] == 'True'
    assert config.adjustments[2]['context']['adjustment2'] == 'val2'
    assert config.adjustments[2]['when'] == 'True'
    assert config.adjustments[3]['context']['adjustment3'] == 'val3'
    assert config.adjustments[3]['when'] == 'True'

    # Verify child3 adjustment (uses fixture environment from same file)
    assert config.adjustments[4]['context']['adj_from_fixture'] == 'x86_64'
    assert config.adjustments[4]['when'] == 'True'


def test_environment_and_context_priority_across_sources():
    """Test priority of ENVIRONMENT and CONTEXT values from CLI, fixtures, and dimensions."""
    yaml_content = """
fixtures:
    context:
        from_fixtures_only: fixture_ctx_value
        overridden_by_dimension: fixture_ctx_value
    environment:
        FROM_FIXTURES_ONLY: fixture_env_value
        OVERRIDDEN_BY_DIMENSION: fixture_env_value

dimensions:
    test:
       - context:
             from_dimension_only: dimension_ctx_value
             overridden_by_dimension: dimension_ctx_value
         environment:
             FROM_DIMENSION_ONLY: dimension_env_value
             OVERRIDDEN_BY_DIMENSION: dimension_env_value
             OVERRIDDEN_BY_CLI: dimension_env_value
"""
    # CLI values for template rendering (used in from_yaml)
    cli_jinja_vars = {
        'ENVIRONMENT': {
            'FROM_CLI_ONLY': 'cli_env_value',
            'OVERRIDDEN_BY_CLI': 'cli_env_value',
            },
        'CONTEXT': {
            'from_cli_only': 'cli_ctx_value',
            },
        }

    # CLI values for request building (RawRecipeConfigDimension format)
    cli_config = {
        'environment': {
            'FROM_CLI_ONLY': 'cli_env_value',
            'OVERRIDDEN_BY_CLI': 'cli_env_value',
            },
        'context': {
            'from_cli_only': 'cli_ctx_value',
            },
        }

    config = RecipeConfig.from_yaml(yaml_content, jinja_vars=cli_jinja_vars)
    reqs = list(config.build_requests({}, cli_config))

    # Should have 1 request
    assert len(reqs) == 1
    req = reqs[0]

    # ENVIRONMENT checks:
    # 1. CLI only - should be present
    assert req.environment['FROM_CLI_ONLY'] == 'cli_env_value'

    # 2. Fixtures only - should be present
    assert req.environment['FROM_FIXTURES_ONLY'] == 'fixture_env_value'

    # 3. Dimension only - should be present
    assert req.environment['FROM_DIMENSION_ONLY'] == 'dimension_env_value'

    # 4. CLI + Dimension - CLI takes priority
    assert req.environment['OVERRIDDEN_BY_CLI'] == 'cli_env_value'

    # 5. Fixtures + Dimension - Dimension takes priority
    assert req.environment['OVERRIDDEN_BY_DIMENSION'] == 'dimension_env_value'

    # CONTEXT checks:
    # 1. CLI only - should be present
    assert req.context['from_cli_only'] == 'cli_ctx_value'

    # 2. Fixtures only - should be present
    assert req.context['from_fixtures_only'] == 'fixture_ctx_value'

    # 3. Dimension only - should be present
    assert req.context['from_dimension_only'] == 'dimension_ctx_value'

    # 4. Fixtures + Dimension - Dimension takes priority
    assert req.context['overridden_by_dimension'] == 'dimension_ctx_value'
