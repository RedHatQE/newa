"""Color configuration management for NEWA CLI."""

import logging
from pathlib import Path
from typing import Optional

from newa.utils.yaml_utils import yaml_parser


class ColorConfig:
    """Color configuration loaded from YAML file."""

    def __init__(self, config_dict: Optional[dict[str, dict[str, str]]] = None):
        """
        Initialize color configuration.

        Args:
            config_dict: Dictionary containing color configuration
        """
        self.config = config_dict or {}

    def get_color(self, category: str, key: str) -> Optional[str]:
        """
        Get color code for a specific category and key.

        Args:
            category: Category name (e.g., 'palette', 'states', 'results')
            key: Key within category (e.g., 'state_dir', 'running', 'passed')

        Returns:
            Color code string if defined, None otherwise
        """
        if category not in self.config:
            return None

        category_config = self.config[category]
        if not isinstance(category_config, dict):
            return None

        return category_config.get(key)

    @classmethod
    def load_from_file(cls, filepath: Path) -> 'ColorConfig':
        """
        Load color configuration from YAML file.

        Args:
            filepath: Path to the color configuration file

        Returns:
            ColorConfig instance
        """
        logger = logging.getLogger(__name__)

        if not filepath.exists():
            return cls()

        try:
            with open(filepath) as f:
                config_dict = yaml_parser().load(f)
                return cls(config_dict or {})
        except Exception as e:
            # If file is malformed, log warning and return empty config (use defaults)
            logger.warning(
                f'Failed to load color configuration from {filepath}: {e}. '
                f'Using default colors.')
            return cls()
