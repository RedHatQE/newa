"""Jira service integration."""

import re
import urllib.parse
from typing import TYPE_CHECKING, Any, ClassVar, Optional, Union

import jira
import jira.client

try:
    from attrs import define, field, frozen
except ModuleNotFoundError:
    from attr import define, field, frozen

from newa.models.events import EventType
from newa.models.issues import Issue, IssueAction, IssueTransitions, IssueType
from newa.utils.helpers import short_sleep

if TYPE_CHECKING:
    from newa.models.jobs import ArtifactJob


@define
class JiraField:
    id_: str
    name: str
    type_: Optional[str]
    items: Optional[str]


@define
class JiraIssueLinkType:
    name: str
    inward: bool


@frozen
class IssueHandler:  # type: ignore[no-untyped-def]
    """An interface to Jira instance handling a specific ArtifactJob."""

    artifact_job: 'ArtifactJob' = field()
    url: str = field()
    token: str = field()
    project: str = field()

    # Each project can have different semantics of issue status.
    transitions: IssueTransitions = field(  # type: ignore[var-annotated]
        converter=lambda x: x if isinstance(x, IssueTransitions) else IssueTransitions(**x))

    # field name=>JiraField mapping will be obtained from Jira later
    # see https://JIRASERVER/rest/api/2/field
    field_map: ClassVar[dict[str, JiraField]] = {}

    board: Optional[Union[str, int]] = field(default=None)
    # Actual Jira connection.
    connection: jira.JIRA = field(init=False)

    # Cache of Jira user names mapped to e-mail addresses.
    user_names: dict[str, str] = field(init=False, default={})

    # NEWA label
    newa_label: ClassVar[str] = "NEWA"

    # active and future sprint ids, will be obtained from Jira later
    sprint_cache: ClassVar[dict[str, list[int]]] = {'active': [], 'future': []}

    group: Optional[str] = field(default=None)

    issue_link_types_map: ClassVar[dict[str, JiraIssueLinkType]] = {}

    @connection.default  # pyright: ignore [reportAttributeAccessIssue]
    def connection_factory(self) -> jira.JIRA:
        conn = jira.JIRA(self.url, token_auth=self.token)
        # try connection first
        try:
            conn.myself()
            short_sleep()
            # read field map from Jira and store its simplified version
            fields = conn.fields()
            for f in fields:
                self.field_map[f['name']] = JiraField(
                    name=f['name'],
                    id_=f['id'],
                    type_=f['schema']['type'] if 'schema' in f else None,
                    items=f['schema']['items']
                    if ('schema' in f and 'items' in f['schema'])
                    else None)
            # read link issue types
            issue_link_types = conn.issue_link_types()
            # self.issue_link_types_map = {}
            for link_type in issue_link_types:
                self.issue_link_types_map[str(link_type.inward)] = JiraIssueLinkType(
                    name=link_type.name, inward=True)
                self.issue_link_types_map[str(link_type.outward)] = JiraIssueLinkType(
                    name=link_type.name, inward=False)
            # read the current and next sprint for the board
            if self.board:
                # if board is identified by name, find its id
                if isinstance(self.board, str):
                    boards = conn.boards(name=self.board)
                    if len(boards) == 1:
                        board_id = boards[0].id
                    else:
                        raise Exception(f"Could not find Jira board with name '{self.board}'")
                    short_sleep()
                else:
                    board_id = self.board
                # fetch both states at once
                sprints = conn.sprints(board_id, state='active,future')
                self.sprint_cache['active'] = [
                    s.id for s in sprints if s.originBoardId == board_id and s.state == 'active']
                self.sprint_cache['future'] = [
                    s.id for s in sprints if s.originBoardId == board_id and s.state == 'future']
                short_sleep()

        except jira.JIRAError as e:
            raise Exception('Could not authenticate to Jira. Wrong token?') from e
        return conn

    def newa_id(self, action: Optional[IssueAction] = None, partial: bool = False) -> str:
        """
        NEWA identifier

        Construct so-called NEWA identifier - it identifies all issues of given
        action for errata. By default it defines issues related to the current
        respin. If 'partial' is defined it defines issues relevant for all respins.
        """

        if not action:
            return f"::: {IssueHandler.newa_label}"

        if action.newa_id:
            return f"::: {IssueHandler.newa_label} {action.newa_id}"
        newa_id = f"::: {IssueHandler.newa_label} {action.id}: {self.artifact_job.id}"
        # for ERRATUM event type update ID with sorted builds
        if (not partial and
            self.artifact_job.event.type_ is EventType.ERRATUM and
                self.artifact_job.erratum):
            newa_id += f" ({', '.join(sorted(self.artifact_job.erratum.builds))}) :::"

        return newa_id

    def get_user_name(self, assignee_email: str) -> str:
        """
        Find Jira user name associated with given e-mail address

        Notice that Jira user name has various forms, it can be either an e-mail
        address or just an user name or even an user name with some sort of prefix.
        It is possible that some e-mail addresses don't have Jira user associated,
        e.g. some mailing lists. In that case empty string is returned.
        """

        if assignee_email not in self.user_names:
            assignee_names = [u.name for u in self.connection.search_users(user=assignee_email)]
            if not assignee_names:
                self.user_names[assignee_email] = ""
            elif len(assignee_names) == 1:
                self.user_names[assignee_email] = assignee_names[0]
            else:
                raise Exception(f"At most one Jira user is expected to match {assignee_email}"
                                f"({', '.join(assignee_names)})!")

        return self.user_names[assignee_email]

    def get_details(self, issue: Issue) -> jira.Issue:
        """Return issue details"""

        try:
            return self.connection.issue(issue.id)
        except jira.JIRAError as e:
            raise Exception(f"Jira issue {issue} not found!") from e

    def get_related_issues(self,
                           action: IssueAction,
                           all_respins: bool = False,
                           closed: bool = False) -> dict[str, dict[str, str]]:
        """
        Get issues related to erratum job with given summary

        Unless 'all_respins' is defined only issues related to the current respin are returned.
        Unless 'closed' is defined, only opened issues are returned.
        Result is a dictionary such that keys are found Jira issue keys (ID) and values
        are dictionaries such that there is always 'description' key and if the issues has
        parent then there is also 'parent' key. For instance:

        {
            "NEWA-123": {
                "description": "description of first issue",
                "parent": "NEWA-456"
                "status": "closed"
            }
            "NEWA-456": {
                "description": "description of second issue"
                "status": "opened"
            }
        }
        """

        fields = ["description", "parent", "status"]

        newa_description = f"{self.newa_id(action, True) if all_respins else self.newa_id(action)}"
        if closed:
            query = \
                f"project = '{self.project}' AND " + \
                f"labels in ({IssueHandler.newa_label}) AND " + \
                f"description ~ '{newa_description}'"
        else:
            query = \
                f"project = '{self.project}' AND " + \
                f"labels in ({IssueHandler.newa_label}) AND " + \
                f"description ~ '{newa_description}' AND " + \
                f"status not in ({','.join(self.transitions.closed)})"
        search_result = self.connection.search_issues(query, fields=fields, json_result=True)
        if not isinstance(search_result, dict):
            raise Exception(f"Unexpected search result type {type(search_result)}!")

        # Transformation of search_result json into simpler structure gets rid of
        # linter warning and also makes easier mocking (for tests).
        # Additionally, double-check that the description matches since Jira tend to mess up
        # searches containing characters like underscore, space etc. and may return extra issues
        result = {}
        for jira_issue in search_result["issues"]:
            if newa_description in jira_issue["fields"]["description"]:
                result[jira_issue["key"]] = {"description": jira_issue["fields"]["description"]}
                if jira_issue["fields"]["status"]["name"] in self.transitions.closed:
                    result[jira_issue["key"]] |= {"status": "closed"}
                else:
                    result[jira_issue["key"]] |= {"status": "opened"}
                if "parent" in jira_issue["fields"]:
                    result[jira_issue["key"]] |= {"parent": jira_issue["fields"]["parent"]["key"]}
        return result

    def create_issue(self,
                     action: IssueAction,
                     summary: str,
                     description: str,
                     use_newa_id: bool = True,
                     assignee_email: Optional[str] = None,
                     parent: Optional[Issue] = None,
                     group: Optional[str] = None,
                     transition_passed: Optional[str] = None,
                     transition_processed: Optional[str] = None,
                     fields: Optional[dict[str, Union[str, float, list[str]]]] = None,
                     links: Optional[dict[str, list[str]]] = None) -> Issue:
        """Create issue"""
        if use_newa_id:
            description = f"{self.newa_id(action)}\n\n{description}"
        data = {
            "project": {"key": self.project},
            "summary": summary,
            "description": description,
            }
        if assignee_email and self.get_user_name(assignee_email):
            data |= {"assignee": {"name": self.get_user_name(assignee_email)}}

        if action.type == IssueType.EPIC:
            data |= {
                "issuetype": {"name": "Epic"},
                IssueHandler.field_map["Epic Name"].id_: data["summary"],
                }
        elif action.type == IssueType.STORY:
            data |= {"issuetype": {"name": "Story"}}
            if parent:
                data |= {IssueHandler.field_map["Epic Link"].id_: parent.id}
        elif action.type == IssueType.TASK:
            data |= {"issuetype": {"name": "Task"}}
            if parent:
                data |= {IssueHandler.field_map["Epic Link"].id_: parent.id}
        elif action.type == IssueType.SUBTASK:
            if not parent:
                raise Exception("Missing task while creating sub-task!")

            data |= {
                "issuetype": {"name": "Sub-task"},
                "parent": {"key": parent.id},
                }
        else:
            raise Exception(f"Unknown issue type {action.type}!")

        # handle fields['Reporter'] already during ticket creation
        if fields and 'Reporter' in fields and isinstance(fields['Reporter'], str):
            data |= {"reporter": {"name": self.get_user_name(fields['Reporter'])}}

        try:
            jira_issue = self.connection.create_issue(data)
        except jira.JIRAError as e:
            # Sometimes Jira may return error 401 while actually creating the ticket
            msg = str(e)
            r = re.search(
                r"jira.exceptions.JIRAError: JiraError HTTP 401 url: https://[^\n]+"
                f"({self.project}-[0-9]+)", msg)
            if r:
                jira_issue = self.connection.issue(r.group(1))
            else:
                raise Exception("Unable to create issue!") from e

        # continue processing new Jira issue eventually
        short_sleep()
        if fields is None:
            fields = {}
        # add NEWA label unless not using newa id
        if use_newa_id:
            if "Labels" in fields and isinstance(fields['Labels'], list):
                fields['Labels'].append(IssueHandler.newa_label)
            else:
                fields['Labels'] = [IssueHandler.newa_label]
        # populate fdata with configuration provided by the user
        fdata: dict[str, Union[str, float, list[Any], dict[str, Any]]] = {}
        transition_name: Optional[str] = None
        for field in fields:

            # skip Reporter field as that one was processed previously
            if field == 'Reporter':
                continue

            if field not in IssueHandler.field_map:
                raise Exception(f"Could not find field '{field}' in Jira.")
            field_id = IssueHandler.field_map[field].id_
            field_type = IssueHandler.field_map[field].type_
            field_items = IssueHandler.field_map[field].items
            value = fields[field]
            # to ease processing set field_values to be always a list of strings
            if isinstance(value, (float, int, str)):
                field_values = [str(value)]
            elif isinstance(value, list):
                field_values = list(map(str, value))
            else:
                raise Exception(
                    f'Unsupported Jira field conversion for {type(value).__name__}')
            # there is extra handling for Sprint as it should be an integer, maybe
            # wrong custom field definition
            if field == 'Sprint':
                if not value:
                    continue
                if not self.board:
                    raise Exception(
                        "Jira 'board' is not configured in the issue-config file.")
                if value == 'active':
                    sprint_id = self.sprint_cache['active'][0]
                elif value == 'future':
                    sprint_id = self.sprint_cache['future'][0]
                elif isinstance(value, (int, str)):
                    sprint_id = int(value)
                else:
                    raise Exception(
                        f"Invalid 'Sprint' value '{value}', "
                        "should be 'active', 'future' or sprintID")
                fdata[field_id] = sprint_id
            # now we need to distinguish different types of fields and values
            elif field_type == 'string':
                fdata[field_id] = field_values[0]
            elif field_type == 'number':
                fdata[field_id] = float(field_values[0])
            elif field_type == 'option':
                fdata[field_id] = {"value": field_values[0]}
            elif field_type == 'array':
                if field_items == 'string':
                    fdata[field_id] = field_values
                elif field_items == 'option':
                    fdata[field_id] = [{"value": v} for v in field_values]
                elif field_items in ['component', 'version']:
                    fdata[field_id] = [{"name": v} for v in field_values]
                else:
                    raise Exception(f'Unsupported Jira field item "{field_items}"')
            elif field_type == 'priority':
                fdata[field_id] = {"name": field_values[0]}
            elif field_type == 'status':
                transition_name = field_values[0]
            else:
                raise Exception(f'Unsupported Jira field type "{field_type}"')

        try:
            jira_issue.update(fields=fdata)
            short_sleep()

            # add links
            if links:
                for relation in links:
                    issue_link_type = self.issue_link_types_map.get(relation, None)
                    if issue_link_type:
                        for linked_key in links[relation]:
                            # create issue link
                            # might raise false warning, see
                            # https://github.com/pycontribs/jira/issues/1875
                            if issue_link_type.inward:
                                self.connection.create_issue_link(
                                    issue_link_type.name, linked_key, jira_issue.key)
                            else:
                                self.connection.create_issue_link(
                                    issue_link_type.name, jira_issue.key, linked_key)
                            short_sleep()
                    else:
                        raise Exception(f'Unknown issue link type "{relation}"')

            if transition_name:
                self.connection.transition_issue(jira_issue.key, transition=transition_name)
                short_sleep()
            return Issue(jira_issue.key,
                         group=self.group,
                         summary=summary,
                         url=urllib.parse.urljoin(self.url, f'/browse/{jira_issue.key}'),
                         transition_passed=transition_passed,
                         transition_processed=transition_processed,
                         action_id=action.id)
        except jira.JIRAError as e:
            raise Exception(f"Unable to update issue {jira_issue.key}") from e

    def refresh_issue(self, action: IssueAction, issue: Issue) -> bool:
        """Update NEWA identifier of issue.
            Returns True when the issue had been 'adopted' by NEWA."""

        issue_details = self.get_details(issue)
        description = issue_details.fields.description
        labels = issue_details.fields.labels
        new_description = ""
        return_value = False

        # add NEWA label if missing
        if self.newa_label not in labels:
            issue_details.add_field_value('labels', self.newa_label)
            return_value = True

        # Issue does not have any NEWA ID yet
        if isinstance(description, str) and self.newa_id() not in description:
            new_description = f"{self.newa_id(action)}\n{description}"
            return_value = True

        # Issue has NEWA ID but not the current respin - update it.
        elif isinstance(description, str) and self.newa_id(action) not in description:
            new_description = re.sub(f"^{re.escape(self.newa_id())}.*\n",
                                     f"{self.newa_id(action)}\n", description)
            return_value = True

        if new_description:
            try:
                self.get_details(issue).update(fields={"description": new_description})
                short_sleep()
                self.comment_issue(
                    issue, f"NEWA ID has been updated to:\n{self.newa_id(action)}")
                short_sleep()
            except jira.JIRAError as e:
                raise Exception(f"Unable to modify issue {issue}!") from e
        return return_value

    def comment_issue(self, issue: Issue, comment: str) -> None:
        """Add comment to issue"""

        try:
            self.connection.add_comment(
                issue.id, comment, visibility={
                    'type': 'group', 'value': self.group} if self.group else None)
        except jira.JIRAError as e:
            raise Exception(f"Unable to add a comment to issue {issue}!") from e

    def drop_obsoleted_issue(self, issue: Issue, obsoleted_by: Issue) -> None:
        """Close obsoleted issue and link obsoleting issue to the obsoleted one"""

        obsoleting_comment = f"NEWA dropped this issue (obsoleted by {obsoleted_by})."
        try:
            self.connection.create_issue_link(
                type="relates to",
                inwardIssue=issue.id,
                outwardIssue=obsoleted_by.id,
                comment={
                    "body": obsoleting_comment,
                    "visibility": {
                        'type': 'group',
                        'value': self.group} if self.group else None,
                    })
            # if the transition has a format status.resolution close with resolution
            short_sleep()
            if '.' in self.transitions.dropped[0]:
                status, resolution = self.transitions.dropped[0].split('.', 1)
                self.connection.transition_issue(issue.id,
                                                 transition=status,
                                                 resolution={'name': resolution})
            # otherwise close just using the status
            else:
                self.connection.transition_issue(issue.id,
                                                 transition=self.transitions.dropped[0])
        except jira.JIRAError as e:
            raise Exception(f"Cannot close issue {issue}!") from e
