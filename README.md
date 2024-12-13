# newa

## About

The New Errata Workflow Automation (NEWA) is an attempt to replace legacy testing workflow with a new one based on the use of `tmt` (Test Management Tool), Testing Farm, Jira and ReportPortal. It ensures transparency and consistency by “standardizing” the errata testing.

## NEWA based workflow

This is the assumed workflow utilizing NEWA in short:
 1. The tester (in the future an automated job in Jenkins) runs NEWA command on erratum update.
 2. NEWA populates Jira with issues that will be used for tracking testing progress.
 3. For Jira issues associated with a recipe file NEWA will trigger Testing Farm jobs.
 4. When all TF jobs are finished NEWA updates the respective Jira issue with a link to RP launch so that a tester can review test results and eventually mark the issue as Done.


## NEWA configuration

The workflow described above requires NEWA to be properly configured by a user. In particular, a user needs to prepare:
 - NEWA configuration file providing URLs and access tokens to individual tools.
 - NEWA issue-config YAML file defining which Jira issues should be created. These issues could represent fully manual steps (like errata checklist items) or steps that are fully or partially automated through the associated recipe YAML file.
 - NEWA recipe YAML file containing necessary metadata for the test execution, for example test repository and tmt plans to be executed.  These plans are parametrized using environment variables defined by the recipe file, ensuring that all required scenarios are tested.

All these files are described in detail below.

### NEWA configuration file

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

This settings can be overriden by environment variables that take precedence.
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

### Jira issue configuration file

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

#### include

Allows user to import snippet of a file from a different URL location or a file.
If the same section exists in both files, definitions from the included file
has lower priority and the whole section is replaced completely.
The only exceptions are are `issues` and `defaults` which are merged.
To unset a value defined in an included file one can set the value to `null`.

#### project and group

Defines Jira project to be used by NEWA and optionally also a user group for access restrictions.

Example:
```
project: MYPROJECT
group: "Company users"
```

#### transitions

This is a mapping which tells NEWA which particular issue states (and resolutions) it should be using. This settings depends on a particular Jira project. It is also possible to specify resolution separated by a dot, e.g. `Closed.Obsolete`.

The following transitions can be defined:

 - `closed` - Required, multiple states can be listed. Used to identify closed Jira issues.
 - `dropped` - Required, single state required. Tells NEWA which state to use when an issue is obsoleted by a newer issue.
 - `processed` - Optional, single state required. Necessary when `auto_transition` is `True`. This state is used when issue processing is finished by NEWA.
 - `passed` - Optional, single state required. Necessary when `auto_transition` is `True`. This state is used when all automated tests scheduled by NEWA pass.

Example:
```
transitions:
  closed:
    - Closed
  dropped:
    - Closed.Obsolete
  processed:
    - In Progress
# here, not using transition for passed tests
# passed:
#  - Closed.Done
```

#### defaults

Defines the default settings for individual records in the `issues` list. This settings can be overriden by a value defined in a particular issue.

Example:
```
defaults:
   assignee: '{{ ERRATUM.people_assigned_to }}'
   fields:
     "Pool Team": "my_great_team"
     "Story Points": 1
```
See `issues` section below for available options.

#### issues

Each record represents a single Jira issue that will be processed by NEWA.
The following options are available:
 - `summary`: Jira issue summary to use
 - `description`: Jira issue description to use
 - `type`: Jira issue type, could be `epic`, `task`, `sub-task`
 - `id`: unique identifier within the scope of issue-config file, it is used to identify this specific config item.
 - `parent_id`: refers to item `id` which should become a parent Jira issue of this issue.
 - `on_respin`: Defines action when the issue is obsoleted by a newer version (due to erratum respin). Possible values are `close` (i.e. create a new issue) and `keep` (i.e. reuse existing issue).
 - `auto_transition`: Defines if automatic issue state transitions are enabled (`True`) or not (`False`, a default value).
 - `when`: A condition that restricts when an item should be used. See "In-config tests" section for examples.

### Recipe config file

This configuration prescribes which automated jobs should be triggered in Testing Farm.
A recipe file is associated with a Jira issue through the `job_recipe` attribute in the issue config file and this Jira issue gets later updated with test results once all Testing Farm requests are completed. Recipe files may also utilize Jinja2 templates in order to adjust configuration with event specific data.

Recipe configuration file enables users to describe a complex test matrix. This is achieved by using a set of parameters passed to each Testing Farm requests and parameterized tmt plans enabling runtime adjustments.

A recipe file configuration is split into two sections. The first section is named `fixtures` and contains configuration that is relevant to all test jobs triggered by the recipe file.

The second section is named` dimensions` and it outlines how the test matrix looks like. Each dimension is identified by its name and defines a list of possible values, each value representing  a configuration snippet that would be used for the respective test job. `newa` does a Cartesian product of defined dimensions, building all possible combinations.

Example:
Using the recipe file
```
fixtures:
    environment:
        PLANET: Earth
dimensions:
    states:
        - environment:
              STATE: USA
        - environment:
              STATE: India
    cities:
        - environment:
              CITY: Salem
        - environment:
              CITY: Delhi
```
`newa` will generate the following combinations:
```
PLANET=Earth, STATE=USA, CITY=Salem
PLANET=Earth, STATE=India, CITY=Salem
PLANET=Earth, STATE=USA, CITY=Delhi
PLANET=Earth, STATE=India, CITY=Delhi
```

Individual dimension values may also contain additional keys like `context`, `reportportal` etc. Individual options are described below.

#### environment

Defines environment varibles to use. See the example above.

#### context

Defines custom `tmt` context setting that will be passed to TestingFarm / `tmt`.

Example:
```
  context:
    swtpm: yes
```

#### tmt

Identifies test plans that should be executed. Possible parameters are:
 - `url`: URL of a repository with `tmt` plans.
 - `ref`: Git repo `ref` within a repository.
 - `path`: Path to `tmt` root within a repository.
 - `plan`: Identifies `tmt` test plans to execute, a regexp used to filter plans by name.

#### testingfarm

May define additional options passed to the `testing-farm request ...` command.
The only possible option is:
 - `cli_args`: String containing extra CLI options.

Example:
```
  testingfarm:
    cli_args: "--pipeline-type tmt-multihost"
```

#### reportportal

Contains ReportPortal launch and suite related settings. Possible parameters are:
 - `launch_name`: RP launch name to use.
 - `launch_description`: RP launch description.
 - `suite_description`: RP suite description.
 - `launch_attributes`: RP launch attributes (tags) to set for a given launch (and suite). In addition to this attributes, `tmt` contexts used for a particular `tmt` plan will be set as attributes of the respective RP suite.

Example:
```
  reportportal:
    launch_name: "keylime"
    launch_description: "keylime_server system role interoperability"
    suite_description: "Testing keylime_server role on {{ ENVIRONMENT.COMPOSE_CONTROLLER }} against keylime on {{ ENVIRONMENT.COMPOSE_KEYLIME }}"
    launch_attributes:
      tier: 1
      trigger: build
```

#### when
A condition that restricts when an item should be used. See "In-config tests" section for examples.

Example:
```
dimensions:
    versions:
      - environment:
            COMPOSE_VERIFIER: "{{ COMPOSE.id }}"
            COMPOSE_REGISTRAR: "{{ COMPOSE.id }}"
            COMPOSE_AGENT: "{{ COMPOSE.id }}"
            COMPOSE_AGENT2: RHEL-9.5.0-Nightly
        when: 'COMPOSE.id is not match("RHEL-9.5.0-Nightly")'
```

### In-config tests

Both NEWA issue-config and recipe files may contain Jinja templates that enable user to parametrize files with details obtain from the event.

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

## Quick demo

Make sure you have your `$HOME/.newa` configuration file defined prior running this file.

```
$ REQUESTS_CA_BUNDLE=/etc/pki/tls/cert.pem newa event --compose CentOS-Stream-9 jira --issue-config demodata/jira-compose-config.yaml schedule execute report
```

Or

```
$ REQUESTS_CA_BUNDLE=/etc/pki/tls/cert.pem newa event --erratum 124115 jira --issue-config demodata/jira-errata-config.yaml schedule execute report
```

## NEWA subcommands

### Subcommand `event`

This subcommand is associated with a particular event (like an erratum) and it attempts to read details about it so that this data can be utilized in later parts of the workflow. While we are using erratum as an event example, other event types could be supported in the future (e.g. compose, build, GitLab MR etc.).

`event` subcommands reads event details either from a command line.
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

This subcommand is responsible for interaction with Jira. It reads details previously gathered by the `event` subcommand and identifies Jira issues that should be used for tracking of individual steps of the testing process. These steps are defined in a so-called NEWA issue-config file.

Specifically, it processes multiple files having `event-` prefix. For each event/file it reads
NEWA issue-config and for each item from the configuration it
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

This subcommand does apply only when a particular item from the Jira (issue) configuration file contains a recipe attribute which points to a specific recipe YAML file. Also, it generates all relevant combinations that will be later executed.

Specifically, it processes multiple files having `jira-` prefix. For each such file it
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

This subcommand does the actual execution. It triggers multiple Testing Farm requests in parallel (single request per one generated combination) and waits until these requests are finished and all individual test results are available in ReportPortal.

Specifically, it processes multiple files having `schedule-` prefix. For each such file it
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

This subcommand updates RP launch with recipe status and updates the respective Jira issue with a comment and a link to RP launch containing all test results.

It processes multiple files having `execute-` prefix,
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
