"""Shared helper functions for filtering logic."""

from typing import Optional

from newa.cli.tag_filter import TagFilter


def should_filter_by_action_tags(
        action_tags: Optional[list[str]],
        tag_filter: TagFilter) -> bool:
    """
    Check if action_tags should be filtered based on the given tag filter.

    Args:
        action_tags: List of action tags (or None/empty if no tags)
        tag_filter: TagFilter object with parsed filter expression

    Returns:
        True if the action should be filtered out (skipped), False if it should be kept.
        An action is kept if it matches the tag filter criteria.
    """
    # The TagFilter.matches() returns True if tags match the filter
    # We need to invert this: filter out (return True) if tags DON'T match
    return not tag_filter.matches(action_tags)
