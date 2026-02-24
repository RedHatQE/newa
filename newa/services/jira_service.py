"""Jira service integration."""

import logging
import re
import urllib.parse
from typing import TYPE_CHECKING, Any, Optional, Union

import jira
import jira.client

try:
    from attrs import field, frozen
except ModuleNotFoundError:
    from attr import field, frozen

from newa.models.events import EventType
from newa.models.issues import Issue, IssueAction, IssueTransitions, IssueType
from newa.services.jira_connection import JiraConnection, JiraField, JiraIssueLinkType
from newa.utils.helpers import short_sleep

if TYPE_CHECKING:
    from newa.models.jobs import ArtifactJob


@frozen
class IssueHandler:  # type: ignore[no-untyped-def]
    """An interface to Jira instance handling a specific ArtifactJob."""

    artifact_job: 'ArtifactJob' = field()
    jira_connection: JiraConnection = field()
    project: str = field()

    # Each project can have different semantics of issue status.
    transitions: IssueTransitions = field(  # type: ignore[var-annotated]
        converter=lambda x: x if isinstance(x, IssueTransitions) else IssueTransitions(**x))

    # Cache of Jira user names mapped to e-mail addresses.
    user_names: dict[str, str] = field(init=False, default={})

    # NEWA label
    newa_label: str = "NEWA"

    group: Optional[str] = field(default=None)
    board: Optional[Union[str, int]] = field(default=None)
    logger: Optional["logging.Logger"] = field(default=None)

    @property
    def connection(self) -> jira.JIRA:
        """Get the underlying Jira connection."""
        return self.jira_connection.get_connection()

    @property
    def field_map(self) -> dict[str, JiraField]:
        """Get the field map from the Jira connection."""
        return self.jira_connection.field_map

    @property
    def issue_link_types_map(self) -> dict[str, JiraIssueLinkType]:
        """Get the issue link types map from the Jira connection."""
        return self.jira_connection.issue_link_types_map

    @property
    def sprint_cache(self) -> dict[str, list[int]]:
        """Get the sprint cache from the Jira connection."""
        return self.jira_connection.sprint_cache

    def newa_id(self, action: Optional[IssueAction] = None, partial: bool = False) -> str:
        """
        NEWA identifier

        Construct so-called NEWA identifier - it identifies all issues of given
        action for errata. By default it defines issues related to the current
        respin. If 'partial' is defined it defines issues relevant for all respins.
        """

        if not action:
            return f"::: {self.newa_label}"

        if action.newa_id:
            return f"::: {self.newa_label} {action.newa_id}"
        newa_id = f"::: {self.newa_label} {action.id}: {self.artifact_job.id}"
        # for ERRATUM event type update ID with sorted builds
        if (not partial and
            self.artifact_job.event.type_ is EventType.ERRATUM and
                self.artifact_job.erratum):
            newa_id += f" ({', '.join(sorted(self.artifact_job.erratum.builds))}) :::"

        return newa_id

    def _format_for_jira(self, text: str) -> str:
        """
        Format text for Jira Cloud or Server.

        For Jira Cloud, wraps text in {{...}} (inline code) to prevent mention parsing.
        For Jira Server, returns text as-is.
        """
        if self.jira_connection.is_cloud:
            return f"{{{{{text}}}}}"
        return text

    def get_user_name(self, assignee_email: str) -> str:
        """
        Find Jira user identifier associated with given e-mail address.

        For Jira Server, returns the user 'name'.
        For Jira Cloud, returns the user 'accountId'.

        Notice that Jira user name has various forms, it can be either an e-mail
        address or just an user name or even an user name with some sort of prefix.
        It is possible that some e-mail addresses don't have Jira user associated,
        e.g. some mailing lists. In that case empty string is returned.
        """

        if assignee_email not in self.user_names:
            assignee_ids = self.jira_connection.search_users_by_email(assignee_email)
            if not assignee_ids:
                self.user_names[assignee_email] = ""
            elif len(assignee_ids) == 1:
                self.user_names[assignee_email] = assignee_ids[0]
            else:
                raise Exception(f"At most one Jira user is expected to match {assignee_email}"
                                f"({', '.join(assignee_ids)})!")

        return self.user_names[assignee_email]

    def _get_user_field_name(self) -> str:
        """
        Get the correct field name for user assignment.

        Returns 'accountId' for Jira Cloud, 'name' for Jira Server.
        """
        return 'accountId' if self.jira_connection.is_cloud else 'name'

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
                f"labels in ({self.newa_label}) AND " + \
                f"description ~ '{newa_description}'"
        else:
            query = \
                f"project = '{self.project}' AND " + \
                f"labels in ({self.newa_label}) AND " + \
                f"description ~ '{newa_description}' AND " + \
                f"status not in ({','.join(self.transitions.closed)})"

        # Use v3 API for Jira Cloud (which returns ADF format), v2 for Server
        if self.jira_connection.is_cloud:
            search_result = self.jira_connection.search_issues_v3(
                jql=query,
                fields=fields,
                max_results=100,  # Increase limit for better results
                )
        else:
            search_result = self.connection.search_issues(query, fields=fields, json_result=True)

        if not isinstance(search_result, dict):
            raise Exception(f"Unexpected search result type {type(search_result)}!")

        # Transformation of search_result json into simpler structure gets rid of
        # linter warning and also makes easier mocking (for tests).
        # Additionally, double-check that the description matches since Jira tend to mess up
        # searches containing characters like underscore, space etc. and may return extra issues
        result = {}
        for jira_issue in search_result["issues"]:
            # Handle both string descriptions (from v3 after ADF conversion or v2)
            # and None values
            description = jira_issue["fields"].get("description") or ""
            if newa_description in description:
                result[jira_issue["key"]] = {"description": description}
                if jira_issue["fields"]["status"]["name"] in self.transitions.closed:
                    result[jira_issue["key"]] |= {"status": "closed"}
                else:
                    result[jira_issue["key"]] |= {"status": "opened"}
                if "parent" in jira_issue["fields"]:
                    result[jira_issue["key"]] |= {"parent": jira_issue["fields"]["parent"]["key"]}
        return result

    def _process_fields_for_jira(
            self,
            fields: dict[str, Union[str, float, list[str]]],
            for_update: bool = False) -> tuple[
                dict[str, Any], dict[str, list[dict[str, Any]]], Optional[str]]:
        """
        Process custom fields for Jira issue creation or update.

        Handles field value conversion and type-specific formatting for Jira API.
        For updates, array fields are returned separately to use 'add' operations.
        User fields are automatically converted from email addresses to proper
        user identifiers (name for Server, accountId for Cloud).

        Args:
            fields: Dictionary of field names to values (emails for user fields)
            for_update: If True, array fields use 'add' operations for extending;
                       If False, array fields are set directly (for creation)

        Returns:
            Tuple of (fields_data, update_data, transition_name):
            - fields_data: Dict for 'fields' parameter (single-value fields,
              or all fields when creating)
            - update_data: Dict for 'update' parameter (only populated when
              for_update=True, for array fields)
            - transition_name: Transition name if status field is provided,
              None otherwise

        Raises:
            Exception: If field is not found, has unsupported type, or Sprint
              configuration is invalid
        """
        fields_data: dict[str, Union[str, float, list[Any], dict[str, Any]]] = {}
        update_data: dict[str, list[dict[str, Any]]] = {}
        transition_name: Optional[str] = None

        for field_name, value in fields.items():
            # Skip Reporter field during updates as it cannot be changed after creation
            if for_update and field_name == 'Reporter':
                if self.logger:
                    self.logger.debug("Skipping Reporter field (cannot be updated after creation)")
                continue

            if field_name not in self.field_map:
                raise Exception(f"Could not find field '{field_name}' in Jira.")

            field_id = self.field_map[field_name].id_
            field_type = self.field_map[field_name].type_
            field_items = self.field_map[field_name].items

            # Normalize value to list of strings for uniform processing
            if isinstance(value, (float, int, str)):
                field_values = [str(value)]
            elif isinstance(value, list):
                field_values = list(map(str, value))
            else:
                raise Exception(
                    f'Unsupported Jira field conversion for {type(value).__name__}')

            # Special handling for Sprint field
            if field_name == 'Sprint':
                if not value:
                    continue
                # Ensure sprint cache is loaded lazily when needed
                if self.board:
                    self.jira_connection.ensure_sprint_cache_loaded(self.board)
                # Check if board is configured
                if not self.board:
                    raise Exception(
                        "Jira 'board' is not configured in the issue-config file.")
                # Check if sprints are available
                if not self.sprint_cache['active'] and not self.sprint_cache['future']:
                    raise Exception(
                        f"No active or future sprints found on board '{self.board}'.")
                if value == 'active':
                    if not self.sprint_cache['active']:
                        raise Exception(
                            f"No active sprints found on board '{self.board}'.")
                    sprint_id = self.sprint_cache['active'][0]
                elif value == 'future':
                    if not self.sprint_cache['future']:
                        raise Exception(
                            f"No future sprints found on board '{self.board}'.")
                    sprint_id = self.sprint_cache['future'][0]
                elif isinstance(value, (int, str)):
                    sprint_id = int(value)
                else:
                    raise Exception(
                        f"Invalid 'Sprint' value '{value}', "
                        "should be 'active', 'future' or sprintID")
                fields_data[field_id] = sprint_id

            # Handle different field types
            elif field_type == 'string':
                fields_data[field_id] = field_values[0]

            elif field_type == 'number':
                fields_data[field_id] = float(field_values[0])

            elif field_type == 'option':
                fields_data[field_id] = {"value": field_values[0]}

            elif field_type == 'user':
                # Single user field - convert email to user identifier
                user_field_name = self._get_user_field_name()
                user_id = self.get_user_name(field_values[0])
                if user_id:
                    fields_data[field_id] = {user_field_name: user_id}

            elif field_type == 'array':
                # For updates, use 'add' operations to extend; for creation, set directly
                if for_update:
                    if field_items == 'string':
                        update_data[field_id] = [{"add": v} for v in field_values]
                    elif field_items == 'option':
                        update_data[field_id] = [{"add": {"value": v}} for v in field_values]
                    elif field_items in ['component', 'version']:
                        update_data[field_id] = [{"add": {"name": v}} for v in field_values]
                    elif field_items == 'user':
                        # Array of users - convert each email to user identifier
                        user_field_name = self._get_user_field_name()
                        user_update_ops: list[dict[str, dict[str, str]]] = []
                        for email in field_values:
                            user_id = self.get_user_name(email)
                            if user_id:
                                user_update_ops.append({"add": {user_field_name: user_id}})
                        if user_update_ops:
                            update_data[field_id] = user_update_ops
                    else:
                        raise Exception(f'Unsupported Jira field item "{field_items}"')
                else:
                    if field_items == 'string':
                        fields_data[field_id] = field_values
                    elif field_items == 'option':
                        fields_data[field_id] = [{"value": v} for v in field_values]
                    elif field_items in ['component', 'version']:
                        fields_data[field_id] = [{"name": v} for v in field_values]
                    elif field_items == 'user':
                        # Array of users - convert each email to user identifier
                        user_field_name = self._get_user_field_name()
                        user_objects: list[dict[str, str]] = []
                        for email in field_values:
                            user_id = self.get_user_name(email)
                            if user_id:
                                user_objects.append({user_field_name: user_id})
                        if user_objects:
                            fields_data[field_id] = user_objects
                    else:
                        raise Exception(f'Unsupported Jira field item "{field_items}"')

            elif field_type == 'priority':
                fields_data[field_id] = {"name": field_values[0]}

            elif field_type == 'status':
                transition_name = field_values[0]

            else:
                raise Exception(f'Unsupported Jira field type "{field_type}"')

        return fields_data, update_data, transition_name

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
            description = f"{self._format_for_jira(self.newa_id(action))}\n\n{description}"
        data = {
            "project": {"key": self.project},
            "summary": summary,
            "description": description,
            }
        if assignee_email and self.get_user_name(assignee_email):
            user_field = self._get_user_field_name()
            data |= {"assignee": {user_field: self.get_user_name(assignee_email)}}

        if action.type == IssueType.EPIC:
            data |= {
                "issuetype": {"name": "Epic"},
                self.field_map["Epic Name"].id_: data["summary"],
                }
        elif action.type == IssueType.STORY:
            data |= {"issuetype": {"name": "Story"}}
            if parent:
                data |= {self.field_map["Epic Link"].id_: parent.id}
        elif action.type == IssueType.TASK:
            data |= {"issuetype": {"name": "Task"}}
            if parent:
                data |= {self.field_map["Epic Link"].id_: parent.id}
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
            user_field = self._get_user_field_name()
            data |= {"reporter": {user_field: self.get_user_name(fields['Reporter'])}}

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
                fields['Labels'].append(self.newa_label)
            else:
                fields['Labels'] = [self.newa_label]

        # Process custom fields using the helper method
        fdata, _, transition_name = self._process_fields_for_jira(fields, for_update=False)

        try:
            jira_issue.update(fields=fdata)
            short_sleep()

            if transition_name:
                self.connection.transition_issue(jira_issue.key, transition=transition_name)
                short_sleep()

            new_issue = Issue(jira_issue.key,
                              group=self.group,
                              summary=summary,
                              url=urllib.parse.urljoin(self.jira_connection.url,
                                                       f'/browse/{jira_issue.key}'),
                              transition_passed=transition_passed,
                              transition_processed=transition_processed,
                              action_id=action.id)

            # Add links using the dedicated method
            if links:
                self.add_issue_links(new_issue, links)

            return new_issue
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

        if self.logger:
            self.logger.debug(f"refresh_issue: description type: {type(description)}")
            self.logger.debug(f"refresh_issue: newa_id(): {self.newa_id()}")
            self.logger.debug(f"refresh_issue: newa_id(action): {self.newa_id(action)}")

        # add NEWA label if missing
        if self.newa_label not in labels:
            issue_details.add_field_value('labels', self.newa_label)
            return_value = True
            if self.logger:
                self.logger.debug("refresh_issue: Added NEWA label")

        # Convert None description to empty string
        if description is None:
            description = ""

        # Issue does not have any NEWA ID yet
        if isinstance(description, str) and self.newa_id() not in description:
            new_description = f"{self._format_for_jira(self.newa_id(action))}\n{description}"
            return_value = True
            if self.logger:
                self.logger.debug("refresh_issue: Adding newa_id (no ID present)")

        # Issue has NEWA ID but not the current respin - update it.
        elif isinstance(description, str) and self.newa_id(action) not in description:
            # Strip any existing formatting ({{...}}) from the old NEWA ID before replacing
            pattern = f"^(?:\\{{{{)?{re.escape(self.newa_id())}(?:\\}}}})?.*\n"
            new_description = re.sub(pattern,
                                     f"{self._format_for_jira(self.newa_id(action))}\n",
                                     description)
            return_value = True
            if self.logger:
                self.logger.debug("refresh_issue: Updating newa_id (different respin)")

        if new_description:
            if self.logger:
                self.logger.debug("refresh_issue: Updating description in Jira")
            try:
                self.get_details(issue).update(fields={"description": new_description})
                short_sleep()
                formatted_id = self._format_for_jira(self.newa_id(action))
                self.comment_issue(
                    issue, f"NEWA ID has been updated to:\n{formatted_id}")
                short_sleep()
            except jira.JIRAError as e:
                raise Exception(f"Unable to modify issue {issue}!") from e
        else:
            if self.logger:
                self.logger.debug("refresh_issue: No description update needed")

        return return_value

    def update_issue(self,
                     action: IssueAction,
                     issue: Issue,
                     summary: str,
                     description: str,
                     fields: Optional[dict[str, Union[str, float, list[str]]]] = None) -> bool:
        """Update issue summary, description, and custom fields for respin.

        This method is called when on_respin is set to UPDATE.
        It updates the issue's summary and description (including NEWA ID),
        and optionally updates custom fields. For array/multi-value fields,
        it extends existing values rather than replacing them.

        Only performs updates if the NEWA ID needs to be added or updated.
        If the correct NEWA ID is already present, no updates are made.

        Args:
            action: The IssueAction configuration
            issue: The issue to update
            summary: New summary text
            description: New description text (NEWA ID will be prepended)
            fields: Optional custom fields to update

        Returns:
            True if the NEWA ID was added or updated in the description, False otherwise
        """
        issue_details = self.get_details(issue)
        current_description = issue_details.fields.description or ""

        # Check if NEWA ID is already correct (same logic as refresh_issue)
        if self.newa_id(action) in current_description:
            if self.logger:
                self.logger.info(
                    f"Issue {issue.id} already has correct NEWA ID, skipping update")
            return False

        # NEWA ID needs updating, proceed with full update
        if self.logger:
            self.logger.info(f"Updating issue {issue.id} with new NEWA ID")

        # Prepare update data for single-value fields (uses 'fields' parameter)
        update_fields: dict[str, Any] = {}

        # Update summary if different
        if issue_details.fields.summary != summary:
            update_fields["summary"] = summary
            if self.logger:
                self.logger.debug(f"Updating summary for issue {issue.id}")

        # Update description with NEWA ID
        new_description = f"{self._format_for_jira(self.newa_id(action))}\n\n{description}"
        if current_description != new_description:
            update_fields["description"] = new_description
            if self.logger:
                self.logger.debug(f"Updating description for issue {issue.id}")

        # Process custom fields if provided
        update_operations: dict[str, list[dict[str, Any]]] = {}
        transition_name: Optional[str] = None
        if fields:
            fields_data, update_ops, transition_name = self._process_fields_for_jira(
                fields, for_update=True)
            # Merge the fields_data into update_fields
            update_fields.update(fields_data)
            update_operations = update_ops

            if self.logger and fields_data:
                self.logger.debug(
                    f"Updating {len(fields_data)} custom field(s) for issue {issue.id}")
            if self.logger and update_operations:
                self.logger.debug(
                    f"Extending {len(update_operations)} array field(s) for issue {issue.id}")

        # Apply updates if any
        if update_fields or update_operations:
            try:
                # Use both fields and update parameters for hybrid approach
                if update_operations:
                    issue_details.update(fields=update_fields, update=update_operations)
                else:
                    issue_details.update(fields=update_fields)
                short_sleep()

                if self.logger:
                    self.logger.debug(f"Issue {issue.id} updated successfully")
            except jira.JIRAError as e:
                raise Exception(f"Unable to update issue {issue.id}!") from e

        # Handle status transitions if specified (independent of other field changes)
        if transition_name:
            try:
                self.connection.transition_issue(issue.id, transition=transition_name)
                short_sleep()
                if self.logger:
                    self.logger.debug(
                        f"Applied transition '{transition_name}' to issue {issue.id}")
            except jira.JIRAError as e:
                raise Exception(
                    f"Unable to transition issue {issue.id} to '{transition_name}'!") from e

        return True

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

    def add_issue_links(
            self,
            issue: Issue,
            links: dict[str, list[str]]) -> None:
        """
        Add issue links to a Jira issue.

        Args:
            issue: The issue to add links to
            links: Dictionary mapping link relation types to lists of issue keys

        For each link relation and target issue:
        - Check if link already exists
        - Verify the target issue exists in Jira
        - Create the link if not already present
        """
        if not links:
            return

        # Fetch existing issue links once before the loop
        issue_details = self.get_details(issue)
        existing_links = getattr(issue_details.fields, "issuelinks", []) or []
        short_sleep()

        # Build set of already linked (relation_type, issue_key) tuples for fast lookup
        # This allows the same issue to be linked with different relation types
        existing_linked_pairs = set()
        for link in existing_links:
            link_type_name = link.type.name
            if hasattr(link, 'inwardIssue'):
                existing_linked_pairs.add((link_type_name, link.inwardIssue.key))
            if hasattr(link, 'outwardIssue'):
                existing_linked_pairs.add((link_type_name, link.outwardIssue.key))

        for relation, target_keys in links.items():
            issue_link_type = self.issue_link_types_map.get(relation, None)
            if not issue_link_type:
                raise Exception(f'Unknown issue link type "{relation}"')

            for linked_key in target_keys:
                # Check if link with this specific relation type already exists
                if (issue_link_type.name, linked_key) in existing_linked_pairs:
                    if self.logger:
                        self.logger.debug(
                            f"Issue {issue.id} is already linked to {linked_key} "
                            f"with relation '{relation}'")
                    continue

                # Verify target issue exists before creating link
                try:
                    self.connection.issue(linked_key)
                    short_sleep()
                except jira.JIRAError:
                    if self.logger:
                        self.logger.info(
                            f"Target issue {linked_key} does not exist "
                            "or is not accessible")
                    continue

                # Create the link
                # might raise false warning, see
                # https://github.com/pycontribs/jira/issues/1875
                try:
                    if issue_link_type.inward:
                        self.connection.create_issue_link(
                            issue_link_type.name, linked_key, issue.id)
                    else:
                        self.connection.create_issue_link(
                            issue_link_type.name, issue.id, linked_key)
                    short_sleep()
                    if self.logger:
                        self.logger.debug(
                            f"Linked issue {issue.id} to {linked_key} "
                            f"with relation '{relation}'")
                except jira.JIRAError as e:
                    raise Exception(
                        f"Unable to link issue {issue.id} "
                        f"with issue {linked_key}") from e
