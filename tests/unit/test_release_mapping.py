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
        ('RHEL-7-ELS', 'RHEL-7.9-ZStream'),
        # build target mapping
        ('rhel-10.1-candidate', 'RHEL-10.1-Nightly'),
        ('rhel-9.7.0-draft', 'RHEL-9.7.0-Nightly'),
        ('rhel-10.1-z-draft', 'RHEL-10.1-Nightly'),
        ]
    for release, distro in matrix:
        assert cli.apply_release_mapping(release) == distro
