"""Tests for _render_request_attributes template variable resolution order."""

from typing import ClassVar

from newa.cli.schedule_helpers import _render_request_attributes
from newa.models.execution import Request


class _FakeROG:
    """Minimal stand-in for a RoG object exposing the attributes templates use."""

    id = 'https://gitlab.com/example/mr/1'
    builds: ClassVar[list[str]] = [
        'mod_md-2.4.26-6.el10,draft_4035211', 'other-1.0-1.el10,draft_9']


class _FakeCompose:
    id = 'RHEL-10.3-Nightly'


def _jinja_vars(request: Request) -> dict:
    """Build jinja_vars the way _prepare_jinja_vars_for_request does.

    Crucially, ENVIRONMENT/CONTEXT alias request.environment/request.context.
    """
    return {
        'EVENT': None,
        'ERRATUM': None,
        'COMPOSE': _FakeCompose(),
        'ROG': _FakeROG(),
        'CONTEXT': request.context,
        'ENVIRONMENT': request.environment,
        'ISSUE': {},
        'ARCH': 'x86_64',
        }


def test_environment_variable_template_is_rendered():
    """An ENVIRONMENT value that is itself a template is rendered."""
    request = Request(
        id='REQ-1',
        environment={'BUILDS': "{{ ROG.builds|join(' ') }}"},
        )
    _render_request_attributes(request, _jinja_vars(request))
    assert request.environment['BUILDS'] == \
        'mod_md-2.4.26-6.el10,draft_4035211 other-1.0-1.el10,draft_9'


def test_context_variable_template_is_rendered():
    """A CONTEXT value that is itself a template is rendered."""
    request = Request(
        id='REQ-1',
        context={'distro': '{{ COMPOSE.id }}', 'rog_mr': '{{ ROG.id }}'},
        )
    _render_request_attributes(request, _jinja_vars(request))
    assert request.context['distro'] == 'RHEL-10.3-Nightly'
    assert request.context['rog_mr'] == 'https://gitlab.com/example/mr/1'


def test_environment_when_key_is_left_unrendered():
    """A 'when' key in ENVIRONMENT is treated as metadata and left unrendered.

    In practice environment/context never carry a 'when' key (it is a separate
    top-level Request attribute), but the render skips it defensively; this locks
    that behavior in.
    """
    request = Request(
        id='REQ-1',
        environment={'when': '{{ ROG.id }}', 'BUILDS': "{{ ROG.builds|join(' ') }}"},
        )
    _render_request_attributes(request, _jinja_vars(request))
    # 'when' is left as-is, other keys are rendered
    assert request.environment['when'] == '{{ ROG.id }}'
    assert request.environment['BUILDS'] == \
        'mod_md-2.4.26-6.el10,draft_4035211 other-1.0-1.el10,draft_9'


def test_context_when_key_is_left_unrendered():
    """A 'when' key in CONTEXT is treated as metadata and left unrendered."""
    request = Request(
        id='REQ-1',
        context={'when': '{{ COMPOSE.id }}', 'distro': '{{ COMPOSE.id }}'},
        )
    _render_request_attributes(request, _jinja_vars(request))
    assert request.context['when'] == '{{ COMPOSE.id }}'
    assert request.context['distro'] == 'RHEL-10.3-Nightly'


def test_testingfarm_attribute_referencing_environment_variable_sees_resolved_value():
    """testingfarm cli_args referencing ENVIRONMENT.* via a filter sees resolved values.

    Mirrors test_attribute_referencing_environment_variable_sees_resolved_value for
    request.testingfarm, the other consumer of the resolved variables.
    """
    request = Request(
        id='REQ-1',
        environment={'BUILDS': "{{ ROG.builds|join(' ') }}"},
        testingfarm={
            'cli_args':
                "{% if ENVIRONMENT.BUILDS %}"
                "--redhat-brew-build "
                "{{ ENVIRONMENT.BUILDS.split() | join(' --redhat-brew-build ') }}"
                "{% endif %}",
            },
        )
    _render_request_attributes(request, _jinja_vars(request))
    assert request.testingfarm is not None
    assert request.testingfarm['cli_args'] == (
        "--redhat-brew-build mod_md-2.4.26-6.el10,draft_4035211 "
        "--redhat-brew-build other-1.0-1.el10,draft_9"
        )


def test_attribute_referencing_environment_variable_sees_resolved_value():
    """tmt cli_args referencing ENVIRONMENT.* via a filter sees resolved values.

    This is the regression covered by the fix: ENVIRONMENT.BUILDS is itself a
    template, and tmt.cli_args feeds it into .split(). If ENVIRONMENT is not
    rendered first, .split() operates on the raw template text and produces
    broken output (or a Jinja error on the next recursion pass).
    """
    request = Request(
        id='REQ-1',
        environment={'BUILDS': "{{ ROG.builds|join(' ') }}"},
        tmt={
            'cli_args':
                "--tmt-environment TMT_POLICY_NAME=rhel-ci "
                "{% if ENVIRONMENT.BUILDS %}"
                "--redhat-brew-build "
                "{{ ENVIRONMENT.BUILDS.split() | join(' --redhat-brew-build ') }}"
                "{% endif %}",
            },
        )
    _render_request_attributes(request, _jinja_vars(request))
    assert request.tmt is not None
    assert request.tmt['cli_args'] == (
        "--tmt-environment TMT_POLICY_NAME=rhel-ci "
        "--redhat-brew-build mod_md-2.4.26-6.el10,draft_4035211 "
        "--redhat-brew-build other-1.0-1.el10,draft_9"
        )


def test_attribute_referencing_empty_environment_variable():
    """The consuming template's guard works when the variable resolves empty."""
    request = Request(
        id='REQ-1',
        environment={'BUILDS': "{{ ROG.builds|join(' ') }}"},
        tmt={
            'cli_args':
                "--tmt-environment TMT_POLICY_NAME=rhel-ci"
                "{% if ENVIRONMENT.BUILDS %} --redhat-brew-build "
                "{{ ENVIRONMENT.BUILDS.split() | join(' --redhat-brew-build ') }}"
                "{% endif %}",
            },
        )
    # ROG with no builds -> BUILDS renders to an empty string
    jinja_vars = _jinja_vars(request)
    jinja_vars['ROG'].builds = []
    _render_request_attributes(request, jinja_vars)
    assert request.environment['BUILDS'] == ''
    assert request.tmt is not None
    assert request.tmt['cli_args'] == "--tmt-environment TMT_POLICY_NAME=rhel-ci"
