"""Utility functions for NEWA CLI."""

import os
import re
from pathlib import Path
from typing import Optional

from newa import CLIContext
from newa.cli.constants import STATEDIR_NAME_PATTERN


def get_state_dir(topdir: Path, use_ppid: bool = False) -> Path:
    """
    Get state directory path.

    When not using ppid returns the first unused directory
    matching /var/tmp/newa/run-[0-9]+, starting with run-1
    When using ppid searches for the most recent state-dir directory
    containing file $PPID.ppid
    """
    counter = 0
    last_dir = None
    ppid_filename = f'{os.getppid()}.ppid'
    try:
        obj = os.scandir(topdir)
    except FileNotFoundError as e:
        if use_ppid:
            raise Exception(f'{topdir} does not exist') from e
        # return initial value run-1
        return topdir / f'run-{counter + 1}'
    dirs = sorted([d for d in obj if d.is_dir()],
                  key=lambda d: os.path.getmtime(d))
    for statedir in dirs:
        # when using ppid find the most recent (using getmtime) matching dir
        if use_ppid:
            ppid_file = Path(statedir.path) / ppid_filename
            if ppid_file.exists():
                last_dir = statedir
        # otherwise find the lowest unsused value for counter
        else:
            r = re.match(STATEDIR_NAME_PATTERN, statedir.name)
            if r:
                c = int(r.group(1))
                counter = max(c, counter)
    if use_ppid:
        if last_dir:
            return Path(last_dir.path)
        raise Exception(f'File {ppid_filename} not found under {topdir}')
    # otherwise return the first unused value
    return topdir / f'run-{counter + 1}'


def initialize_state_dir(ctx: CLIContext) -> None:
    """Initialize state directory."""
    if not ctx.state_dirpath.exists():
        ctx.new_state_dir = True
        ctx.logger.debug(f'State directory {ctx.state_dirpath} does not exist, creating...')
        ctx.state_dirpath.mkdir(parents=True)
    # create empty ppid file
    with open(os.path.join(ctx.state_dirpath, f'{os.getppid()}.ppid'), 'w'):
        pass


def test_file_presence(statedir: Path, prefix: str) -> bool:
    """Check if files with given prefix exist in state directory."""
    return any(child.name.startswith(prefix) for child in statedir.iterdir())


def apply_release_mapping(string: str,
                          mapping: Optional[list[str]] = None,
                          regexp: bool = True,
                          logger: Optional[object] = None) -> str:
    """Apply release mapping transformations to a string."""
    # define default mapping
    if not mapping:
        mapping = [
            r'\.GA$=',
            r'\.Z\.?(MAIN)?(\+)?(AUS|E.S|TUS)?(\.EXTENSION)?$=',
            r'^rhel-=RHEL-',
            r'RHEL-10\.0\.BETA=RHEL-10-Beta',
            r'-candidate$=',
            r'-draft$=',
            r'-z$=',
            r'$=-Nightly',
            # ugly hack to narrow weird TF compose naming for RHEL-7
            r'RHEL-7-ELS-Nightly=RHEL-7.9-ZStream',
            ]
    new_string = string
    for m in mapping:
        r = re.fullmatch(r'([^\s=]+)=([^=]*)', m)
        if not r:
            raise Exception(f"Mapping {m} does not having expected format 'patten=value'")
        pattern, value = r.groups()
        # for regexp=True apply each matching regexp
        if regexp and re.search(pattern, new_string):
            new_string = re.sub(pattern, value, new_string)
            if logger:
                logger.debug(  # type: ignore[attr-defined]
                    f'Found match in {new_string} for mapping {m}, new value {new_string}')
        # for string matching return the first match
        if (not regexp) and new_string == pattern:
            if logger:
                logger.debug(  # type: ignore[attr-defined]
                    f'Found match in {new_string} for mapping {m}, new value {new_string}')
            return value
    return new_string


def derive_compose(release: str,
                   mapping: Optional[list[str]] = None,
                   logger: Optional[object] = None) -> str:
    """Derive RHEL compose from the provided errata release or brew/koji build target."""
    # when compose_mapping is provided, apply it with regexp disabled
    if mapping:
        compose = apply_release_mapping(
            release, mapping, regexp=False, logger=logger)
    # otherwise use the built-in default mapping
    else:
        compose = apply_release_mapping(release, logger=logger)
    return compose


def test_patterns_match(s: str, patterns: list[str]) -> tuple[bool, str]:
    """Test if string matches any of the given patterns."""
    for pattern in patterns:
        if s.strip() == pattern.strip():
            return (True, pattern)
    return (False, '')
