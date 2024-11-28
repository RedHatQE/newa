from newa import cli


def test_release_mapping():
    # Test that not passing erratum loads the default errata config and excepts
    matrix = [
        ('RHEL-10.0.BETA', 'RHEL-10-Beta-Nightly'),
        ('RHEL-10.0.GA', 'RHEL-10.0-Nightly'),
        ('RHEL-9.5.0.Z.MAIN', 'RHEL-9.5.0-Nightly'),
        ('RHEL-9.2.0.Z.EUS', 'RHEL-9.2.0-Nightly'),
        ('RHEL-9.0.0.Z.E4S', 'RHEL-9.0.0-Nightly'),
        ('RHEL-8.10.0.Z.MAIN+EUS', 'RHEL-8.10.0-Nightly'),
        ('RHEL-8.6.0.Z.AUS', 'RHEL-8.6.0-Nightly'),
        ]
    for release, distro in matrix:
        assert cli.apply_release_mapping(release) == distro
