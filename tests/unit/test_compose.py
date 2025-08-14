from newa import UNDEFINED_COMPOSE, Compose


def test_compose():
    def test(compose_id: str, prev_major: str, prev_minor: str):
        c = Compose(compose_id)
        assert (c.prev_major == prev_major)
        assert (c.prev_minor == prev_minor)
    # Checking various combinations
    test('foo', UNDEFINED_COMPOSE, UNDEFINED_COMPOSE)
    test('RHEL-', UNDEFINED_COMPOSE, UNDEFINED_COMPOSE)
    test('RHEL-6', UNDEFINED_COMPOSE, UNDEFINED_COMPOSE)
    test('RHEL-8.10.0-updates-20250811.3', 'RHEL-7-LatestUpdated', 'RHEL-8.9.0-Nightly')
    test('RHEL-9.0.0-Nightly', 'RHEL-8-Nightly', UNDEFINED_COMPOSE)
    test('RHEL-9.7.0-Nightly', 'RHEL-8-Nightly', 'RHEL-9.6.0-Nightly')
    test('RHEL-9-Nightly', 'RHEL-8-Nightly', UNDEFINED_COMPOSE)
    test('RHEL-10.1-Nightly', 'RHEL-9-Nightly', 'RHEL-10.0-Nightly')
    test('RHEL-10.0-Nightly', 'RHEL-9-Nightly', UNDEFINED_COMPOSE)
    test('Fedora', UNDEFINED_COMPOSE, UNDEFINED_COMPOSE)
    test('Fedora-42-Updated', 'Fedora-41-Updated', UNDEFINED_COMPOSE)
    test('Fedora-Rawhide-Nightly', UNDEFINED_COMPOSE, UNDEFINED_COMPOSE)
