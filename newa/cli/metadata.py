"""State directory metadata utilities."""

import logging
from pathlib import Path
from typing import Any, Literal, Optional

from newa.utils.yaml_utils import yaml_parser

METADATA_FILENAME = '.newa-metadata.yaml'

OperationType = Literal['copy', 'extract', 'new', 'update']


class StateMetadata:
    """Manages state directory metadata."""

    def __init__(self, state_dir: Path):
        """Initialize metadata for a state directory."""
        self.state_dir = state_dir
        self.metadata_file = state_dir / METADATA_FILENAME

    def read(self) -> dict[str, Any]:
        """Read metadata from the state directory."""
        if not self.metadata_file.exists():
            return {}
        return yaml_parser().load(self.metadata_file.read_text()) or {}

    def write(self, data: dict[str, Any]) -> None:
        """Write metadata to the state directory."""
        import io
        yaml = yaml_parser()
        stream = io.StringIO()
        yaml.dump(data, stream)
        self.metadata_file.write_text(stream.getvalue())

    def update(self, **kwargs: Any) -> None:
        """Update metadata fields."""
        data = self.read()
        data.update(kwargs)
        self.write(data)

    def get_description(self) -> Optional[str]:
        """Get the description from metadata."""
        return self.read().get('description')

    def set_description(self, description: str) -> None:
        """Set the description in metadata."""
        self.update(description=description)

    def set_parent_state_dir(self, parent_dir: Path) -> None:
        """Set the parent state directory (for copied state dirs)."""
        self.update(parent_state_dir=str(parent_dir))


def setup_state_metadata(
        state_dir: Path,
        description: str = '',
        parent_state_dir: Optional[str] = None,
        operation: Optional[OperationType] = None,
        logger: Optional[logging.Logger] = None) -> None:
    """Setup metadata for a state directory with appropriate defaults.

    Centralizes metadata setup logic to ensure consistent behavior across
    all state-dir operations (new, copy, extract, update).

    Args:
        state_dir: Path to the state directory
        description: User-provided description (optional)
        parent_state_dir: Source path/URL for copied/extracted state-dirs
        operation: Type of operation ('copy', 'extract', 'new', 'update')
        logger: Logger for debug messages
    """
    metadata = StateMetadata(state_dir)

    # Set parent if provided (copy/extract operations)
    if parent_state_dir:
        metadata.update(parent_state_dir=parent_state_dir)

    # Determine final description
    final_description = None
    if description:
        final_description = description
    elif operation == 'copy' and parent_state_dir:
        final_description = f'Copied from {parent_state_dir}'
    elif operation == 'extract' and parent_state_dir:
        final_description = f'Extracted from {parent_state_dir}'

    # Set description if we have one
    if final_description:
        metadata.set_description(final_description)
        if logger:
            logger.debug(f'Description set: "{final_description}"')
