"""Jinja2 template rendering utilities."""

import re
from typing import TYPE_CHECKING, Any, Optional, Union

import jinja2

if TYPE_CHECKING:
    from newa.models.events import Event
    from newa.models.jobs import ArtifactJob


def default_template_environment() -> jinja2.Environment:
    """
    Create a Jinja2 environment with default settings.

    Adds common filters, and enables block trimming and left strip.
    """

    environment = jinja2.Environment()

    environment.trim_blocks = True
    environment.lstrip_blocks = True

    return environment


def render_template(
        template: str,
        environment: Optional[jinja2.Environment] = None,
        **variables: Any,
        ) -> str:
    """
    Render a template recursively.

    :param template: template to render.
    :param environment: Jinja2 environment to use.
    :param variables: variables to pass to the template.
    """

    limit = 50
    environment = environment or default_template_environment()
    old = template
    try:
        for _ in range(limit):
            new = environment.from_string(old).render(**variables).strip()
            if old == new:
                return new
            old = new

    except jinja2.exceptions.TemplateSyntaxError as exc:
        raise Exception(
            f"Could not parse template at line {exc.lineno}.") from exc

    except jinja2.exceptions.TemplateError as exc:
        raise Exception("Could not render template.") from exc

    raise Exception(f"Jinja2 template recursion limit {limit} reached for template: '{template}'")


def eval_test(
        test: str,
        environment: Optional[jinja2.Environment] = None,
        **variables: Any,
        ) -> bool:
    """
    Evaluate a test expression.

    :param test: expression to evaluate. It must be a Jinja2-compatible expression.
    :param environment: Jinja2 environment to use.
    :param variables: variables to pass to the template.
    :returns: whether the expression evaluated to true-ish value.
    """
    # Import here to avoid circular dependency
    from newa.models.events import Event, EventType
    from newa.models.jobs import ArtifactJob

    environment = environment or default_template_environment()

    def _test_compose(obj: Union['Event', 'ArtifactJob']) -> bool:
        if isinstance(obj, Event):
            return obj.type_ is EventType.COMPOSE

        if isinstance(obj, ArtifactJob):
            return obj.event.type_ is EventType.COMPOSE

        raise Exception(f"Unsupported type in 'compose' test: {type(obj).__name__}")

    def _test_erratum(obj: Union['Event', 'ArtifactJob']) -> bool:
        if isinstance(obj, Event):
            return obj.type_ is EventType.ERRATUM

        if isinstance(obj, ArtifactJob):
            return obj.event.type_ is EventType.ERRATUM

        raise Exception(f"Unsupported type in 'erratum' test: {type(obj).__name__}")

    def _test_rog(obj: Union['Event', 'ArtifactJob']) -> bool:
        if isinstance(obj, Event):
            return obj.type_ is EventType.ROG

        if isinstance(obj, ArtifactJob):
            return obj.event.type_ is EventType.ROG

        raise Exception(f"Unsupported type in 'rog-mr' test: {type(obj).__name__}")

    def _test_match(s: str, pattern: str) -> bool:
        return re.match(pattern, s) is not None

    environment.tests['compose'] = _test_compose
    environment.tests['erratum'] = _test_erratum
    environment.tests['RoG'] = _test_rog
    environment.tests['match'] = _test_match

    try:
        outcome = render_template(
            f'{{% if {test} %}}true{{% else %}}false{{% endif %}}',
            environment=environment,
            **variables,
            )

    except Exception as exc:
        raise Exception(f"Could not evaluate test '{test}'") from exc

    return bool(outcome == 'true')
