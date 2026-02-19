"""Jira connection management."""

from typing import Optional, Union

import jira
import jira.client
import requests

try:
    from attrs import define, field
except ModuleNotFoundError:
    from attr import define, field

from newa.utils.helpers import short_sleep


@define
class JiraField:
    """Represents a Jira field definition."""
    id_: str
    name: str
    type_: Optional[str]
    items: Optional[str]


@define
class JiraIssueLinkType:
    """Represents a Jira issue link type."""
    name: str
    inward: bool


@define
class JiraConnection:
    """
    Manages a single Jira connection instance with lazy initialization.

    This class handles the connection to Jira. Metadata such as field mappings,
    issue link types, and sprint cache are loaded on-demand only when needed
    (e.g., during issue-config processing).

    Supports both Jira Server (token authentication) and Jira Cloud (email + API token).
    Authentication method is auto-detected based on the URL.
    """

    url: str = field()
    token: str = field()
    email: Optional[str] = field(default=None)

    # Private connection instance, initialized on first use
    _connection: Optional[jira.JIRA] = field(default=None, init=False, repr=False)

    # Cached metadata - loaded on-demand, not automatically
    field_map: dict[str, JiraField] = field(factory=dict, init=False, repr=False)
    issue_link_types_map: dict[str, JiraIssueLinkType] = field(
        factory=dict, init=False, repr=False)
    sprint_cache: dict[str, list[int]] = field(
        factory=lambda: {'active': [], 'future': []}, init=False, repr=False)

    # Track which board's sprint cache is currently loaded
    _cached_board: Optional[Union[str, int]] = field(default=None, init=False, repr=False)

    # Track whether we're using Jira Cloud
    _is_cloud: Optional[bool] = field(default=None, init=False, repr=False)

    @property
    def is_cloud(self) -> bool:
        """Check if this is a Jira Cloud instance based on URL."""
        if self._is_cloud is None:
            self._is_cloud = 'atlassian.net' in self.url.lower()
        return self._is_cloud

    def get_connection(self) -> jira.JIRA:
        """
        Get or create Jira connection with lazy initialization.

        Auto-detects whether to use Jira Cloud or Server authentication:
        - Cloud (atlassian.net): Uses basic auth with email + API token
        - Server: Uses token auth with Personal Access Token

        Returns:
            jira.JIRA: The initialized Jira connection

        Raises:
            Exception: If authentication fails or connection cannot be established
        """
        if self._connection is None:
            # Configure API version (use v2 for both Cloud and Server)
            options = {'rest_api_version': '2'}

            if self.is_cloud:
                # Jira Cloud requires email + API token for basic auth
                if not self.email:
                    raise Exception(
                        'Jira Cloud (atlassian.net) requires email to be configured. '
                        'Please set jira/email in config file or '
                        'NEWA_JIRA_EMAIL environment variable.')
                self._connection = jira.JIRA(
                    self.url, basic_auth=(self.email, self.token), options=options)
            else:
                # Jira Server uses Personal Access Token
                self._connection = jira.JIRA(self.url, token_auth=self.token, options=options)

            # Verify connection works
            try:
                self._connection.myself()
                short_sleep()
            except jira.JIRAError as e:
                auth_type = 'email + API token' if self.is_cloud else 'Personal Access Token'
                raise Exception(
                    f'Could not authenticate to Jira. Wrong {auth_type}?') from e

        return self._connection

    def ensure_metadata_loaded(self) -> None:
        """
        Ensure field mappings and issue link types are loaded.

        This should be called explicitly when metadata is needed (e.g., during
        issue-config processing). It only loads the data once.
        """
        # Skip if already loaded
        if self.field_map:
            return

        conn = self.get_connection()

        # Read field map from Jira and store its simplified version
        fields = conn.fields()
        for f in fields:
            self.field_map[f['name']] = JiraField(
                name=f['name'],
                id_=f['id'],
                type_=f['schema']['type'] if 'schema' in f else None,
                items=f['schema']['items'] if ('schema' in f and 'items' in f['schema']) else None,
                )

        # Read issue link types
        issue_link_types = conn.issue_link_types()
        for link_type in issue_link_types:
            self.issue_link_types_map[str(link_type.inward)] = JiraIssueLinkType(
                name=link_type.name, inward=True,
                )
            self.issue_link_types_map[str(link_type.outward)] = JiraIssueLinkType(
                name=link_type.name, inward=False,
                )

    def ensure_sprint_cache_loaded(self, board: Optional[Union[str, int]]) -> None:
        """
        Ensure sprint cache is loaded for the specified board.

        Args:
            board: Board name (str) or ID (int) from issue-config

        This should be called explicitly when sprint data is needed. It only
        loads/reloads the data if the board changes.
        """
        if not board:
            return

        # Skip if already loaded for this board (check if cache was loaded, not if sprints exist)
        if self._cached_board == board:
            return

        conn = self.get_connection()

        # If board is identified by name, find its id
        if isinstance(board, str):
            boards = conn.boards(name=board)
            if len(boards) == 1:
                board_id = boards[0].id
            else:
                raise Exception(f"Could not find Jira board with name '{board}'")
            short_sleep()
        else:
            board_id = board

        # Fetch both states at once
        sprints = conn.sprints(board_id, state='active,future')
        self.sprint_cache['active'] = [
            s.id for s in sprints if s.originBoardId == board_id and s.state == 'active'
            ]
        self.sprint_cache['future'] = [
            s.id for s in sprints if s.originBoardId == board_id and s.state == 'future'
            ]
        self._cached_board = board
        short_sleep()

    def search_users_by_email(self, user_email: str) -> list[str]:
        """
        Search for Jira users by email address.

        This method uses the built-in search_users() for Jira Server,
        but uses the requests library for Jira Cloud to support Python 3.9.

        Args:
            user_email: Email address to search for

        Returns:
            List of user identifiers (name for Server, accountId for Cloud)
        """
        if self.is_cloud:
            # Jira Cloud: Use requests library (Python 3.9 compatible)
            if not self.email:
                raise Exception(
                    'Jira Cloud (atlassian.net) requires email to be configured. '
                    'Please set jira/email in config file or '
                    'NEWA_JIRA_EMAIL environment variable.')
            headers = {'Content-Type': 'application/json'}
            auth = (self.email, self.token)
            url = f"{self.url}/rest/api/2/user/search"
            params = {'query': user_email}

            try:
                response = requests.get(url, params=params, headers=headers, auth=auth, timeout=30)
                response.raise_for_status()
                users = response.json()
                short_sleep()
                # Cloud uses accountId
                return [u.get('accountId') for u in users if u.get('accountId')]

            except requests.exceptions.RequestException as e:
                raise Exception(f'Failed to search for user {user_email}: {e!s}') from e
        else:
            # Jira Server: Use built-in method
            conn = self.get_connection()
            users = conn.search_users(user=user_email)
            short_sleep()
            # Server uses name
            return [u.name for u in users]
