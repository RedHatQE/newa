"""Unit tests for Jira ADF (Atlassian Document Format) conversion."""

import unittest

from newa.services.jira_connection import adf_to_text


class TestADFConverter(unittest.TestCase):
    """Test ADF to text conversion."""

    def test_adf_to_text_empty(self):
        """Test conversion of empty ADF content."""
        assert adf_to_text(None) == ""
        assert adf_to_text("") == ""
        assert adf_to_text({}) == ""

    def test_adf_to_text_plain_string(self):
        """Test conversion when input is already a plain string."""
        text = "This is plain text"
        assert adf_to_text(text) == text

    def test_adf_to_text_simple_paragraph(self):
        """Test conversion of simple ADF paragraph."""
        adf = {
            "type": "doc",
            "version": 1,
            "content": [
                {
                    "type": "paragraph",
                    "content": [
                        {
                            "type": "text",
                            "text": "Hello World",
                            },
                        ],
                    },
                ],
            }
        assert adf_to_text(adf) == "Hello World"

    def test_adf_to_text_multiple_paragraphs(self):
        """Test conversion of multiple ADF paragraphs."""
        adf = {
            "type": "doc",
            "version": 1,
            "content": [
                {
                    "type": "paragraph",
                    "content": [
                        {
                            "type": "text",
                            "text": "First paragraph",
                            },
                        ],
                    },
                {
                    "type": "paragraph",
                    "content": [
                        {
                            "type": "text",
                            "text": "Second paragraph",
                            },
                        ],
                    },
                ],
            }
        result = adf_to_text(adf)
        assert "First paragraph" in result
        assert "Second paragraph" in result

    def test_adf_to_text_with_formatting(self):
        """Test conversion of ADF with text formatting."""
        adf = {
            "type": "doc",
            "version": 1,
            "content": [
                {
                    "type": "paragraph",
                    "content": [
                        {
                            "type": "text",
                            "text": "Bold text",
                            "marks": [{"type": "strong"}],
                            },
                        {
                            "type": "text",
                            "text": " and ",
                            },
                        {
                            "type": "text",
                            "text": "italic text",
                            "marks": [{"type": "em"}],
                            },
                        ],
                    },
                ],
            }
        result = adf_to_text(adf)
        assert "Bold text" in result
        assert "and" in result
        assert "italic text" in result

    def test_adf_to_text_newa_id(self):
        """Test conversion of ADF containing NEWA ID."""
        # This simulates a real-world scenario where NEWA ID is in the description
        adf = {
            "type": "doc",
            "version": 1,
            "content": [
                {
                    "type": "paragraph",
                    "content": [
                        {
                            "type": "text",
                            "text": "::: NEWA test-action-123: job-456 :::",
                            },
                        ],
                    },
                {
                    "type": "paragraph",
                    "content": [
                        {
                            "type": "text",
                            "text": "This is the actual issue description.",
                            },
                        ],
                    },
                ],
            }
        result = adf_to_text(adf)
        assert "::: NEWA test-action-123: job-456 :::" in result
        assert "This is the actual issue description." in result

    def test_adf_to_text_code_block(self):
        """Test conversion of ADF with code block."""
        adf = {
            "type": "doc",
            "version": 1,
            "content": [
                {
                    "type": "codeBlock",
                    "attrs": {"language": "python"},
                    "content": [
                        {
                            "type": "text",
                            "text": "def hello():\n    print('Hello')",
                            },
                        ],
                    },
                ],
            }
        result = adf_to_text(adf)
        assert "def hello():" in result
        assert "print('Hello')" in result

    def test_adf_to_text_heading(self):
        """Test conversion of ADF with heading."""
        adf = {
            "type": "doc",
            "version": 1,
            "content": [
                {
                    "type": "heading",
                    "attrs": {"level": 1},
                    "content": [
                        {
                            "type": "text",
                            "text": "Main Title",
                            },
                        ],
                    },
                {
                    "type": "paragraph",
                    "content": [
                        {
                            "type": "text",
                            "text": "Content text",
                            },
                        ],
                    },
                ],
            }
        result = adf_to_text(adf)
        assert "Main Title" in result
        assert "Content text" in result


if __name__ == '__main__':
    unittest.main()
