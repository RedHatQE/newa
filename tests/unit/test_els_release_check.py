from newa.utils.helpers import els_release_check


def test_els_release_check():
    # Test that not passing erratum loads the default errata config and excepts
    matrix = [
        ('RHEL-10.2.GA', False),
        ('RHEL-9.7.0.Z.MAIN', False),
        ('RHEL-10.1.Z', False),
        ('RHEL-9.2.0.Z.EUS', True),
        ('RHEL-9.0.0.Z.E4S', True),
        ('RHEL-8.10.0.Z.MAIN+EUS', True),
        ('RHEL-8.6.0.Z.AUS', True),
        ('RHEL-7-ELS', True),
        ]
    for release, result in matrix:
        assert els_release_check(release) == result
