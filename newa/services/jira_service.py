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

    def _text_to_adf(self, text: str) -> dict[str, Any]:
        """
        Convert plain text to Atlassian Document Format (ADF).

        Jira Cloud requires descriptions to be in ADF format instead of plain text.
        This converts multi-line text into ADF paragraphs.

        Args:
            text: Plain text string (may contain newlines)

        Returns:
            Dictionary representing the ADF document
        """
        # Split text into paragraphs (by double newlines or single newlines)
        paragraphs = text.split('\n')

        content = []
        for para in paragraphs:
            if para.strip():  # Skip empty paragraphs
                content.append({
                    "type": "paragraph",
                    "content": [{
                        "type": "text",
                        "text": para,
                        }],
                    })
            else:
                # Empty line - add empty paragraph for spacing
                content.append({
                    "type": "paragraph",
                    "content": [],
                    })

        return {
            "version": 1,
            "type": "doc",
            "content": content,
            }

    def _convert_description_to_text(self, description_field: Any) -> str:
        """
        Convert description field to plain text.

        Handles multiple formats:
        - Plain text strings (Jira Server)
        - ADF dicts (Jira Cloud search results)
        - PropertyHolder objects (Jira Cloud API responses)

        Args:
            description_field: Description field from Jira API

        Returns:
            Plain text string extracted from the description
        """
        if description_field is None:
            return ""
        if isinstance(description_field, str):
            return description_field
        if isinstance(description_field, dict):
            # Regular dict from search results
            return self._adf_to_text(description_field)
        if hasattr(description_field, 'type'):
            # PropertyHolder object with ADF attributes
            return self._adf_to_text(description_field)
        return str(description_field)

    def _adf_to_text(self, adf: dict[str, Any]) -> str:
        """
        Extract plain text from Atlassian Document Format (ADF).

        Args:
            adf: ADF document structure (dict or PropertyHolder)

        Returns:
            Plain text string extracted from ADF
        """
        text_parts = []

        def extract_text_from_node(node: Any, depth: int = 0) -> None:
            # Handle PropertyHolder objects by accessing attributes directly
            if hasattr(node, 'type') and not isinstance(node, dict):
                # This is a PropertyHolder - access attributes directly
                node_type = getattr(node, 'type', None)
                node_content = getattr(node, 'content', None)
                node_text = getattr(node, 'text', "")
            elif isinstance(node, dict):
                # Regular dict - use .get()
                node_type = node.get("type")
                node_content = node.get("content")
                node_text = node.get("text", "")
            elif isinstance(node, list):
                # List of nodes - recurse into each
                for item in node:
                    extract_text_from_node(item, depth + 1)
                return
            else:
                # Unknown type, skip
                return

            if node_type == "text":
                text_parts.append(node_text)
            elif node_type == "paragraph":
                # Process paragraph content
                if node_content:
                    if isinstance(node_content, list):
                        for child in node_content:
                            extract_text_from_node(child, depth + 1)
                    else:
                        extract_text_from_node(node_content, depth + 1)
                # Add newline after paragraph
                if text_parts and text_parts[-1] != "\n":
                    text_parts.append("\n")
            elif (node_type == "doc" or node_content) and node_content:
                # For doc type or any other type with content, just recurse
                if isinstance(node_content, list):
                    for child in node_content:
                        extract_text_from_node(child, depth + 1)
                else:
                    extract_text_from_node(node_content, depth + 1)

        extract_text_from_node(adf, 0)
        return "".join(text_parts).strip()

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

    def get_user_name(self, assignee_email: str) -> str:
        """
        Find Jira user identifier associated with given e-mail address

        For Jira Cloud, returns the accountId. For Jira Server, returns the username.
        It is possible that some e-mail addresses don't have Jira user associated,
        e.g. some mailing lists. In that case empty string is returned.
        """

        if assignee_email not in self.user_names:
            # For Jira Cloud with GDPR strict mode, use 'query' parameter instead of 'user'
            if self.jira_connection.is_cloud:
                users = self.connection.search_users(query=assignee_email)
                # For Cloud, use accountId instead of name
                assignee_ids = [u.accountId for u in users]
            else:
                users = self.connection.search_users(user=assignee_email)
                # For Server, use name (username)
                assignee_ids = [u.name for u in users]

            if not assignee_ids:
                self.user_names[assignee_email] = ""
            elif len(assignee_ids) == 1:
                self.user_names[assignee_email] = assignee_ids[0]
            else:
                raise Exception(f"At most one Jira user is expected to match {assignee_email}"
                                f"({', '.join(assignee_ids)})!")

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
                f"labels in ({self.newa_label}) AND " + \
                f"description ~ '{newa_description}'"
        else:
            query = \
                f"project = '{self.project}' AND " + \
                f"labels in ({self.newa_label}) AND " + \
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
            description_field = jira_issue["fields"]["description"]

            # For Jira Cloud, description is ADF (dict); for Server, it's plain text (str)
            if isinstance(description_field, dict):
                # Extract text from ADF format
                description_text = self._adf_to_text(description_field)
            else:
                # Plain text for Jira Server
                description_text = description_field or ""

            if newa_description in description_text:
                result[jira_issue["key"]] = {"description": description_text}
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

        Args:
            fields: Dictionary of field names to values
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
            if isinstance(value, float | int | str):
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
                elif isinstance(value, int | str):
                    sprint_id = int(value)
                else:
                    raise Exception(
                        f"Invalid 'Sprint' value '{value}', "
                        "should be 'active', 'future' or sprintID")
                fields_data[field_id] = sprint_id

            # Handle different field types
            elif field_type == 'string':
                # For Jira Cloud, custom string fields often require ADF format
                # System fields like 'summary' are plain text, custom fields need ADF
                if self.jira_connection.is_cloud and field_id.startswith('customfield_'):
                    # Convert custom string fields to ADF for Jira Cloud
                    fields_data[field_id] = self._text_to_adf(field_values[0])
                else:
                    # Plain text for Jira Server or system fields
                    fields_data[field_id] = field_values[0]

            elif field_type == 'number':
                fields_data[field_id] = float(field_values[0])

            elif field_type == 'option':
                fields_data[field_id] = {"value": field_values[0]}

            elif field_type == 'array':
                # For updates, use 'add' operations to extend; for creation, set directly
                if for_update:
                    if field_items == 'string':
                        update_data[field_id] = [{"add": v} for v in field_values]
                    elif field_items == 'option':
                        update_data[field_id] = [{"add": {"value": v}} for v in field_values]
                    elif field_items in ['component', 'version']:
                        update_data[field_id] = [{"add": {"name": v}} for v in field_values]
                    else:
                        raise Exception(f'Unsupported Jira field item "{field_items}"')
                else:
                    if field_items == 'string':
                        fields_data[field_id] = field_values
                    elif field_items == 'option':
                        fields_data[field_id] = [{"value": v} for v in field_values]
                    elif field_items in ['component', 'version']:
                        fields_data[field_id] = [{"name": v} for v in field_values]
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
            description = f"{self.newa_id(action)}\n\n{description}"

        # For Jira Cloud, description must be in ADF format; for Server, use plain text
        description_value = (
            self._text_to_adf(description)
            if self.jira_connection.is_cloud
            else description
            )
        data = {
            "project": {"key": self.project},
            "summary": summary,
            "description": description_value,
            }
        if assignee_email and self.get_user_name(assignee_email):
            # For Jira Cloud, use accountId; for Server, use name
            user_id = self.get_user_name(assignee_email)
            data |= {
                "assignee": {
                    "accountId": user_id} if self.jira_connection.is_cloud else {
                    "name": user_id}}

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
            # For Jira Cloud, use accountId; for Server, use name
            reporter_id = self.get_user_name(fields['Reporter'])
            data |= {
                "reporter": {
                    "accountId": reporter_id} if self.jira_connection.is_cloud else {
                    "name": reporter_id}}

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
        description_field = issue_details.fields.description
        labels = issue_details.fields.labels
        new_description = ""
        return_value = False

        if self.logger:
            self.logger.debug(f"refresh_issue: description type: {type(description_field)}")
            self.logger.debug(f"refresh_issue: newa_id(): {self.newa_id()}")
            self.logger.debug(f"refresh_issue: newa_id(action): {self.newa_id(action)}")

        # add NEWA label if missing
        if self.newa_label not in labels:
            issue_details.add_field_value('labels', self.newa_label)
            return_value = True
            if self.logger:
                self.logger.debug("refresh_issue: Added NEWA label")

        # Convert ADF to text if needed (Jira Cloud returns ADF
        # dict/PropertyHolder, Server returns string)
        description = self._convert_description_to_text(description_field)

        if self.logger:
            self.logger.debug(f"refresh_issue: converted description: {description!r}")

        # Issue does not have any NEWA ID yet
        if self.newa_id() not in description:
            new_description = f"{self.newa_id(action)}\n{description}"
            return_value = True
            if self.logger:
                self.logger.debug("refresh_issue: Adding newa_id (no ID present)")

        # Issue has NEWA ID but not the current respin - update it.
        elif self.newa_id(action) not in description:
            new_description = re.sub(f"^{re.escape(self.newa_id())}.*\n",
                                     f"{self.newa_id(action)}\n", description)
            return_value = True
            if self.logger:
                self.logger.debug("refresh_issue: Updating newa_id (different respin)")

        if new_description:
            if self.logger:
                self.logger.debug("refresh_issue: Updating description in Jira")
            try:
                # Convert description to ADF for Cloud, keep plain text for Server
                description_value = self._text_to_adf(
                    new_description) if self.jira_connection.is_cloud else new_description
                self.get_details(issue).update(fields={"description": description_value})
                short_sleep()
                self.comment_issue(
                    issue, f"NEWA ID has been updated to:\n{self.newa_id(action)}")
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
        current_description_field = issue_details.fields.description

        # Convert ADF to text if needed (Jira Cloud returns ADF dict, Server returns string)
        current_description = self._convert_description_to_text(current_description_field)

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
        new_description = f"{self.newa_id(action)}\n\n{description}"
        if current_description != new_description:
            # Convert description to ADF for Cloud, keep plain text for Server
            update_fields["description"] = self._text_to_adf(
                new_description) if self.jira_connection.is_cloud else new_description
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
                    self.logger.info(f"Issue {issue.id} updated successfully")
            except jira.JIRAError as e:
                raise Exception(f"Unable to update issue {issue.id}!") from e

        # Handle status transitions if specified (independent of other field changes)
        if transition_name:
            try:
                self.connection.transition_issue(issue.id, transition=transition_name)
                short_sleep()
                if self.logger:
                    self.logger.info(
                        f"Applied transition '{transition_name}' to issue {issue.id}")
            except jira.JIRAError as e:
                raise Exception(
                    f"Unable to transition issue {issue.id} to '{transition_name}'!") from e

        return True

    def comment_issue(self, issue: Issue, comment: str) -> None:
        """Add comment to issue"""

        try:
            # For Jira Cloud, comment body must be in ADF format; for Server, use plain text
            comment_body = self._text_to_adf(comment) if self.jira_connection.is_cloud else comment

            self.connection.add_comment(
                issue.id, comment_body, visibility={
                    'type': 'group', 'value': self.group} if self.group else None)
        except jira.JIRAError as e:
            raise Exception(f"Unable to add a comment to issue {issue}!") from e

    def drop_obsoleted_issue(self, issue: Issue, obsoleted_by: Issue) -> None:
        """Close obsoleted issue and link obsoleting issue to the obsoleted one"""

        obsoleting_comment = f"NEWA dropped this issue (obsoleted by {obsoleted_by})."

        # For Jira Cloud, comment body must be in ADF format; for Server, use plain text
        comment_body = self._text_to_adf(
            obsoleting_comment) if self.jira_connection.is_cloud else obsoleting_comment

        try:
            self.connection.create_issue_link(
                type="relates to",
                inwardIssue=issue.id,
                outwardIssue=obsoleted_by.id,
                comment={
                    "body": comment_body,
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
                        self.logger.info(
                            f"Linked issue {issue.id} to {linked_key} "
                            f"with relation '{relation}'")
                except jira.JIRAError as e:
                    raise Exception(
                        f"Unable to link issue {issue.id} "
                        f"with issue {linked_key}") from e
