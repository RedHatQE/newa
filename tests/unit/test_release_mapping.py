from unittest import mock

import pytest

from newa import cli


def test_release_mapping():
    # Test that not passing erratum loads the default errata config and excepts
    matrix = [
        # errata release mapping
        ('RHEL-10.0.BETA', 'RHEL-10-Beta-Nightly'),
        ('RHEL-10.0.GA', 'RHEL-10.0-Nightly'),
        ('RHEL-10.0.Z', 'RHEL-10.0-Nightly'),
        ('RHEL-9.5.0.Z.MAIN', 'RHEL-9.5.0-Nightly'),
        ('RHEL-9.2.0.Z.EUS', 'RHEL-9.2.0-Nightly'),
        ('RHEL-9.0.0.Z.E4S', 'RHEL-9.0.0-Nightly'),
        ('RHEL-8.10.0.Z.MAIN+EUS', 'RHEL-8.10.0-Nightly'),
        ('RHEL-8.6.0.Z.AUS', 'RHEL-8.6.0-Nightly'),
        ('RHEL-8.4.0.Z.EUS.EXTENSION', 'RHEL-8.4.0-Nightly'),
        ('RHEL-6-ELS-EXTENSION', 'RHEL-6.10-ZStream'),
        ('RHEL-7-ELS', 'RHEL-7.9-ZStream'),
        # build target mapping
        ('rhel-10.1-candidate', 'RHEL-10.1-Nightly'),
        ('rhel-9.7.0-draft', 'RHEL-9.7.0-Nightly'),
        ('rhel-10.1-z-draft', 'RHEL-10.1-Nightly'),
        ('rhel-6.10-z-draft', 'RHEL-6.10-ZStream'),
        ('rhel-6.10-z-candidate', 'RHEL-6.10-ZStream'),
        ('rhel-7.9-z-draft', 'RHEL-7.9-Nightly'),
        ]
    for release, distro in matrix:
        assert cli.apply_release_mapping(release) == distro


def test_release_mapping_debug_log():
    logger = mock.Mock()
    # custom (non-regexp) mapping logs the mapped value
    assert cli.apply_release_mapping(
        'rhel-6.10-z-draft', ['rhel-6.10-z-draft=RHEL-6-LatestUpdated'],
        regexp=False, logger=logger) == 'RHEL-6-LatestUpdated'
    logger.debug.assert_called_once_with(
        'Found match in rhel-6.10-z-draft for mapping '
        'rhel-6.10-z-draft=RHEL-6-LatestUpdated, new value RHEL-6-LatestUpdated')
    # regexp mapping logs the string before and after the substitution
    logger.reset_mock()
    cli.apply_release_mapping('rhel-10.1', [r'^rhel-=RHEL-'], logger=logger)
    logger.debug.assert_called_once_with(
        'Found match in rhel-10.1 for mapping ^rhel-=RHEL-, new value RHEL-10.1')


def test_release_mapping_invalid_format():
    with pytest.raises(Exception, match="does not have expected format 'pattern=value'"):
        cli.apply_release_mapping('rhel-10.1', ['no-equals-sign'])
