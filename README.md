# newa

## Contribute

Currently the code expects a stable Fedora release.

```shell
$ make system/fedora
$ hatch env create dev
$ hatch -e dev shell
$ newa
Newa!
$
```

## In-config tests

Built on https://jinja.palletsprojects.com/en/3.1.x/templates/#builtin-tests, adds the following tests:

* `erratum` - whether an event or job are processing an erratum
* `match` - whether a string matches given regular exception

`newa` should expose the following variables to expressions:

* `JOB` - an instance of `Job` subclass, e.g. `ErratumJob`
* `EVENT` - also `JOB.event`
* `ERRATUM` - also `JOB.erratum` if `JOB is erratum`

A couple of examples:

```yaml
# Checking if event type equals to "errata", "compose", ...
when: EVENT is erratum
when: EVENT is not erratum

when: JOB is erratum
when: JOB is not erratum

# Checking if errata number starts with (or contains or matches regexp) string "RHSA"
when: EVENT.id is match("f.*")
when: EVENT.id is match("b.*")

when: JOB.event.id is match("f.*")
when: JOB.event.id is match("b.*")

# Checking if errata release starts with (or contains or matches regexp) string "rhel-x.y"
when: JOB.erratum.release is match("RHEL-.*")
when: JOB.erratum.release is match("(?i)rhel-.*")
when: JOB.erratum.release is match("RHEL-9.7.0")

# Maybe we could add custom tests, e.g.:
when: JOB is RHEL
when: JOB is RHEL_9
when: JOB is not RHEL_9_5_0

# Negations of all checks above
when: JOB.erratum.release is not match("RHEL-.*")
when: JOB.erratum.release is not match("(?i)rhel-.*")
when: JOB.erratum.release is not match("RHEL-9.7.0")
```
