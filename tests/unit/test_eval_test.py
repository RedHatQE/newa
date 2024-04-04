from newa import Erratum, ErratumJob, Event, EventType, eval_test

event = Event(type_=EventType.ERRATUM, id='foo')
erratum = Erratum(release='RHEL-9.4.0')
erratum_job = ErratumJob(event=event, erratum=erratum)

variables = {
    'EVENT': event,
    'ERRATUM': erratum,
    'JOB': erratum_job,
    }


def test_expressions():
    def test(expression: str, expected: bool) -> bool:
        return eval_test(expression, **variables) is expected
    # Checking if event type equals to "errata", "compose", ...
    assert test('EVENT is erratum', True)
    assert test('EVENT is not erratum', False)

    assert test('JOB is erratum', True)
    assert test('JOB is not erratum', False)

    # Checking if errata artifact type equals to "rpm", "container" etc.
    # TODO: don't know how to test this

    # Checking if errata build list contains a specific package
    # TODO: don't know how to test this

    # Checking if errata number starts with (or contains or matches regexp) string "RHSA"
    assert test('EVENT.id is match("f.*")', True)
    assert test('EVENT.id is match("b.*")', False)

    assert test('JOB.event.id is match("f.*")', True)
    assert test('JOB.event.id is match("b.*")', False)

    # Checking if errata release starts with (or contains or matches regexp) string "rhel-x.y"
    assert test('JOB.erratum.release is match("RHEL-.*")', True)
    assert test('JOB.erratum.release is match("(?i)rhel-.*")', True)
    assert test('JOB.erratum.release is match("RHEL-9.7.0")', False)

    # Checking if errata batch starts with (or contains or matches regexp) string "RHEL"
    # TODO: there seems to be no `batch` attribute yet

    # Negations of all checks above
    assert test('JOB.erratum.release is not match("RHEL-.*")', False)
    assert test('JOB.erratum.release is not match("(?i)rhel-.*")', False)
    assert test('JOB.erratum.release is not match("RHEL-9.7.0")', True)
