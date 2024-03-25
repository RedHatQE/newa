#!/usr/bin/env python3

# TODO: once we get unit tests, convert this to one

from newa import Erratum, ErratumJob, Event, EventType, eval_test

event = Event(type_=EventType.ERRATUM, id='foo')
erratum = Erratum(release='RHEL-9.4.0')
erratum_job = ErratumJob(event=event, erratum=erratum)

variables = {
    'EVENT': event,
    'ERRATUM': erratum,
    'JOB': erratum_job,
    }


def test(expression: str, expected: bool) -> None:
    assert eval_test(expression, **variables) is expected


# Checking if event type equals to "errata", "compose", ...
test('EVENT is erratum', True)
test('EVENT is not erratum', False)

test('JOB is erratum', True)
test('JOB is not erratum', False)

# Checking if errata artifact type equals to "rpm", "container" etc.
# TODO: don't know how to test this

# Checking if errata build list contains a specific package
# TODO: don't know how to test this

# Checking if errata number starts with (or contains or matches regexp) string "RHSA"
test('EVENT.id is match("f.*")', True)
test('EVENT.id is match("b.*")', False)

test('JOB.event.id is match("f.*")', True)
test('JOB.event.id is match("b.*")', False)

# Checking if errata release starts with (or contains or matches regexp) string "rhel-x.y"
test('JOB.erratum.release is match("RHEL-.*")', True)
test('JOB.erratum.release is match("(?i)rhel-.*")', True)
test('JOB.erratum.release is match("RHEL-9.7.0")', False)

# Checking if errata batch starts with (or contains or matches regexp) string "RHEL"
# TODO: there seems to be no `batch` attribute yet

# Negations of all checks above
test('JOB.erratum.release is not match("RHEL-.*")', False)
test('JOB.erratum.release is not match("(?i)rhel-.*")', False)
test('JOB.erratum.release is not match("RHEL-9.7.0")', True)
