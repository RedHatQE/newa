"""Issue and Jira configuration models."""

import copy
from collections.abc import Generator
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional, Union

import ruamel.yaml

try:
    from attrs import define, field
except ModuleNotFoundError:
    from attr import define, field

from newa.models.base import ErratumCommentTrigger, Serializable
from newa.models.recipes import RecipeContext, RecipeEnvironment
from newa.utils.http import ResponseContentType, get_request
from newa.utils.yaml_utils import yaml_parser

if TYPE_CHECKING:
    from typing_extensions import Self


class IssueType(Enum):
    EPIC = 'epic'
    TASK = 'task'
    SUBTASK = 'subtask'
    STORY = 'story'


class OnRespinAction(Enum):
    # TODO: what's the default? It would simplify the class a bit.
    KEEP = 'keep'
    CLOSE = 'close'


def _default_action_id_generator() -> Generator[str, int, None]:
    n = 1
    while True:
        yield f'DEFAULT_ACTION_ID_{n}'
        n += 1


default_action_id = _default_action_id_generator()


@define
class Issue(Serializable):  # type: ignore[no-untyped-def]
    """Issue - a key in Jira (eg. NEWA-123)."""

    id: str = field()
    erratum_comment_triggers: list[ErratumCommentTrigger] = field(  # type: ignore[var-annotated]
        factory=list, converter=lambda triggers: [
            ErratumCommentTrigger(trigger) for trigger in triggers])
    # this is used to store comment visibility restriction
    # usually JiraHandler.group takes priority but this value
    # will be used when JiraHandler is not available
    group: Optional[str] = None
    summary: Optional[str] = None
    closed: Optional[bool] = None
    url: Optional[str] = None
    transition_processed: Optional[str] = None
    transition_passed: Optional[str] = None
    action_id: Optional[str] = None

    def __str__(self) -> str:
        return self.id


@define
class IssueAction(Serializable):  # type: ignore[no-untyped-def]
    type: IssueType = field(converter=IssueType, default=IssueType.TASK)
    on_respin: OnRespinAction = field(  # type: ignore[var-annotated]
        converter=lambda value: OnRespinAction(value), default=OnRespinAction.CLOSE)
    erratum_comment_triggers: list[ErratumCommentTrigger] = field(  # type: ignore[var-annotated]
        factory=list, converter=lambda triggers: [
            ErratumCommentTrigger(trigger) for trigger in triggers])
    auto_transition: Optional[bool] = False
    summary: Optional[str] = None
    description: Optional[str] = None
    id: Optional[str] = field(  # type: ignore[var-annotated]
        converter=lambda s: s if s else next(default_action_id),
        default=None)
    assignee: Optional[str] = None
    parent_id: Optional[str] = None
    job_recipe: Optional[str] = None
    when: Optional[str] = None
    newa_id: Optional[str] = None
    fields: Optional[dict[str, Union[str, float, list[str]]]] = None
    iterate: Optional[list[RecipeEnvironment]] = None
    context: Optional[RecipeContext] = None
    environment: Optional[RecipeEnvironment] = None
    links: Optional[dict[str, list[str]]] = None

    # function to handle issue-config file defaults

    def update_with_defaults(
            self,
            defaults: Optional['IssueAction'] = None) -> None:
        if not isinstance(defaults, IssueAction):
            return
        for attr_name in dir(defaults):
            attr = getattr(defaults, attr_name)
            if attr and (not attr_name.startswith('_') or callable(attr)):
                if attr_name == 'fields' and defaults.fields:
                    if self.fields:
                        self.fields = copy.deepcopy({**defaults.fields, **self.fields})
                    else:
                        setattr(self, attr_name, copy.deepcopy(defaults.fields))
                elif attr_name == 'links' and defaults.links:
                    if not self.links:
                        self.links = copy.deepcopy(defaults.links)
                    else:
                        for relation in defaults.links:
                            # if I have such a relation defined, extend the list
                            if relation in self.links:
                                self.links[relation].extend(defaults.links[relation])
                            elif defaults.links[relation]:
                                self.links[relation] = copy.deepcopy(defaults.links[relation])
                elif not getattr(self, attr_name, None):
                    setattr(self, attr_name, copy.deepcopy(attr))
        return


@define
class IssueTransitions(Serializable):
    closed: list[str] = field()
    dropped: list[str] = field()
    processed: Optional[list[str]] = None
    passed: Optional[list[str]] = None


@define
class IssueConfig(Serializable):  # type: ignore[no-untyped-def]
    project: str = field()
    transitions: IssueTransitions = field(  # type: ignore[var-annotated]
        converter=lambda x: x if isinstance(x, IssueTransitions) else IssueTransitions(**x))
    defaults: Optional[IssueAction] = field(  # type: ignore[var-annotated]
        converter=lambda action: IssueAction(**action) if action else None, default=None)
    issues: list[IssueAction] = field(  # type: ignore[var-annotated]
        factory=list, converter=lambda issues: [
            IssueAction(**issue) for issue in issues])
    group: Optional[str] = field(default=None)
    board: Optional[Union[str, int]] = field(default=None)

    @classmethod
    def from_yaml_with_include(cls: type['Self'], location: str) -> 'Self':

        def load_data_from_location(location: str,
                                    stack: Optional[list[str]] = None) -> dict[str, Any]:
            if stack and location in stack:
                raise Exception(
                    'Duplicate location encountered while loading issue-config YAML '
                    f'from "{location}"')
            # include location into the stack so we can detect recursion
            if stack:
                stack.append(location)
            else:
                stack = [location]
            data: dict[str, Any] = {}
            if location.startswith(('http://', 'https://')):
                data = yaml_parser().load(get_request(
                    url=location,
                    response_content=ResponseContentType.TEXT))
            else:
                try:
                    data = yaml_parser().load(Path(location).read_text())
                except ruamel.yaml.error.YAMLError as e:
                    raise Exception(
                        f'Unable to load and parse YAML file from location {location}') from e

            # process 'include' attribute
            if 'include' in data:
                locations = data['include']
                # drop 'include' so it won't be processed again
                del data['include']
                # if 'include' list is empty, return data
                if not locations:
                    return data
                # processing files in reversed order so that later definition takes priority
                for loc in reversed(locations):
                    included_data = load_data_from_location(loc, stack)
                    if included_data:
                        for key in included_data:
                            # special handing of 'issues'
                            # explicitly join 'issues' lists
                            if key == 'issues':
                                if key in data:
                                    data[key].extend(included_data[key])
                                else:
                                    data[key] = copy.deepcopy(included_data[key])
                            # special handing of 'defaults'
                            elif key == 'defaults':
                                if key not in data:
                                    data[key] = copy.deepcopy(included_data[key])
                                else:
                                    for (k, v) in included_data[key].items():
                                        if k not in data[key]:
                                            data[key][k] = copy.deepcopy(v)
                                        else:
                                            # 'fields' we extend, original values having priority
                                            if k == 'fields':
                                                # entend fields configuration
                                                data[key][k] = copy.deepcopy(
                                                    {**included_data[key][k], **data[key][k]})
                                            # other defined keys are not modified
                            else:
                                if key not in data:
                                    data[key] = copy.deepcopy(included_data[key])

            return data

        data = load_data_from_location(location)
        return cls(**data)

    @classmethod
    def read_file(cls: type['Self'], location: str) -> 'Self':

        config = cls.from_yaml_with_include(location)

        for action in config.issues:
            if config.defaults:
                # update action object with default attributes when not present
                action.update_with_defaults(config.defaults)
        return config
