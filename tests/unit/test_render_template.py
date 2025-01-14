import pytest

from newa import render_template


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
