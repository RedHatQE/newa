"""Base classes and enums for newa models."""

import hashlib
import io
import re
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Optional

try:
    from attrs import asdict, define, evolve
except ModuleNotFoundError:
    from attr import asdict, define, evolve

if TYPE_CHECKING:
    from typing_extensions import Self

from newa.utils.http import ResponseContentType, get_request
from newa.utils.yaml_utils import yaml_parser

TF_REQUEST_FINISHED_STATES = {'complete', 'error', 'canceled', 'skipped'}


class Arch(Enum):
    """Available system architectures."""

    I386 = 'i386'
    I686 = 'i686'
    X86_64 = 'x86_64'
    AARCH64 = 'aarch64'
    S390 = 's390'
    S390X = 's390x'
    PPC64LE = 'ppc64le'
    PPC64 = 'ppc64'
    NOARCH = 'noarch'
    MULTI = 'multi'
    SRPMS = 'SRPMS'  # just to ease errata processing

    @classmethod
    def architectures(cls: type['Arch'],
                      preset: Optional[list['Arch']] = None,
                      compose: Optional[str] = None) -> list['Arch']:

        _valid = [Arch.X86_64, Arch.AARCH64, Arch.S390X, Arch.PPC64LE, Arch.PPC64]
        _default = [Arch.X86_64, Arch.S390X, Arch.PPC64LE, Arch.AARCH64]

        if compose and re.match(r'^rhel-6', compose, flags=re.IGNORECASE):
            _valid = [*_valid, Arch.I386]
            _default = [Arch.X86_64, Arch.S390X, Arch.I386]
        elif compose and re.match(r'^rhel-7', compose, flags=re.IGNORECASE):
            _default = [Arch.X86_64, Arch.S390X, Arch.PPC64LE, Arch.PPC64]

        if not preset:
            return _default
        # 'noarch' should be tested on all architectures
        if Arch.NOARCH in preset:
            return _default
        # 'multi' is given for container advisories
        if Arch.MULTI in preset:
            return _default
        return [a for a in preset if a in _valid]


class ErratumCommentTrigger(Enum):
    JIRA = 'jira'
    EXECUTE = 'execute'
    REPORT = 'report'


class RoGCommentTrigger(Enum):
    JIRA = 'jira'
    EXECUTE = 'execute'
    REPORT = 'report'


class ExecuteHow(Enum):
    """Available system architectures"""

    TESTING_FARM = 'testingfarm'
    TMT = 'tmt'


class RequestResult(Enum):
    PASSED = 'passed'
    FAILED = 'failed'
    ERROR = 'error'
    SKIPPED = 'skipped'
    NONE = 'None'

    @classmethod
    def values(cls: type['RequestResult']) -> list[str]:
        return [str(v) for v in RequestResult.__members__.values()]


@define
class Cloneable:
    """A class whose instances can be cloned."""

    def clone(self) -> 'Self':
        return evolve(self)


@define
class Serializable:
    """A class whose instances can be serialized into YAML."""

    def get_hash(self, seed: str = '') -> str:
        # use only first 12 characters
        return hashlib.sha256(f'{seed}{self.to_yaml()}'.encode()).hexdigest()[:12]

    def to_yaml(self) -> str:
        output = io.StringIO()

        parser = yaml_parser()
        parser.width = 4096
        parser.dump(asdict(self, recurse=True), output)

        return output.getvalue()

    def to_yaml_file(self, filepath: Path) -> None:
        filepath.write_text(self.to_yaml())

    @classmethod
    def from_yaml(cls: type['Self'], serialized: str) -> 'Self':
        data = yaml_parser().load(serialized)

        return cls(**data)

    @classmethod
    def from_yaml_file(cls: type['Self'], filepath: Path) -> 'Self':
        return cls.from_yaml(filepath.read_text())

    @classmethod
    def from_yaml_url(cls: type['Self'], url: str) -> 'Self':
        r = get_request(url=url, response_content=ResponseContentType.TEXT)
        return cls.from_yaml(r)
