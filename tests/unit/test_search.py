"""Tests for search command."""

import os

from click.testing import CliRunner

from newa import cli


def test_search_basic(tmp_path):
    """Test basic search functionality."""
    runner = CliRunner()

    # Create topdir with state directories
    topdir = tmp_path / "topdir"
    topdir.mkdir()

    # Create first state directory with matching content
    state_dir1 = topdir / "run-001"
    state_dir1.mkdir()
    (state_dir1 / "event-12345-RHEL-9.yaml").write_text("""
event:
  id: '12345'
  type_: erratum
erratum:
  id: '12345'
  content_type: rpm
  respin_count: 1
  summary: keylime security update
  url: https://errata.example.com/12345
  builds:
    - keylime-1.2.3-4.el9
  release: RHEL-9.4.0
compose:
  id: RHEL-9.4.0-Nightly
""")

    # Create second state directory with matching content
    state_dir2 = topdir / "run-002"
    state_dir2.mkdir()
    (state_dir2 / "event-67890-RHEL-9.yaml").write_text("""
event:
  id: '67890'
  type_: erratum
erratum:
  id: '67890'
  content_type: rpm
  respin_count: 1
  summary: keylime agent update
  url: https://errata.example.com/67890
  builds:
    - keylime-agent-2.0.0-1.el9
  release: RHEL-9.5.0
compose:
  id: RHEL-9.5.0-Nightly
""")

    # Create third state directory without matching content
    state_dir3 = topdir / "run-003"
    state_dir3.mkdir()
    (state_dir3 / "event-11111-RHEL-9.yaml").write_text("""
event:
  id: '11111'
  type_: erratum
erratum:
  id: '11111'
  content_type: rpm
  respin_count: 1
  summary: systemd update
  url: https://errata.example.com/11111
  builds:
    - systemd-1.0.0-1.el9
  release: RHEL-9.4.0
compose:
  id: RHEL-9.4.0-Nightly
""")

    # Run search command
    result = runner.invoke(
        cli.main,
        ['search', '--text', 'keylime'],
        env={**os.environ, 'NEWA_STATEDIR_TOPDIR': str(topdir)})

    assert result.exit_code == 0
    # Should find 2 matching state directories
    assert 'Found 2 state directories with matches' in result.output
    # Should show the matching directories
    assert str(state_dir1) in result.output
    assert str(state_dir2) in result.output
    # Should not show the non-matching directory
    assert str(state_dir3) not in result.output


def test_search_case_insensitive(tmp_path):
    """Test that search is case-insensitive."""
    runner = CliRunner()

    # Create topdir with state directory
    topdir = tmp_path / "topdir"
    topdir.mkdir()

    state_dir = topdir / "run-001"
    state_dir.mkdir()
    (state_dir / "event-12345-RHEL-9.yaml").write_text("""
event:
  id: '12345'
  type_: erratum
erratum:
  id: '12345'
  content_type: rpm
  respin_count: 1
  summary: KEYLIME Security Update
  url: https://errata.example.com/12345
  builds: []
  release: RHEL-9.4.0
compose:
  id: RHEL-9.4.0-Nightly
""")

    # Search with lowercase
    result = runner.invoke(
        cli.main,
        ['search', '--text', 'keylime'],
        env={**os.environ, 'NEWA_STATEDIR_TOPDIR': str(topdir)})

    assert result.exit_code == 0
    assert 'Found 1 state directory with matches' in result.output
    assert str(state_dir) in result.output

    # Search with uppercase
    result = runner.invoke(
        cli.main,
        ['search', '--text', 'SECURITY'],
        env={**os.environ, 'NEWA_STATEDIR_TOPDIR': str(topdir)})

    assert result.exit_code == 0
    assert 'Found 1 state directory with matches' in result.output
    assert str(state_dir) in result.output


def test_search_no_matches(tmp_path):
    """Test search with no matching results."""
    runner = CliRunner()

    # Create topdir with state directory
    topdir = tmp_path / "topdir"
    topdir.mkdir()

    state_dir = topdir / "run-001"
    state_dir.mkdir()
    (state_dir / "event-12345-RHEL-9.yaml").write_text("""
event:
  id: '12345'
  type_: erratum
erratum:
  id: '12345'
  content_type: rpm
  respin_count: 1
  summary: systemd update
  url: https://errata.example.com/12345
  builds: []
  release: RHEL-9.4.0
compose:
  id: RHEL-9.4.0-Nightly
""")

    # Search for non-existent text
    result = runner.invoke(
        cli.main,
        ['search', '--text', 'nonexistent-package-xyz'],
        env={**os.environ, 'NEWA_STATEDIR_TOPDIR': str(topdir)})

    assert result.exit_code == 0
    assert 'No matches found for "nonexistent-package-xyz"' in result.output
    assert str(state_dir) not in result.output


def test_search_empty_topdir(tmp_path):
    """Test search with empty topdir."""
    runner = CliRunner()

    # Create empty topdir
    topdir = tmp_path / "topdir"
    topdir.mkdir()

    # Run search
    result = runner.invoke(
        cli.main,
        ['search', '--text', 'keylime'],
        env={**os.environ, 'NEWA_STATEDIR_TOPDIR': str(topdir)})

    assert result.exit_code == 0
    assert 'No matches found for "keylime"' in result.output


def test_search_with_description(tmp_path):
    """Test search output includes state directory descriptions."""
    runner = CliRunner()

    # Create topdir with state directory
    topdir = tmp_path / "topdir"
    topdir.mkdir()

    state_dir = topdir / "run-001"
    state_dir.mkdir()

    # Create event file
    (state_dir / "event-12345-RHEL-9.yaml").write_text("""
event:
  id: '12345'
  type_: erratum
erratum:
  id: '12345'
  content_type: rpm
  respin_count: 1
  summary: keylime update
  url: https://errata.example.com/12345
  builds: []
  release: RHEL-9.4.0
compose:
  id: RHEL-9.4.0-Nightly
""")

    # Create metadata file with description
    (state_dir / ".newa-metadata.yaml").write_text("""
description: Test state directory for keylime
""")

    # Run search
    result = runner.invoke(
        cli.main,
        ['search', '--text', 'keylime'],
        env={**os.environ, 'NEWA_STATEDIR_TOPDIR': str(topdir)})

    assert result.exit_code == 0
    assert str(state_dir) in result.output
    assert '(Test state directory for keylime)' in result.output


def test_search_shows_event_details(tmp_path):
    """Test that search shows event-level details like 'newa list --events'."""
    runner = CliRunner()

    # Create topdir with state directory
    topdir = tmp_path / "topdir"
    topdir.mkdir()

    state_dir = topdir / "run-001"
    state_dir.mkdir()

    # Create event file
    (state_dir / "event-12345-RHEL-9.yaml").write_text("""
event:
  id: '12345'
  type_: erratum
erratum:
  id: '12345'
  content_type: rpm
  respin_count: 1
  summary: keylime security update
  url: https://errata.example.com/12345
  builds:
    - keylime-1.2.3-4.el9
  release: RHEL-9.4.0
compose:
  id: RHEL-9.4.0-Nightly
""")

    # Run search
    result = runner.invoke(
        cli.main,
        ['search', '--text', 'keylime'],
        env={**os.environ, 'NEWA_STATEDIR_TOPDIR': str(topdir)})

    assert result.exit_code == 0
    # Should show event details
    assert 'event E:' in result.output
    assert 'keylime security update' in result.output
    assert 'https://errata.example.com/12345' in result.output


def test_search_multiple_yaml_files(tmp_path):
    """Test search across multiple YAML files in same directory."""
    runner = CliRunner()

    # Create topdir with state directory
    topdir = tmp_path / "topdir"
    topdir.mkdir()

    state_dir = topdir / "run-001"
    state_dir.mkdir()

    # Create event file without search term
    (state_dir / "event-12345-RHEL-9.yaml").write_text("""
event:
  id: '12345'
  type_: erratum
erratum:
  id: '12345'
  content_type: rpm
  respin_count: 1
  summary: systemd update
  url: https://errata.example.com/12345
  builds: []
  release: RHEL-9.4.0
compose:
  id: RHEL-9.4.0-Nightly
""")

    # Create jira file with search term
    (state_dir / "jira-12345-RHEL-9-PROJ-123.yaml").write_text("""
jira:
  id: PROJ-123
  action_id: keylime_test
  summary: Test keylime package
""")

    # Run search
    result = runner.invoke(
        cli.main,
        ['search', '--text', 'keylime'],
        env={**os.environ, 'NEWA_STATEDIR_TOPDIR': str(topdir)})

    assert result.exit_code == 0
    # Should find the state directory (match in jira file)
    assert 'Found 1 state directory with matches' in result.output
    assert str(state_dir) in result.output


def test_search_partial_match(tmp_path):
    """Test search with partial string matching."""
    runner = CliRunner()

    # Create topdir with state directory
    topdir = tmp_path / "topdir"
    topdir.mkdir()

    state_dir = topdir / "run-001"
    state_dir.mkdir()
    (state_dir / "event-12345-RHEL-9.yaml").write_text("""
event:
  id: '12345'
  type_: erratum
erratum:
  id: '12345'
  content_type: rpm
  respin_count: 1
  summary: keylime security update
  url: https://errata.example.com/12345
  builds: []
  release: RHEL-9.4.0
compose:
  id: RHEL-9.4.0-Nightly
""")

    # Search with partial match
    result = runner.invoke(
        cli.main,
        ['search', '--text', 'RHEL-9.4'],
        env={**os.environ, 'NEWA_STATEDIR_TOPDIR': str(topdir)})

    assert result.exit_code == 0
    assert 'Found 1 state directory with matches' in result.output
    assert str(state_dir) in result.output


def test_search_required_text_option():
    """Test that --text option is required."""
    runner = CliRunner()

    # Run search without --text option
    result = runner.invoke(cli.main, ['search'])

    # Should fail with missing option error
    assert result.exit_code != 0
    assert '--text' in result.output or 'required' in result.output.lower()


def test_search_regex_pattern(tmp_path):
    """Test search with regex patterns."""
    runner = CliRunner()

    # Create topdir with state directories
    topdir = tmp_path / "topdir"
    topdir.mkdir()

    # Create state directory with RHEL-9 content
    state_dir1 = topdir / "run-001"
    state_dir1.mkdir()
    (state_dir1 / "event-12345-RHEL-9.yaml").write_text("""
event:
  id: '12345'
  type_: erratum
erratum:
  id: '12345'
  content_type: rpm
  respin_count: 1
  summary: keylime update
  url: https://errata.example.com/12345
  builds: []
  release: RHEL-9.4.0
compose:
  id: RHEL-9.4.0-Nightly
""")

    # Create state directory with RHEL-10 content
    state_dir2 = topdir / "run-002"
    state_dir2.mkdir()
    (state_dir2 / "event-67890-RHEL-10.yaml").write_text("""
event:
  id: '67890'
  type_: erratum
erratum:
  id: '67890'
  content_type: rpm
  respin_count: 1
  summary: keylime update
  url: https://errata.example.com/67890
  builds: []
  release: RHEL-10.2.0
compose:
  id: RHEL-10.2.0-Nightly
""")

    # Create state directory with RHEL-8 content (should not match)
    state_dir3 = topdir / "run-003"
    state_dir3.mkdir()
    (state_dir3 / "event-11111-RHEL-8.yaml").write_text("""
event:
  id: '11111'
  type_: erratum
erratum:
  id: '11111'
  content_type: rpm
  respin_count: 1
  summary: systemd update
  url: https://errata.example.com/11111
  builds: []
  release: RHEL-8.10.0
compose:
  id: RHEL-8.10.0-Nightly
""")

    # Search for RHEL-9 or RHEL-10 using regex
    result = runner.invoke(
        cli.main,
        ['search', '--text', r'RHEL-(9|10)\.'],
        env={**os.environ, 'NEWA_STATEDIR_TOPDIR': str(topdir)})

    assert result.exit_code == 0
    # Should find 2 matching state directories (RHEL-9 and RHEL-10)
    assert 'Found 2 state directories with matches' in result.output
    assert str(state_dir1) in result.output
    assert str(state_dir2) in result.output
    assert str(state_dir3) not in result.output


def test_search_regex_anchors(tmp_path):
    """Test search with regex word boundaries."""
    runner = CliRunner()

    # Create topdir with state directories
    topdir = tmp_path / "topdir"
    topdir.mkdir()

    # Create state directory with erratum 154960
    state_dir1 = topdir / "run-001"
    state_dir1.mkdir()
    (state_dir1 / "event-154960-RHEL-9.yaml").write_text("""
event:
  id: '154960'
  type_: erratum
erratum:
  id: '154960'
  content_type: rpm
  respin_count: 1
  summary: Test erratum
  url: https://errata.example.com/154960
  builds: []
  release: RHEL-9.4.0
compose:
  id: RHEL-9.4.0-Nightly
""")

    # Create state directory with erratum 1549601 (should not match exact ID)
    state_dir2 = topdir / "run-002"
    state_dir2.mkdir()
    (state_dir2 / "event-1549601-RHEL-9.yaml").write_text("""
event:
  id: '1549601'
  type_: erratum
erratum:
  id: '1549601'
  content_type: rpm
  respin_count: 1
  summary: Test erratum
  url: https://errata.example.com/1549601
  builds: []
  release: RHEL-9.4.0
compose:
  id: RHEL-9.4.0-Nightly
""")

    # Search for erratum IDs starting with 154 (should match both)
    result = runner.invoke(
        cli.main,
        ['search', '--text', r"id: '154"],
        env={**os.environ, 'NEWA_STATEDIR_TOPDIR': str(topdir)})

    assert result.exit_code == 0
    assert 'Found 2 state directories with matches' in result.output

    # Search for exact erratum ID 154960 using word boundary
    result = runner.invoke(
        cli.main,
        ['search', '--text', r"'154960'"],
        env={**os.environ, 'NEWA_STATEDIR_TOPDIR': str(topdir)})

    assert result.exit_code == 0
    assert 'Found 1 state directory with matches' in result.output
    assert str(state_dir1) in result.output
    assert str(state_dir2) not in result.output


def test_search_regex_optional_groups(tmp_path):
    """Test search with optional regex groups."""
    runner = CliRunner()

    # Create topdir with state directories
    topdir = tmp_path / "topdir"
    topdir.mkdir()

    # Create state directory with keylime
    state_dir1 = topdir / "run-001"
    state_dir1.mkdir()
    (state_dir1 / "event-1.yaml").write_text("""
event:
  id: '1'
  type_: erratum
erratum:
  id: '1'
  content_type: rpm
  respin_count: 1
  summary: keylime update
  url: https://errata.example.com/1
  builds:
    - keylime-1.2.3-4.el9
  release: RHEL-9.4.0
compose:
  id: RHEL-9.4.0-Nightly
""")

    # Create state directory with keylime-agent-rust
    state_dir2 = topdir / "run-002"
    state_dir2.mkdir()
    (state_dir2 / "event-2.yaml").write_text("""
event:
  id: '2'
  type_: erratum
erratum:
  id: '2'
  content_type: rpm
  respin_count: 1
  summary: keylime-agent-rust update
  url: https://errata.example.com/2
  builds:
    - keylime-agent-rust-2.0.0-1.el9
  release: RHEL-9.4.0
compose:
  id: RHEL-9.4.0-Nightly
""")

    # Create state directory with systemd (should not match)
    state_dir3 = topdir / "run-003"
    state_dir3.mkdir()
    (state_dir3 / "event-3.yaml").write_text("""
event:
  id: '3'
  type_: erratum
erratum:
  id: '3'
  content_type: rpm
  respin_count: 1
  summary: systemd update
  url: https://errata.example.com/3
  builds:
    - systemd-1.0.0-1.el9
  release: RHEL-9.4.0
compose:
  id: RHEL-9.4.0-Nightly
""")

    # Search for keylime with optional agent-rust suffix
    result = runner.invoke(
        cli.main,
        ['search', '--text', r'keylime(-agent-rust)?'],
        env={**os.environ, 'NEWA_STATEDIR_TOPDIR': str(topdir)})

    assert result.exit_code == 0
    # Should find both keylime and keylime-agent-rust
    assert 'Found 2 state directories with matches' in result.output
    assert str(state_dir1) in result.output
    assert str(state_dir2) in result.output
    assert str(state_dir3) not in result.output


def test_search_invalid_regex(tmp_path):
    """Test search with invalid regex pattern."""
    runner = CliRunner()

    # Create topdir
    topdir = tmp_path / "topdir"
    topdir.mkdir()

    # Search with invalid regex pattern
    result = runner.invoke(
        cli.main,
        ['search', '--text', r'RHEL-[9'],  # Unclosed bracket
        env={**os.environ, 'NEWA_STATEDIR_TOPDIR': str(topdir)})

    # Should fail with regex error
    assert result.exit_code != 0
    assert 'Invalid regular expression' in result.output or 'regex' in result.output.lower()
