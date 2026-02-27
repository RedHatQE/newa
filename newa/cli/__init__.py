"""NEWA CLI package."""

from newa.cli.commands.event_cmd import cmd_event
from newa.cli.main import main
from newa.cli.utils import apply_release_mapping
from newa.models.settings import CLIContext

__all__ = ['CLIContext', 'apply_release_mapping', 'cmd_event', 'main']
