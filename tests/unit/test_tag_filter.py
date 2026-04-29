"""Tests for tag filter expression parser and evaluator."""
import pytest

from newa.cli.tag_filter import TagFilter, parse_tag_filter


class TestParseTagFilter:
    """Tests for parse_tag_filter function."""

    def test_empty_string(self):
        """Empty filter string should return empty TagFilter."""
        result = parse_tag_filter("")
        assert result.any_patterns == []
        assert result.all_patterns == []
        assert result.none_patterns == []

    def test_whitespace_only(self):
        """Whitespace-only filter string should return empty TagFilter."""
        result = parse_tag_filter("   ")
        assert result.any_patterns == []
        assert result.all_patterns == []
        assert result.none_patterns == []

    def test_single_pattern(self):
        """Single pattern should be treated as AND pattern."""
        result = parse_tag_filter("regression")
        assert len(result.all_patterns) == 1
        assert result.all_patterns[0].pattern == "regression"
        assert result.any_patterns == []
        assert result.none_patterns == []

    def test_or_patterns(self):
        """Pipe-separated patterns should be treated as OR."""
        result = parse_tag_filter("regression|security")
        assert len(result.any_patterns) == 2
        assert {p.pattern for p in result.any_patterns} == {"regression", "security"}
        assert result.all_patterns == []
        assert result.none_patterns == []

    def test_and_patterns(self):
        """Comma-separated patterns should be treated as AND."""
        result = parse_tag_filter("smoke,rhel-9")
        assert len(result.all_patterns) == 2
        assert {p.pattern for p in result.all_patterns} == {"smoke", "rhel-9"}
        assert result.any_patterns == []
        assert result.none_patterns == []

    def test_not_pattern(self):
        """Exclamation prefix should create NOT pattern."""
        result = parse_tag_filter("!slow")
        assert len(result.none_patterns) == 1
        assert result.none_patterns[0].pattern == "slow"
        assert result.any_patterns == []
        assert result.all_patterns == []

    def test_combined_or_and_not(self):
        """Complex expression with OR, AND, and NOT."""
        result = parse_tag_filter("regression|security,!slow")
        assert len(result.any_patterns) == 2
        assert {p.pattern for p in result.any_patterns} == {"regression", "security"}
        assert len(result.none_patterns) == 1
        assert result.none_patterns[0].pattern == "slow"
        assert result.all_patterns == []

    def test_regex_patterns(self):
        """Regex patterns should be compiled correctly."""
        result = parse_tag_filter("rhel-.*,tier[12]")
        assert len(result.all_patterns) == 2
        # Verify patterns can match
        assert result.all_patterns[0].fullmatch("rhel-9")
        assert result.all_patterns[1].fullmatch("tier1")
        assert not result.all_patterns[1].fullmatch("tier3")

    def test_invalid_regex_raises_error(self):
        """Invalid regex pattern should raise ValueError."""
        with pytest.raises(ValueError, match="Invalid regex pattern"):
            parse_tag_filter("tier[")

    def test_empty_not_pattern_raises_error(self):
        """Empty pattern after ! should raise ValueError."""
        with pytest.raises(ValueError, match="Empty pattern after '!'"):
            parse_tag_filter("!")

    def test_not_with_or_raises_error(self):
        """NOT cannot be combined with OR in same group."""
        with pytest.raises(ValueError, match="NOT patterns.*cannot be combined with OR"):
            parse_tag_filter("!slow|fast")

    def test_whitespace_handling(self):
        """Whitespace around patterns should be stripped."""
        result = parse_tag_filter(" regression | security , !slow ")
        assert len(result.any_patterns) == 2
        assert len(result.none_patterns) == 1


class TestTagFilterMatches:
    """Tests for TagFilter.matches() method."""

    def test_empty_filter_matches_empty_tags(self):
        """Empty filter matches empty/no tags."""
        f = TagFilter(any_patterns=[], all_patterns=[], none_patterns=[])
        assert f.matches(None) is True
        assert f.matches([]) is True

    def test_empty_filter_matches_any_tags(self):
        """Empty filter matches any tags."""
        f = TagFilter(any_patterns=[], all_patterns=[], none_patterns=[])
        assert f.matches(["foo", "bar"]) is True

    def test_any_pattern_matches(self):
        """ANY pattern matches if at least one tag matches."""
        import re
        f = TagFilter(
            any_patterns=[re.compile("regression"), re.compile("security")],
            all_patterns=[],
            none_patterns=[])

        assert f.matches(["regression"]) is True
        assert f.matches(["security"]) is True
        assert f.matches(["regression", "other"]) is True
        assert f.matches(["other"]) is False
        assert f.matches([]) is False
        assert f.matches(None) is False

    def test_all_pattern_matches(self):
        """ALL patterns must all match at least one tag each."""
        import re
        f = TagFilter(
            any_patterns=[],
            all_patterns=[re.compile("smoke"), re.compile("rhel-9")],
            none_patterns=[])

        assert f.matches(["smoke", "rhel-9"]) is True
        assert f.matches(["smoke", "rhel-9", "tier1"]) is True
        assert f.matches(["smoke"]) is False
        assert f.matches(["rhel-9"]) is False
        assert f.matches([]) is False
        assert f.matches(None) is False

    def test_none_pattern_excludes(self):
        """NONE pattern excludes tags that match."""
        import re
        f = TagFilter(
            any_patterns=[],
            all_patterns=[],
            none_patterns=[re.compile("slow")])

        assert f.matches(["fast"]) is True
        assert f.matches(["regression"]) is True
        assert f.matches(["slow"]) is False
        assert f.matches(["fast", "slow"]) is False
        assert f.matches([]) is True  # No tags = no match to exclude
        assert f.matches(None) is True

    def test_combined_any_and_none(self):
        """Combined ANY and NONE patterns."""
        import re
        f = TagFilter(
            any_patterns=[re.compile("regression"), re.compile("security")],
            all_patterns=[],
            none_patterns=[re.compile("slow")])

        # Has matching ANY tag and no NONE tag
        assert f.matches(["regression"]) is True
        assert f.matches(["security", "fast"]) is True

        # Has matching ANY tag but also has NONE tag
        assert f.matches(["regression", "slow"]) is False
        assert f.matches(["security", "slow"]) is False

        # No matching ANY tag
        assert f.matches(["fast"]) is False
        assert f.matches([]) is False

    def test_combined_all_and_none(self):
        """Combined ALL and NONE patterns."""
        import re
        f = TagFilter(
            any_patterns=[],
            all_patterns=[re.compile("smoke"), re.compile("rhel-9")],
            none_patterns=[re.compile("slow")])

        # Has all ALL tags and no NONE tag
        assert f.matches(["smoke", "rhel-9"]) is True
        assert f.matches(["smoke", "rhel-9", "tier1"]) is True

        # Has all ALL tags but also has NONE tag
        assert f.matches(["smoke", "rhel-9", "slow"]) is False

        # Missing one ALL tag
        assert f.matches(["smoke"]) is False

    def test_complex_expression(self):
        """Test complex expression: (regression|security),tier1,!slow"""
        import re
        f = TagFilter(
            any_patterns=[re.compile("regression"), re.compile("security")],
            all_patterns=[re.compile("tier1")],
            none_patterns=[re.compile("slow")])

        # Has (regression OR security) AND tier1 AND NOT slow
        assert f.matches(["regression", "tier1"]) is True
        assert f.matches(["security", "tier1"]) is True
        assert f.matches(["regression", "security", "tier1"]) is True

        # Missing tier1
        assert f.matches(["regression"]) is False
        assert f.matches(["security"]) is False

        # Has slow
        assert f.matches(["regression", "tier1", "slow"]) is False

        # No regression or security
        assert f.matches(["tier1"]) is False

    def test_regex_patterns_in_matches(self):
        """Test that regex patterns work correctly in matches."""
        import re
        f = TagFilter(
            any_patterns=[],
            all_patterns=[re.compile(r"rhel-\d+"), re.compile(r"tier[12]")],
            none_patterns=[])

        assert f.matches(["rhel-9", "tier1"]) is True
        assert f.matches(["rhel-10", "tier2"]) is True
        assert f.matches(["rhel-9", "tier3"]) is False  # tier3 doesn't match tier[12]
        assert f.matches(["rhel-foo", "tier1"]) is False  # rhel-foo doesn't match rhel-\d+


class TestTagFilterIntegration:
    """Integration tests with real-world scenarios."""

    def test_simple_or_filter(self):
        """User wants: regression OR security tests."""
        f = parse_tag_filter("regression|security")

        assert f.matches(["regression"]) is True
        assert f.matches(["security"]) is True
        assert f.matches(["regression", "tier1"]) is True
        assert f.matches(["tier1"]) is False
        assert f.matches([]) is False

    def test_simple_and_filter(self):
        """User wants: smoke AND rhel-9 tests."""
        f = parse_tag_filter("smoke,rhel-9")

        assert f.matches(["smoke", "rhel-9"]) is True
        assert f.matches(["smoke", "rhel-9", "tier1"]) is True
        assert f.matches(["smoke"]) is False
        assert f.matches(["rhel-9"]) is False

    def test_exclusion_filter(self):
        """User wants: anything except slow tests."""
        f = parse_tag_filter("!slow")

        assert f.matches(["fast"]) is True
        assert f.matches(["regression"]) is True
        assert f.matches(["slow"]) is False
        assert f.matches(["fast", "slow"]) is False

    def test_complex_real_world(self):
        """User wants: (regression OR security) AND tier1 AND NOT slow."""
        f = parse_tag_filter("regression|security,tier1,!slow")

        # Valid combinations
        assert f.matches(["regression", "tier1", "fast"]) is True
        assert f.matches(["security", "tier1"]) is True
        assert f.matches(["regression", "security", "tier1"]) is True

        # Invalid combinations
        assert f.matches(["regression", "tier2"]) is False  # wrong tier
        assert f.matches(["security", "tier1", "slow"]) is False  # has slow
        assert f.matches(["tier1"]) is False  # missing regression/security

    def test_regex_real_world(self):
        """User wants: rhel-9.* version AND (tier1 OR tier2) tests."""
        f = parse_tag_filter("rhel-9.*,tier1|tier2")

        assert f.matches(["rhel-9.5", "tier1"]) is True
        assert f.matches(["rhel-9.10", "tier2"]) is True
        assert f.matches(["rhel-9", "tier1"]) is True
        assert f.matches(["rhel-8.10", "tier1"]) is False  # wrong rhel version
        assert f.matches(["rhel-9.5", "tier3"]) is False  # wrong tier
