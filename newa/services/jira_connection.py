"""Jira connection management."""

from typing import Optional, Union

import jira
import jira.client

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
    """

    url: str = field()
    token: str = field()

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

    def get_connection(self) -> jira.JIRA:
        """
        Get or create Jira connection with lazy initialization.

        Returns:
            jira.JIRA: The initialized Jira connection

        Raises:
            Exception: If authentication fails or connection cannot be established
        """
        if self._connection is None:
            self._connection = jira.JIRA(self.url, token_auth=self.token)

            # Verify connection works
            try:
                self._connection.myself()
                short_sleep()
            except jira.JIRAError as e:
                raise Exception('Could not authenticate to Jira. Wrong token?') from e

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
