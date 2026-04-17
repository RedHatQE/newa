"""Color utilities for NEWA CLI output."""

import os
import sys
from pathlib import Path
from typing import Optional

# Default color codes
DEFAULT_COLORS = {
    'RED': '\033[38;5;203m',  # light red (salmon pink)
    'GREEN': '\033[32m',
    'YELLOW': '\033[33m',
    'BLUE': '\033[38;5;75m',  # medium blue
    'ORANGE': '\033[38;5;208m',
    'PURPLE': '\033[38;5;141m',
    }


class Colors:
    """ANSI color codes."""

    # Reset
    RESET = '\033[0m'

    # Colors - initialized with defaults
    RED = DEFAULT_COLORS['RED']
    GREEN = DEFAULT_COLORS['GREEN']
    YELLOW = DEFAULT_COLORS['YELLOW']
    BLUE = DEFAULT_COLORS['BLUE']
    ORANGE = DEFAULT_COLORS['ORANGE']
    PURPLE = DEFAULT_COLORS['PURPLE']

    # Palette colors (for output elements)
    STATE_DIR: Optional[str] = None  # None means use default (no override)
    EVENT: Optional[str] = None
    ISSUE: Optional[str] = None
    REQUEST_ID: Optional[str] = None
    REPORTPORTAL: Optional[str] = None

    # State colors
    STATE_NOT_EXECUTED: Optional[str] = None
    STATE_RUNNING: Optional[str] = None
    STATE_COMPLETE: Optional[str] = None
    STATE_ERROR: Optional[str] = None
    STATE_DEFAULT: Optional[str] = None

    # Result colors
    RESULT_PASSED: Optional[str] = None
    RESULT_FAILED: Optional[str] = None
    RESULT_NONE: Optional[str] = None
    RESULT_CANCELLED: Optional[str] = None
    RESULT_DEFAULT: Optional[str] = None

    # Disable all colors
    @classmethod
    def disable(cls) -> None:
        """Disable all colors by setting them to empty strings."""
        cls.RESET = ''
        cls.RED = ''
        cls.GREEN = ''
        cls.YELLOW = ''
        cls.BLUE = ''
        cls.ORANGE = ''
        cls.PURPLE = ''


def should_use_colors() -> bool:
    """
    Determine if colored output should be used.

    Checks environment variables and terminal capabilities:
    - NO_COLOR: If set (to any value), disables colors
    - FORCE_COLOR: If set (to any value), forces colors
    - Otherwise: Auto-detect based on terminal capabilities

    Returns:
        bool: True if colors should be used, False otherwise
    """
    # Check NO_COLOR environment variable (highest priority for disabling)
    if os.environ.get('NO_COLOR'):
        return False

    # Check FORCE_COLOR environment variable
    if os.environ.get('FORCE_COLOR'):
        return True

    # Auto-detect: check if stdout is a TTY and supports colors
    if not hasattr(sys.stdout, 'isatty'):
        return False

    if not sys.stdout.isatty():
        return False

    # Check TERM environment variable for color support
    term = os.environ.get('TERM', '')
    # Most modern terminals support colors
    return term != 'dumb'


def colorize(text: str, color_code: str) -> str:
    """
    Colorize text with the given ANSI color code.

    Args:
        text: The text to colorize
        color_code: The ANSI color code (e.g., Colors.RED)

    Returns:
        str: The colorized text with reset code appended
    """
    if not color_code:  # Colors disabled
        return text
    return f'{color_code}{text}{Colors.RESET}'


def colorize_state(state: str) -> str:
    """
    Colorize request state according to the color scheme.

    States:
    - 'not executed': yellow (or configured)
    - 'running': blue (or configured)
    - 'complete'/'finished': green (or configured)
    - 'error': red (or configured)
    - others: orange (or configured)

    Args:
        state: The state string

    Returns:
        str: The colorized state string
    """
    state_lower = state.lower() if state else ''

    if 'not executed' in state_lower:
        color = Colors.STATE_NOT_EXECUTED or Colors.YELLOW
        return colorize(state, color)
    if 'running' in state_lower:
        color = Colors.STATE_RUNNING or Colors.BLUE
        return colorize(state, color)
    if state_lower in ('complete', 'finished'):
        color = Colors.STATE_COMPLETE or Colors.GREEN
        return colorize(state, color)
    if 'error' in state_lower:
        color = Colors.STATE_ERROR or Colors.RED
        return colorize(state, color)
    # Default for other states (e.g., 'executed, not reported', 'cancelled')
    color = Colors.STATE_DEFAULT or Colors.ORANGE
    return colorize(state, color)


def colorize_result(result: str) -> str:
    """
    Colorize request result according to the color scheme.

    Results:
    - 'passed': green (or configured)
    - 'failed': red (or configured)
    - 'None': blue (or configured)
    - 'cancelled'/'canceled': orange (or configured)
    - others (error, skipped, etc.): orange (or configured)

    Args:
        result: The result string

    Returns:
        str: The colorized result string
    """
    result_lower = result.lower() if result else ''

    if result_lower == 'passed':
        color = Colors.RESULT_PASSED or Colors.GREEN
        return colorize(result, color)
    if result_lower == 'failed':
        color = Colors.RESULT_FAILED or Colors.RED
        return colorize(result, color)
    if result_lower == 'none':
        color = Colors.RESULT_NONE or Colors.BLUE
        return colorize(result, color)
    if result_lower in ('cancelled', 'canceled'):
        color = Colors.RESULT_CANCELLED or Colors.ORANGE
        return colorize(result, color)
    # Default for error, skipped, and other values
    color = Colors.RESULT_DEFAULT or Colors.ORANGE
    return colorize(result, color)


def colorize_text(text: str, color_code: str) -> str:
    """
    Colorize arbitrary text with the given color.

    This is a convenience wrapper around colorize() for specific color names.

    Args:
        text: The text to colorize
        color_code: The ANSI color code (e.g., Colors.ORANGE)

    Returns:
        str: The colorized text
    """
    return colorize(text, color_code)


def init_colors(color_config_path: Optional[str] = None) -> None:
    """
    Initialize colors based on environment, terminal detection, and config file.

    Args:
        color_config_path: Path to color configuration file. If None, no config is loaded.
    """
    if not should_use_colors():
        Colors.disable()
        return

    # Load color configuration if path provided
    if color_config_path:
        from newa.cli.color_config import ColorConfig

        config_path = Path(color_config_path)
        if config_path.exists():
            color_config = ColorConfig.load_from_file(config_path)

            # Load palette colors
            Colors.STATE_DIR = color_config.get_color('palette', 'state_dir')
            Colors.EVENT = color_config.get_color('palette', 'event')
            Colors.ISSUE = color_config.get_color('palette', 'issue')
            Colors.REQUEST_ID = color_config.get_color('palette', 'request_id')
            Colors.REPORTPORTAL = color_config.get_color('palette', 'reportportal')

            # Load state colors
            Colors.STATE_NOT_EXECUTED = color_config.get_color('states', 'not_executed')
            Colors.STATE_RUNNING = color_config.get_color('states', 'running')
            Colors.STATE_COMPLETE = color_config.get_color('states', 'complete')
            Colors.STATE_ERROR = color_config.get_color('states', 'error')
            Colors.STATE_DEFAULT = color_config.get_color('states', 'default')

            # Load result colors
            Colors.RESULT_PASSED = color_config.get_color('results', 'passed')
            Colors.RESULT_FAILED = color_config.get_color('results', 'failed')
            Colors.RESULT_NONE = color_config.get_color('results', 'none')
            Colors.RESULT_CANCELLED = color_config.get_color('results', 'cancelled')
            Colors.RESULT_DEFAULT = color_config.get_color('results', 'default')
