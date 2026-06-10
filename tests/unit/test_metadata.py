"""Tests for state directory metadata functionality."""

import logging
from pathlib import Path
from unittest import mock

import pytest

from newa.cli.metadata import METADATA_FILENAME, StateMetadata, setup_state_metadata


@pytest.fixture
def temp_state_dir(tmp_path):
    """Create a temporary state directory."""
    state_dir = tmp_path / "run-123"
    state_dir.mkdir()
    return state_dir


def test_metadata_file_creation(temp_state_dir):
    """Test that metadata file is created correctly."""
    metadata = StateMetadata(temp_state_dir)
    assert metadata.metadata_file == temp_state_dir / METADATA_FILENAME
    assert not metadata.metadata_file.exists()

    metadata.set_description("Test description")
    assert metadata.metadata_file.exists()


def test_set_and_get_description(temp_state_dir):
    """Test setting and getting description."""
    metadata = StateMetadata(temp_state_dir)

    # Initially no description
    assert metadata.get_description() is None

    # Set description
    metadata.set_description("My test run")
    assert metadata.get_description() == "My test run"

    # Update description
    metadata.set_description("Updated description")
    assert metadata.get_description() == "Updated description"


def test_set_parent_state_dir(temp_state_dir):
    """Test setting parent state directory."""
    metadata = StateMetadata(temp_state_dir)
    parent_dir = Path("/var/tmp/newa/run-100")

    metadata.set_parent_state_dir(parent_dir)

    data = metadata.read()
    assert data['parent_state_dir'] == str(parent_dir)


def test_metadata_persistence(temp_state_dir):
    """Test that metadata persists across instances."""
    metadata1 = StateMetadata(temp_state_dir)
    metadata1.set_description("Persistent test")
    metadata1.set_parent_state_dir(Path("/tmp/parent"))

    # Create new instance and verify data persists
    metadata2 = StateMetadata(temp_state_dir)
    assert metadata2.get_description() == "Persistent test"
    data = metadata2.read()
    assert data['parent_state_dir'] == "/tmp/parent"


def test_update_multiple_fields(temp_state_dir):
    """Test updating multiple metadata fields at once."""
    metadata = StateMetadata(temp_state_dir)

    metadata.update(
        description="Test run",
        parent_state_dir="/tmp/source",
        custom_field="custom_value",
        )

    data = metadata.read()
    assert data['description'] == "Test run"
    assert data['parent_state_dir'] == "/tmp/source"
    assert data['custom_field'] == "custom_value"


def test_read_nonexistent_metadata(temp_state_dir):
    """Test reading metadata when file doesn't exist."""
    metadata = StateMetadata(temp_state_dir)
    data = metadata.read()
    assert data == {}
    assert metadata.get_description() is None


def test_metadata_yaml_format(temp_state_dir):
    """Test that metadata is stored as valid YAML."""
    metadata = StateMetadata(temp_state_dir)
    metadata.set_description("Test description")
    metadata.set_parent_state_dir(Path("/tmp/parent"))

    # Read the file content directly
    content = metadata.metadata_file.read_text()

    # Verify it contains expected YAML structure
    assert "description:" in content
    assert "parent_state_dir:" in content
    assert "Test description" in content
    assert "/tmp/parent" in content


def test_description_with_special_characters(temp_state_dir):
    """Test description with special characters."""
    metadata = StateMetadata(temp_state_dir)
    special_desc = "Test: with 'quotes' and \"double quotes\" & symbols!"

    metadata.set_description(special_desc)
    assert metadata.get_description() == special_desc


def test_parent_state_dir_with_url(temp_state_dir):
    """Test parent_state_dir with URL (for extracted archives)."""
    metadata = StateMetadata(temp_state_dir)
    url = "https://example.com/archive.tar.gz"

    # For URLs, we pass them directly without Path() wrapper
    metadata.update(parent_state_dir=url)

    data = metadata.read()
    assert data['parent_state_dir'] == url


class TestSetupStateMetadata:
    """Tests for the centralized setup_state_metadata helper."""

    def test_new_operation_with_description(self, temp_state_dir):
        """Test new state-dir with user-provided description."""
        setup_state_metadata(
            temp_state_dir,
            description="New test run",
            operation='new')

        metadata = StateMetadata(temp_state_dir)
        assert metadata.get_description() == "New test run"
        data = metadata.read()
        assert 'parent_state_dir' not in data

    def test_new_operation_without_description(self, temp_state_dir):
        """Test new state-dir without description (no metadata created)."""
        setup_state_metadata(
            temp_state_dir,
            operation='new')

        metadata = StateMetadata(temp_state_dir)
        assert not metadata.metadata_file.exists()

    def test_copy_operation_with_custom_description(self, temp_state_dir):
        """Test copy operation with user-provided description."""
        parent = "/var/tmp/newa/run-100"
        setup_state_metadata(
            temp_state_dir,
            description="Custom copy description",
            parent_state_dir=parent,
            operation='copy')

        metadata = StateMetadata(temp_state_dir)
        assert metadata.get_description() == "Custom copy description"
        data = metadata.read()
        assert data['parent_state_dir'] == parent

    def test_copy_operation_with_default_description(self, temp_state_dir):
        """Test copy operation with auto-generated description."""
        parent = "/var/tmp/newa/run-100"
        setup_state_metadata(
            temp_state_dir,
            parent_state_dir=parent,
            operation='copy')

        metadata = StateMetadata(temp_state_dir)
        assert metadata.get_description() == f"Copied from {parent}"
        data = metadata.read()
        assert data['parent_state_dir'] == parent

    def test_extract_operation_with_custom_description(self, temp_state_dir):
        """Test extract operation with user-provided description."""
        archive_url = "https://example.com/archive.tar.gz"
        setup_state_metadata(
            temp_state_dir,
            description="From Jenkins build",
            parent_state_dir=archive_url,
            operation='extract')

        metadata = StateMetadata(temp_state_dir)
        assert metadata.get_description() == "From Jenkins build"
        data = metadata.read()
        assert data['parent_state_dir'] == archive_url

    def test_extract_operation_with_default_description(self, temp_state_dir):
        """Test extract operation with auto-generated description."""
        archive_url = "https://example.com/archive.tar.gz"
        setup_state_metadata(
            temp_state_dir,
            parent_state_dir=archive_url,
            operation='extract')

        metadata = StateMetadata(temp_state_dir)
        assert metadata.get_description() == f"Extracted from {archive_url}"
        data = metadata.read()
        assert data['parent_state_dir'] == archive_url

    def test_update_operation(self, temp_state_dir):
        """Test update operation to change existing description."""
        # Create initial metadata
        metadata = StateMetadata(temp_state_dir)
        metadata.set_description("Old description")

        # Update with new description
        setup_state_metadata(
            temp_state_dir,
            description="Updated description",
            operation='update')

        assert metadata.get_description() == "Updated description"

    def test_logger_debug_called(self, temp_state_dir):
        """Test that logger.debug is called when description is set."""
        mock_logger = mock.MagicMock(spec=logging.Logger)

        setup_state_metadata(
            temp_state_dir,
            description="Test description",
            operation='new',
            logger=mock_logger)

        mock_logger.debug.assert_called_once_with('Description set: "Test description"')

    def test_no_operation_specified(self, temp_state_dir):
        """Test behavior when operation is not specified."""
        setup_state_metadata(
            temp_state_dir,
            description="Description without operation")

        metadata = StateMetadata(temp_state_dir)
        assert metadata.get_description() == "Description without operation"
