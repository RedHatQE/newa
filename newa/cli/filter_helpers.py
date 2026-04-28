"""Shared helper functions for filtering logic."""

from re import Pattern
from typing import Optional


def should_filter_by_action_tags(
        action_tags: Optional[list[str]],
        pattern: Pattern[str]) -> bool:
    """
    Check if action_tags should be filtered based on the given pattern.

    Args:
        action_tags: List of action tags (or None/empty if no tags)
        pattern: Compiled regex pattern to match against tags

    Returns:
        True if the action should be filtered out (skipped), False if it should be kept.
        An action is kept if ANY of its tags matches the pattern.
    """
    if not action_tags:
        # No tags, filter it out
        return True

    # Check if any tag matches the pattern (using fullmatch for exact match)
    return not any(pattern.fullmatch(tag) for tag in action_tags)
