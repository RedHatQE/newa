"""Tag filter expression parser and evaluator.

Supports a simple expression syntax for filtering by action tags:
- "|" = OR (match any of these tags)
- "," = AND (match all of these conditions)
- "!" = NOT (exclude these tags)

Examples:
    "regression|security"        - Match if action has 'regression' OR 'security' tag
    "smoke,rhel-9"              - Match if action has BOTH 'smoke' AND 'rhel-9' tags
    "!slow"                     - Match if action does NOT have 'slow' tag
    "regression|security,!slow" - Match if has (regression OR security) AND NOT slow
    "smoke,rhel-.*"             - Match if has 'smoke' AND a tag matching 'rhel-.*' regex
"""

import re
from dataclasses import dataclass
from typing import Optional


@dataclass
class TagFilter:
    """Parsed tag filter expression."""
    any_patterns: list[re.Pattern[str]]  # OR conditions (at least one must match)
    all_patterns: list[re.Pattern[str]]  # AND conditions (all must match)
    none_patterns: list[re.Pattern[str]]  # NOT conditions (none can match)

    def matches(self, action_tags: Optional[list[str]]) -> bool:
        """
        Check if the given action_tags match this filter.

        Args:
            action_tags: List of action tags (or None/empty if no tags)

        Returns:
            True if the tags match the filter, False otherwise.
        """
        if not action_tags:
            # No tags on the action
            # If we only have NONE patterns, consider it a match (nothing to exclude)
            # If we have ANY or ALL patterns, it's not a match (missing required tags)
            if self.none_patterns and not (self.any_patterns or self.all_patterns):
                return True
            # Only matches if we have no requirements (empty filter)
            return not (self.any_patterns or self.all_patterns or self.none_patterns)

        # Check NOT conditions first (highest priority)
        for pattern in self.none_patterns:
            if any(pattern.fullmatch(tag) for tag in action_tags):
                return False

        # Check ALL conditions (all patterns must match at least one tag)
        for pattern in self.all_patterns:
            if not any(pattern.fullmatch(tag) for tag in action_tags):
                return False

        # Check ANY conditions (at least one pattern must match at least one tag)
        # If no ANY patterns, this is a match. Otherwise, at least one must match.
        return not self.any_patterns or any(
            pattern.fullmatch(tag)
            for pattern in self.any_patterns
            for tag in action_tags
            )


def parse_tag_filter(filter_str: str) -> TagFilter:
    """
    Parse a tag filter expression into a TagFilter object.

    The expression format is: "pattern1|pattern2,pattern3,!pattern4"
    - "|" separates OR patterns (match any)
    - "," separates AND groups (match all)
    - "!" prefix indicates NOT patterns (exclude)

    Each pattern is treated as a regex pattern for matching tags.

    Args:
        filter_str: The filter expression string

    Returns:
        TagFilter object with compiled regex patterns

    Raises:
        ValueError: If the filter expression is invalid or contains bad regex patterns

    Examples:
        >>> f = parse_tag_filter("regression|security")
        >>> f.matches(["regression"])
        True
        >>> f.matches(["other"])
        False

        >>> f = parse_tag_filter("smoke,rhel-9")
        >>> f.matches(["smoke", "rhel-9"])
        True
        >>> f.matches(["smoke"])
        False

        >>> f = parse_tag_filter("!slow")
        >>> f.matches(["fast"])
        True
        >>> f.matches(["slow"])
        False
    """
    if not filter_str or not filter_str.strip():
        return TagFilter(any_patterns=[], all_patterns=[], none_patterns=[])

    any_patterns: list[re.Pattern[str]] = []
    all_patterns: list[re.Pattern[str]] = []
    none_patterns: list[re.Pattern[str]] = []

    # Split by comma to get AND groups
    and_groups = [g.strip() for g in filter_str.split(',') if g.strip()]

    for group in and_groups:
        # Check if this is a NOT pattern
        if group.startswith('!'):
            pattern_str = group[1:].strip()
            if not pattern_str:
                raise ValueError(f"Empty pattern after '!' in filter: {filter_str}")
            # Check for invalid combination of NOT with OR
            if '|' in pattern_str:
                raise ValueError(
                    f"NOT patterns (!) cannot be combined with OR (|): {group}")
            try:
                none_patterns.append(re.compile(pattern_str))
            except re.error as e:
                raise ValueError(
                    f"Invalid regex pattern '{pattern_str}' in filter: {e}") from e
        # Check if this group contains OR patterns
        elif '|' in group:
            or_patterns = [p.strip() for p in group.split('|') if p.strip()]
            for pattern_str in or_patterns:
                if pattern_str.startswith('!'):
                    raise ValueError(
                        f"NOT patterns (!) cannot be combined with OR (|): {group}")
                try:
                    any_patterns.append(re.compile(pattern_str))
                except re.error as e:
                    raise ValueError(
                        f"Invalid regex pattern '{pattern_str}' in filter: {e}") from e
        # Regular AND pattern
        else:
            pattern_str = group.strip()
            if not pattern_str:
                continue
            try:
                all_patterns.append(re.compile(pattern_str))
            except re.error as e:
                raise ValueError(
                    f"Invalid regex pattern '{pattern_str}' in filter: {e}") from e

    return TagFilter(
        any_patterns=any_patterns,
        all_patterns=all_patterns,
        none_patterns=none_patterns)
