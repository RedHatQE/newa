"""Parsers for package names and versions."""

try:
    from attrs import define, field
except ModuleNotFoundError:
    from attr import define, field


@define
class NVRParser:
    nvr: str
    name: str = field(init=False)
    version: str = field(init=False)
    release: str = field(init=False)

    def __attrs_post_init__(self) -> None:
        self.name, self.version, self.release = self.nvr.rsplit("-", 2)


@define
class NSVCParser:
    nsvc: str
    name: str = field(init=False)
    stream: str = field(init=False)
    version: str = field(init=False)
    context: str = field(init=False)

    def __attrs_post_init__(self) -> None:
        self.name, self.stream, partial = self.nsvc.rsplit("-", 2)
        self.version, self.context = partial.split('.', 1)

    def __str__(self) -> str:
        return f'{self.name}:{self.stream}:{self.version}:{self.context}'
