"""Tests for errata deduplication logic."""

from newa.services.errata_service import _deduplicate_errata_by_compose


def test_deduplicate_no_duplicates():
    """Test that releases mapping to different composes are not deduplicated."""
    candidates = [
        {
            'release': 'RHEL-9.5.0.Z.MAIN',
            'builds': ['build-1.el9'],
            'archs': ['x86_64'],
            },
        {
            'release': 'RHEL-9.4.0.Z.EUS',
            'builds': ['build-1.el9'],
            'archs': ['x86_64'],
            },
        ]

    result = _deduplicate_errata_by_compose(candidates)

    # Both should be kept as they map to different composes
    # RHEL-9.5.0.Z.MAIN -> RHEL-9.5.0-Nightly
    # RHEL-9.4.0.Z.EUS -> RHEL-9.4.0-Nightly
    assert len(result) == 2


def test_deduplicate_identical_builds_and_archs():
    """Test that duplicate releases with identical builds and archs are filtered."""
    candidates = [
        {
            'release': 'RHEL-9.5.0.Z.MAIN',
            'builds': ['build-1.el9', 'build-2.el9'],
            'archs': ['x86_64', 'aarch64'],
            },
        {
            'release': 'RHEL-9.5.0.Z.EUS',
            'builds': ['build-1.el9', 'build-2.el9'],
            'archs': ['aarch64', 'x86_64'],  # Same archs, different order
            },
        ]

    result = _deduplicate_errata_by_compose(candidates)

    # Only one should be kept as both map to RHEL-9.5.0-Nightly
    # and have identical builds and archs
    assert len(result) == 1
    # The first one should be kept (same number of archs and builds)
    assert result[0]['release'] == 'RHEL-9.5.0.Z.MAIN'


def test_deduplicate_subset_architectures():
    """Test that releases with subset architectures are filtered out."""
    candidates = [
        {
            'release': 'RHEL-8.10.0.Z.MAIN',
            'builds': ['build-1.el8'],
            'archs': ['x86_64', 'aarch64', 'ppc64le'],
            },
        {
            'release': 'RHEL-8.10.0.Z.EUS',
            'builds': ['build-1.el8'],
            'archs': ['x86_64', 'aarch64'],  # Subset of first
            },
        ]

    result = _deduplicate_errata_by_compose(candidates)

    # Only the first should be kept as it has more architectures
    # Both map to RHEL-8.10.0-Nightly
    assert len(result) == 1
    assert result[0]['release'] == 'RHEL-8.10.0.Z.MAIN'
    assert len(result[0]['archs']) == 3


def test_deduplicate_superset_covers_multiple_releases():
    """Test that a superset release filters out multiple releases with different arch subsets."""
    candidates = [
        {
            'release': 'RHEL-9.5.0.Z.MAIN',
            'builds': ['build-1.el9'],
            'archs': ['x86_64'],
            },
        {
            'release': 'RHEL-9.5.0.Z.EUS',
            'builds': ['build-1.el9'],
            'archs': ['aarch64'],
            },
        {
            'release': 'RHEL-9.5.0.Z.E4S',
            'builds': ['build-1.el9'],
            'archs': ['aarch64', 'ppc64le', 'x86_64'],  # Superset of both above
            },
        ]

    result = _deduplicate_errata_by_compose(candidates)

    # Only the superset should be kept (E4S with all 3 architectures)
    # All three map to RHEL-9.5.0-Nightly
    assert len(result) == 1
    assert result[0]['release'] == 'RHEL-9.5.0.Z.E4S'
    assert set(result[0]['archs']) == {'aarch64', 'ppc64le', 'x86_64'}


def test_deduplicate_different_builds_same_compose():
    """Test that releases with different builds are not deduplicated."""
    candidates = [
        {
            'release': 'RHEL-9.2.0.Z.MAIN',
            'builds': ['build-1.el9'],
            'archs': ['x86_64'],
            },
        {
            'release': 'RHEL-9.2.0.Z.EUS',
            'builds': ['build-2.el9'],
            'archs': ['x86_64'],
            },
        ]

    result = _deduplicate_errata_by_compose(candidates)

    # Both should be kept as they have different builds
    # even though they map to the same compose (RHEL-9.2.0-Nightly)
    assert len(result) == 2


def test_deduplicate_superset_architectures_kept():
    """Test that releases with superset architectures are kept over subsets."""
    candidates = [
        {
            'release': 'RHEL-9.0.0.Z.E4S',
            'builds': ['build-1.el9'],
            'archs': ['x86_64'],
            },
        {
            'release': 'RHEL-9.0.0.Z.EUS',
            'builds': ['build-1.el9'],
            'archs': ['aarch64', 'x86_64'],  # Superset
            },
        ]

    result = _deduplicate_errata_by_compose(candidates)

    # Only the one with more architectures should be kept
    # Both map to RHEL-9.0.0-Nightly
    assert len(result) == 1
    assert result[0]['release'] == 'RHEL-9.0.0.Z.EUS'
    assert set(result[0]['archs']) == {'aarch64', 'x86_64'}


def test_deduplicate_multiple_composes_with_duplicates():
    """Test deduplication across multiple compose groups."""
    candidates = [
        # First compose group: RHEL-9.5.0-Nightly
        {
            'release': 'RHEL-9.5.0.Z.MAIN',
            'builds': ['build-1.el9'],
            'archs': ['x86_64', 'aarch64'],
            },
        {
            'release': 'RHEL-9.5.0.Z.EUS',
            'builds': ['build-1.el9'],
            'archs': ['x86_64'],  # Subset
            },
        # Second compose group: RHEL-9.4.0-Nightly
        {
            'release': 'RHEL-9.4.0.Z.MAIN',
            'builds': ['build-2.el9'],
            'archs': ['x86_64'],
            },
        {
            'release': 'RHEL-9.4.0.Z.EUS',
            'builds': ['build-2.el9'],
            'archs': ['x86_64'],  # Identical to above
            },
        ]

    result = _deduplicate_errata_by_compose(candidates)

    # Should keep 2: one from each compose group
    assert len(result) == 2
    releases = [r['release'] for r in result]
    assert 'RHEL-9.5.0.Z.MAIN' in releases  # More archs
    assert 'RHEL-9.4.0.Z.MAIN' in releases or 'RHEL-9.4.0.Z.EUS' in releases


def test_deduplicate_empty_list():
    """Test that empty list returns empty result."""
    result = _deduplicate_errata_by_compose([])
    assert result == []


def test_deduplicate_single_candidate():
    """Test that single candidate is returned as-is."""
    candidates = [
        {
            'release': 'RHEL-9.5.0.Z.MAIN',
            'builds': ['build-1.el9'],
            'archs': ['x86_64'],
            },
        ]

    result = _deduplicate_errata_by_compose(candidates)
    assert len(result) == 1
    assert result[0]['release'] == 'RHEL-9.5.0.Z.MAIN'


def test_deduplicate_preserves_metadata():
    """Test that deduplication preserves all candidate metadata."""
    candidates = [
        {
            'release': 'RHEL-9.5.0.Z.MAIN',
            'builds': ['build-1.el9'],
            'archs': ['x86_64'],
            'blocking_builds': ['blocker-1.el9'],
            'components': ['component1'],
            'content_type': 'rpm',
            },
        ]

    result = _deduplicate_errata_by_compose(candidates)

    assert len(result) == 1
    assert result[0]['blocking_builds'] == ['blocker-1.el9']
    assert result[0]['components'] == ['component1']
    assert result[0]['content_type'] == 'rpm'


def test_deduplicate_els_releases():
    """Test deduplication with ELS releases."""
    candidates = [
        {
            'release': 'RHEL-9.0.0.Z.ELS',
            'builds': ['build-1.el9'],
            'archs': ['x86_64'],
            },
        {
            'release': 'RHEL-9.0.0.Z.E4S',
            'builds': ['build-1.el9'],
            'archs': ['x86_64'],
            },
        ]

    result = _deduplicate_errata_by_compose(candidates)

    # Both map to RHEL-9.0.0-Nightly, should deduplicate
    assert len(result) == 1


def test_deduplicate_sorting_by_build_count():
    """Test that releases are sorted by build count when arch count is same."""
    candidates = [
        {
            'release': 'RHEL-9.5.0.Z.EUS',
            'builds': ['build-1.el9'],
            'archs': ['x86_64'],
            },
        {
            'release': 'RHEL-9.5.0.Z.MAIN',
            'builds': ['build-1.el9', 'build-2.el9'],
            'archs': ['x86_64'],
            },
        ]

    result = _deduplicate_errata_by_compose(candidates)

    # Should keep the one with more builds
    assert len(result) == 1
    assert result[0]['release'] == 'RHEL-9.5.0.Z.MAIN'
    assert len(result[0]['builds']) == 2


def test_deduplicate_overlapping_builds_different_architectures():
    """Test that releases with overlapping builds but different architectures are both kept."""
    candidates = [
        {
            'release': 'RHEL-9.5.0.Z.MAIN',
            'builds': ['build-1.el9', 'build-2.el9'],
            'archs': ['x86_64', 'aarch64'],
            },
        {
            'release': 'RHEL-9.5.0.Z.EUS',
            'builds': ['build-1.el9', 'build-3.el9'],
            'archs': ['ppc64le', 's390x'],
            },
        ]

    result = _deduplicate_errata_by_compose(candidates)

    # Both should be kept as they have different builds and different architectures
    # Neither is a subset of the other
    assert len(result) == 2
    releases = {r['release'] for r in result}
    assert releases == {'RHEL-9.5.0.Z.MAIN', 'RHEL-9.5.0.Z.EUS'}
