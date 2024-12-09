# newa

## Contribute

Currently the code expects a stable Fedora release.

```shell
$ make system/fedora
$ hatch env create dev
$ hatch -e dev shell
$ newa
Usage: newa [OPTIONS] COMMAND1 [ARGS]... [COMMAND2 [ARGS]...
...
```

## NEWA configuration file

By default, NEWA settings is loaded from file `$HOME/.newa`.

Below is an example of such a file.
```
[erratatool]
url = https://..
[jira]
url = https://...
token = *JIRATOKEN*
[reportportal]
url = https://...
token = *RP_TOKEN*
project = my_personal
[testingfarm]
token = *TESTING_FARM_API_TOKEN*
recheck_delay = 120
```

This settings can be overriden by environment variables that takes precedence.
```
NEWA_ET_URL
NEWA_JIRA_URL
NEWA_JIRA_TOKEN
NEWA_JIRA_PROJECT
NEWA_REPORTPORTAL_URL
NEWA_REPORTPORTAL_TOKEN
NEWA_REPORTPORTAL_PROJECT
TESTING_FARM_API_TOKEN
NEWA_TF_RECHECK_DELAY
```

## Jira issue configuration file

This is a configuration for the `newa jira` subcommand and typically it targets a particular package and event, e.g. it prescribes which Jira issues should be created for an advisory.
This configuration file is passed to newa command like this:
```
newa ... jira --issue-config CONFIG_FILE ...
```
Issue config file may utilize Jinja2 templates in order to adjust configuration with event specific data.

Example of the Jira issue configuration file:
```
include: global_settings.yaml

project: MYPROJECT

transitions:
  closed:
    - Closed
  dropped:
    - Closed.Obsolete
  processed:
    - In Progress
  passed:
    - Closed.Done

defaults:
   assignee: '{{ ERRATUM.people_assigned_to }}'
#  fields:
#    "Pool Team": "my_great_team"
#    "Story Points": 0

issues:

 - summary: "Errata Workflow Checklist {% if ERRATUM.respin_count > 0 %}(respin {{ ERRATUM.respin_count }}){% endif %}"
   description: "Task tracking particular respin of errata."
   type: task
   id: errata_task
   parent_id: errata_epic
   on_respin: close
```

Individual settings are described below.

### include

Allows user to import snippet of a file from a different URL location or a file.
If the same section exists in both files, definitions from the included file
has lower priority and the whole section is replaced completely.
The only exceptions are are `issues` and `defaults` which are merged.
To unset a value defined in an included file one can set the value to `null`.

### project

Defines Jira project to be used by NEWA.

### transitions

This is a mapping which tells NEWA which particular issue states (and resolutions) it should be using. This settings depends on a particular Jira project. It is also possible to specify resolution separated by a dot, e.g. `Closed.Obsolete`.

The following transitions can be defined:

 - `closed` - Required, multiple states can be listed. Used to identify closed Jira issues.
 - `dropped` - Required, single state required. Tells NEWA which state to use when an issue is obsoleted by a newer issue.
 - `processed` - Optional, single state required. Necessary when `auto_transition` is `True`. This state is used when issue processing is finished by NEWA.
 - `passed` - Optional, single state required. Necessary when `auto_transition` is `True`. This state is used when all automated tests scheduled by NEWA pass.

### defaults

Defines the default settings for individual records in the `issues` list.

### issues

Each record represents a single Jira issue that will be processed by NEWA.


## Quick demo

Make sure you have your `$HOME/.newa` configuration file defined prior running this file.

```
$ REQUESTS_CA_BUNDLE=/etc/pki/tls/cert.pem newa event --compose CentOS-Stream-9 jira --issue-config demodata/jira-compose-config.yaml schedule execute report
```

Or

```
$ REQUESTS_CA_BUNDLE=/etc/pki/tls/cert.pem newa event --erratum 124115 jira --issue-config demodata/jira-errata-config.yaml schedule execute report
```

## Architecture

### Subcommand `event`

Gets event details either from a command line
```shell
newa event --erratum 12345
```
or from a files having `init-` prefix.

Produces multiple files based on the event (erratum) details,
splitting them according to the product release and populating
them with the `event` and `erratum` keys.

For example:
```shell
$ cat state/event-128049-RHEL-9.4.0.yaml
erratum:
  builds: []
  release: RHEL-9.4.0
event:
  id: '128049'
  type_: erratum

$ cat state/event-128049-RHEL-8.10.0.yaml
erratum:
  builds: []
  release: RHEL-8.10.0
event:
  id: '128049'
  type_: erratum
```

### Subcommand `jira`

Processes multiple files having `event-` prefix. For each event/file reads
Jira issue configuration file and for each item from the configuration it
creates or updates a Jira issue and produces `jira-` file, populating it
with `jira` and `recipe` keys.

For example:
```shell
$ cat state/jira-128049-RHEL-8.10.0-NEWA-12.yaml
erratum:
  builds: []
  release: RHEL-8.10.0
event:
  id: '128049'
  type_: erratum
jira:
  id: NEWA-12
recipe:
  url: https://path/to/recipe.yaml

$ cat state/jira-128049-RHEL-9.4.0-NEWA-6.yaml
erratum:
  builds: []
  release: RHEL-9.4.0
event:
  id: '128049'
  type_: erratum
jira:
  id: NEWA-6
recipe:
  url: https://path/to/recipe.yaml
```

### Subcommand `schedule`

Processes multiple files having `jira-` prefix. For each such file it
reads recipe details from `recipe.url` and according to that recipe
it produces multiple `request-` files, populating it with `recipe` key.

For example:
```shell
$ cat state/request-128049-RHEL-8.10.0-NEWA-12-REQ-1.yaml
erratum:
  builds: []
  release: RHEL-8.10.0
event:
  id: '128049'
  type_: erratum
jira:
  id: NEWA-12
recipe:
  url: https://path/to/recipe.yaml
request:
  context:
    distro: rhel-8.10.0
  environment: {}
  git_ref: ''
  git_url: ''
  id: REQ-1
  tmt_path: ''

$ cat state/request-128049-RHEL-8.10.0-NEWA-12-REQ-2.yaml
erratum:
  builds: []
  release: RHEL-8.10.0
event:
  id: '128049'
  type_: erratum
jira:
  id: NEWA-12
recipe:
  url: https://path/to/recipe.yaml
request:
  context:
    distro: rhel-8.10.0
  environment: {}
  git_ref: ''
  git_url: ''
  id: REQ-2
  tmt_path: ''
```

### Subcommand `cancel`

Cancels TF reqests found in `execute-` files within the given state-dir.

Example:
```
$ newa --prev-state-dir cancel
```

### Subcommand `execute`

Processes multiple files having `schedule-` prefix. For each such file it
reads request details from the inside and proceeds with the actual execution.
When tests are finished it produces files having `execute-` prefix updated with
details of the execution.

Example:
```
$ cat state/execute-RHEL-9.5.0-20240519.9-RHEL-9.5.0-20240519.9-BASEQESEC-1227-REQ-1.2.yaml
compose:
  id: RHEL-9.5.0-20240519.9
erratum: null
event:
  id: RHEL-9.5.0-20240519.9
  type_: compose
execution:
  artifacts_url: https://artifacts.somedomain.com/testing-farm/db0d98d2-f5c0-4f18-9308-66801f054342
  batch_id: 49aa0321898d
  return_code: 0
jira:
  id: BASEQESEC-1227
recipe:
  url: https://raw.githubusercontent.com/RedHatQE/newa/main/demodata/recipe1.yaml
request:
  compose: RHEL-9.5.0-20240519.9
  context:
    color: blue
  environment:
    CITY: Brno
    PLANET: Earth
  git_ref: main
  git_url: https://github.com/RedHatQE/newa.git
  id: REQ-1.2
  plan: /plan1
  rp_launch: recipe1
  tmt_path: demodata
  when: null
```

### Subcommand `report`

This subcommand currently takes care of the results reporting to Jira and
interaction with ReportPortal. It processes multiple files having `execute-` prefix,
reads RP launch details and searches for all the relevant launches, subsequently
merging them into a single launch. Later, it updates the respective Jira issue
with a note about test results availalability and a link to ReportPortal launch.
This subcommand doesn't produce any files.


### Subcommand `list`

With this subcommand you get a brief listing of the most recent newa invocations.
This information is based on state-directories on the default path /var/tmp/newa.

Example:

```
$ newa list
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
#
```
