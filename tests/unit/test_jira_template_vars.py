"""Jira issue fields must reach Jinja templates as plain data only.

Live ``jira`` library objects hold the authenticated session and expose public
methods (``update()``, ``delete()``, ``find()``) that the Jinja sandbox allows
templates to call. These tests make sure no template can trigger a request.
"""

import contextlib
from typing import Any

import pytest
import requests
from jira.resources import Issue

from newa.cli.jira_helpers import jira_fields_to_template_vars
from newa.utils.templates import render_template

_OPTIONS = {
    'server': 'https://jira.example.com',
    'rest_path': 'api',
    'rest_api_version': '2',
    'agile_rest_path': 'agile',
    'agile_rest_api_version': '1.0',
    'context_path': '/',
    'async': False,
    }


class _RecordingSession(requests.Session):
    """Session that records requests instead of sending them."""

    def __init__(self) -> None:
        super().__init__()
        self.headers['Authorization'] = 'Bearer SECRET-TOKEN'
        self.calls: list[tuple[str, str]] = []

    def request(  # type: ignore[override]
            self, method: str, url: str, *args: Any, **kwargs: Any) -> Any:
        self.calls.append((method, url))
        raise RuntimeError('network access attempted')


def _issue_fields(session: requests.Session, summary: str = 'Test summary') -> Any:
    raw = {
        'id': '1',
        'key': 'NEWA-1',
        'self': 'https://jira.example.com/rest/api/2/issue/1',
        'fields': {
            'summary': summary,
            'labels': ['a', 'b'],
            'assignee': {
                'self': 'https://jira.example.com/rest/api/2/user?username=jdoe',
                'name': 'jdoe',
                'displayName': 'J. Doe',
                },
            'components': [
                {'self': 'https://jira.example.com/rest/api/2/component/7', 'name': 'comp'}],
            'customfield_1': {'value': 'opt'},
            'priority': None,
            },
        }
    return Issue(_OPTIONS, session, raw=raw).fields


def test_fields_are_converted_to_plain_data() -> None:
    session = _RecordingSession()
    fields = jira_fields_to_template_vars(_issue_fields(session))

    assert fields['summary'] == 'Test summary'
    assert fields['labels'] == ['a', 'b']
    assert fields['assignee']['name'] == 'jdoe'
    assert fields['components'][0]['name'] == 'comp'
    assert fields['customfield_1'] == {'value': 'opt'}
    assert fields['priority'] is None

    def _only_plain(value: Any) -> None:
        if isinstance(value, dict):
            for v in value.values():
                _only_plain(v)
        elif isinstance(value, list):
            for v in value:
                _only_plain(v)
        else:
            assert value is None or isinstance(value, (str, int, float, bool))

    _only_plain(fields)


def test_attribute_access_still_works_in_templates() -> None:
    fields = jira_fields_to_template_vars(_issue_fields(_RecordingSession()))
    rendered = render_template(
        "{{ ISSUE.summary }}|{{ ISSUE.assignee.name }}|{{ ISSUE.components[0].name }}",
        ISSUE=fields)
    assert rendered == 'Test summary|jdoe|comp'


@pytest.mark.parametrize('template', [
    '{{ ISSUE.assignee.delete() }}',
    "{{ ISSUE.assignee.update(name='x') }}",
    "{{ ISSUE.assignee.find('1') }}",
    '{{ ISSUE.components[0].delete() }}',
    ])
def test_templates_cannot_issue_jira_requests(template: str) -> None:
    session = _RecordingSession()
    fields = jira_fields_to_template_vars(_issue_fields(session))
    with contextlib.suppress(Exception):
        render_template(template, ISSUE=fields)
    assert session.calls == []


def test_jira_content_is_not_evaluated_as_template() -> None:
    session = _RecordingSession()
    fields = jira_fields_to_template_vars(_issue_fields(
        session, summary="{{ ISSUE.assignee.update(name='pwn') }}"))
    rendered = render_template('{{ ISSUE.summary }}', ISSUE=fields)
    assert session.calls == []
    assert 'SECRET-TOKEN' not in rendered
    assert rendered == '" ISSUE.assignee.update(name=\'pwn\') "'


@pytest.mark.parametrize(('summary', 'expected'), [
    ('{% if true %}YES{% endif %}', '{ % if true % }YES{ % endif % }'),
    ('{% for i in range(3) %}x{% endfor %}', '{ % for i in range(3) % }x{ % endfor % }'),
    ('keep {# hidden #} text', 'keep { # hidden # } text'),
    ('{#}', '{ # }'),
    ('{{% raw %}}', '"% raw %"'),
    ('plain {code} text', 'plain {code} text'),
    ])
def test_jinja_statements_and_comments_are_neutralized(summary: str, expected: str) -> None:
    fields = jira_fields_to_template_vars(_issue_fields(_RecordingSession(), summary=summary))
    assert fields['summary'] == expected
    assert render_template('{{ ISSUE.summary }}', ISSUE=fields) == expected


def test_mapping_keys_are_sanitized() -> None:
    fields = jira_fields_to_template_vars({
        'customfield_1': {'{% if true %}k{% endif %}': '{{ v }}'},
        })
    assert fields == {'customfield_1': {'{ % if true % }k{ % endif % }': '" v "'}}
    rendered = render_template('{{ ISSUE.customfield_1 | tojson }}', ISSUE=fields)
    assert rendered == '{"{ % if true % }k{ % endif % }": "\\" v \\""}'
