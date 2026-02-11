"""YAML parsing utilities."""

from enum import Enum

import ruamel.yaml


def yaml_parser() -> ruamel.yaml.YAML:
    """Create standardized YAML parser."""
    # Import here to avoid circular dependency
    from newa.models.artifacts import ErratumContentType
    from newa.models.base import (
        Arch,
        ErratumCommentTrigger,
        ExecuteHow,
        RequestResult,
        RoGCommentTrigger,
        )
    from newa.models.events import EventType

    yaml = ruamel.yaml.YAML(typ='safe')

    yaml.indent(mapping=4, sequence=4, offset=2)
    yaml.default_flow_style = False
    yaml.allow_unicode = True
    yaml.encoding = 'utf-8'

    # For simpler dumping of well-known classes
    def _represent_enum(
            representer: ruamel.yaml.representer.Representer,
            data: Enum) -> ruamel.yaml.nodes.ScalarNode:
        return representer.represent_scalar('tag:yaml.org,2002:str', data.value)

    yaml.representer.add_representer(EventType, _represent_enum)
    yaml.representer.add_representer(ErratumContentType, _represent_enum)
    yaml.representer.add_representer(ErratumCommentTrigger, _represent_enum)
    yaml.representer.add_representer(RoGCommentTrigger, _represent_enum)
    yaml.representer.add_representer(Arch, _represent_enum)
    yaml.representer.add_representer(ExecuteHow, _represent_enum)
    yaml.representer.add_representer(RequestResult, _represent_enum)

    return yaml
