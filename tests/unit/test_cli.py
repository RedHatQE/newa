import os
from pathlib import Path
from unittest import mock

import pytest
from click.testing import CliRunner

import newa
from newa import Settings, cli


@pytest.fixture
def mock_clicontext(tmp_path):
    """ Return a CLIContext object with mocked logger and temp dirpath"""
    return cli.CLIContext(
        logger=mock.MagicMock(),
        settings=Settings(
            et_url='http://dummy.et.url.com',
            ),
        state_dirpath=tmp_path,
        cli_environment={},
        cli_context={})


@pytest.fixture
def _mock_errata_tool(monkeypatch):
    """ Patch methods and functions to avoid communication with ErrataTool """

    def mock_get_request(url: str):
        return {"mock_key": "mock_response"}

    def mock_et_fetch_info(self, id: str):
        """ Return a meaningful json with info """
        return {
            "id": 12345,
            "synopsis": "testing errata",
            "content_types": ["rpm"],
            "people": {
                "assigned_to": "user@domain.com",
                "package_owner": "user2@domain.com",
                "qe_group": "group1@domain.com",
                "devel_group": "group2@domain.com",
                },
            "respin_count": "1",
            "revision": "2",
            }

    def mock_et_fetch_releases(self, id: str):
        """ Return a meaningful json with releases/builds """
        return {
            "RHEL-9.0.0.Z.EUS": [
                {
                    "somepkg-1.2-1.el9_3": {},
                    },
                ],
            "RHEL-9.2.0.Z.EUS": [
                {
                    "somepkg-1.2-1.el9_3": {},
                    },
                ],
            }

    def mock_et_fetch_blocking_errata(self, id: str):
        """ Return empty json for blocking errata """
        return {}

    def mock_et_fetch_system_info(self):
        """ Return dictionary with information about ErrataTool system """
        return {
            "errata_version": "v1.5.3",
            }

    # TODO in the future we might want to do more complex patching of the class
    # methods, but this will suffice for now
    monkeypatch.setenv("NEWA_ET_URL", "https://fake.erratatool.com")
    monkeypatch.setattr(newa, 'get_request', mock_get_request)
    monkeypatch.setattr(newa.ErrataTool, 'fetch_info', mock_et_fetch_info)
    monkeypatch.setattr(newa.ErrataTool, 'fetch_releases', mock_et_fetch_releases)
    monkeypatch.setattr(newa.ErrataTool, 'fetch_blocking_errata', mock_et_fetch_blocking_errata)
    monkeypatch.setattr(newa.ErrataTool, 'fetch_system_info', mock_et_fetch_system_info)


# TODO There's still not much logic to test in cli. These test is just a stub to
# have some tests running. We'll need to update them as we add more functionality

@pytest.mark.usefixtures('_mock_errata_tool')
def test_main_event():
    runner = CliRunner()
    with runner.isolated_filesystem() as temp_dir:
        result = runner.invoke(
            cli.main, ['--state-dir', temp_dir, 'event', '--erratum', '12345'])
        assert result.exit_code == 0
        assert len(list(Path(temp_dir).glob('event-12345*'))) == 2


@pytest.mark.usefixtures('_mock_errata_tool')
def test_event_with_id(mock_clicontext):
    runner = CliRunner()

    # Test that passing an erratum works
    ctx = mock_clicontext
    result = runner.invoke(cli.cmd_event, ['--erratum', '12345'], obj=ctx)
    assert result.exit_code == 0
    # This should have produced 2 event files, one per release (from mock_errata_tool)
    assert len(list(Path(ctx.state_dirpath).glob('event-12345*'))) == 2


@pytest.mark.usefixtures('_mock_errata_tool')
def test_event_no_id(mock_clicontext):
    # Test that not passing erratum loads the default errata config and excepts
    runner = CliRunner()
    ctx = mock_clicontext
    result = runner.invoke(cli.cmd_event, obj=ctx)
    assert result.exception
    assert len(list(Path(ctx.state_dirpath).glob('event-*'))) == 0


def test_copy_state_dir_no_filters(tmp_path):
    """Test copying state directory without any filters."""
    runner = CliRunner()

    # Create source directory with test YAML files
    source_dir = tmp_path / "source"
    source_dir.mkdir()

    # Create sample YAML files
    (source_dir / "jira-issue1.yaml").write_text("""
jira:
  id: PROJ-123
  action_id: action1
""")
    (source_dir / "jira-issue2.yaml").write_text("""
jira:
  id: PROJ-456
  action_id: action2
""")

    # Create a topdir for new state directories
    topdir = tmp_path / "topdir"
    topdir.mkdir()

    # Run copy command without filters
    # --state-dir identifies the source, --copy-state-dir triggers the copy to a new state-dir
    result = runner.invoke(
        cli.main,
        ['--state-dir', str(source_dir), '--copy-state-dir', 'list'],
        env={**os.environ, 'NEWA_STATEDIR_TOPDIR': str(topdir)})

    assert result.exit_code == 0

    # Verify source files still exist (source is read-only)
    assert len(list(source_dir.glob('jira-*.yaml'))) == 2

    # Find the newly created state directory and verify files were copied
    state_dirs = [d for d in topdir.iterdir() if d.is_dir() and d.name.startswith('run-')]
    assert len(state_dirs) == 1
    dest_dir = state_dirs[0]

    # All files should be copied to the new state-dir
    copied_files = list(dest_dir.glob('jira-*.yaml'))
    assert len(copied_files) == 2


def test_copy_state_dir_action_id_filter(tmp_path):
    """Test copying state directory with action-id-filter."""
    runner = CliRunner()

    # Create source directory with test YAML files
    source_dir = tmp_path / "source"
    source_dir.mkdir()

    # Create sample YAML files with different action_ids
    (source_dir / "jira-issue1.yaml").write_text("""
jira:
  id: PROJ-123
  action_id: test_action
""")
    (source_dir / "jira-issue2.yaml").write_text("""
jira:
  id: PROJ-456
  action_id: other_action
""")
    (source_dir / "jira-issue3.yaml").write_text("""
jira:
  id: PROJ-789
  action_id: test_action_2
""")

    # Create a topdir for new state directories
    topdir = tmp_path / "topdir"
    topdir.mkdir()

    # Run copy command with action_id filter
    result = runner.invoke(
        cli.main,
        ['--state-dir', str(source_dir),
         '--copy-state-dir',
         '--action-id-filter', 'test_.*',
         'list'],
        env={**os.environ, 'NEWA_STATEDIR_TOPDIR': str(topdir)})

    assert result.exit_code == 0

    # Find the newly created state directory
    state_dirs = [d for d in topdir.iterdir() if d.is_dir() and d.name.startswith('run-')]
    assert len(state_dirs) == 1
    dest_dir = state_dirs[0]

    # Only files matching action_id pattern should be copied
    copied_files = list(dest_dir.glob('jira-*.yaml'))
    assert len(copied_files) == 2

    # Verify correct files were copied
    from newa.utils.yaml_utils import yaml_parser
    for yaml_file in copied_files:
        yaml_data = yaml_parser().load(yaml_file.read_text())
        action_id = yaml_data.get('jira', {}).get('action_id')
        assert action_id.startswith('test_')


def test_copy_state_dir_issue_id_filter(tmp_path):
    """Test copying state directory with issue-id-filter."""
    runner = CliRunner()

    # Create source directory with test YAML files
    source_dir = tmp_path / "source"
    source_dir.mkdir()

    # Create sample YAML files with different issue IDs
    (source_dir / "jira-issue1.yaml").write_text("""
jira:
  id: PROJ-123
  action_id: action1
""")
    (source_dir / "jira-issue2.yaml").write_text("""
jira:
  id: OTHER-456
  action_id: action2
""")
    (source_dir / "jira-issue3.yaml").write_text("""
jira:
  id: PROJ-789
  action_id: action3
""")

    # Create a topdir for new state directories
    topdir = tmp_path / "topdir"
    topdir.mkdir()

    # Run copy command with issue_id filter
    result = runner.invoke(
        cli.main,
        ['--state-dir', str(source_dir),
         '--copy-state-dir',
         '--issue-id-filter', 'PROJ-.*',
         'list'],
        env={**os.environ, 'NEWA_STATEDIR_TOPDIR': str(topdir)})

    assert result.exit_code == 0

    # Find the newly created state directory
    state_dirs = [d for d in topdir.iterdir() if d.is_dir() and d.name.startswith('run-')]
    assert len(state_dirs) == 1
    dest_dir = state_dirs[0]

    # Only files matching issue_id pattern should be copied
    copied_files = list(dest_dir.glob('jira-*.yaml'))
    assert len(copied_files) == 2

    # Verify correct files were copied
    from newa.utils.yaml_utils import yaml_parser
    for yaml_file in copied_files:
        yaml_data = yaml_parser().load(yaml_file.read_text())
        issue_id = yaml_data.get('jira', {}).get('id')
        assert issue_id.startswith('PROJ-')


def test_copy_state_dir_both_filters(tmp_path):
    """Test copying state directory with both action-id-filter and issue-id-filter."""
    runner = CliRunner()

    # Create source directory with test YAML files
    source_dir = tmp_path / "source"
    source_dir.mkdir()

    # Create sample YAML files with various combinations
    (source_dir / "jira-issue1.yaml").write_text("""
jira:
  id: PROJ-123
  action_id: test_action
""")
    (source_dir / "jira-issue2.yaml").write_text("""
jira:
  id: PROJ-456
  action_id: other_action
""")
    (source_dir / "jira-issue3.yaml").write_text("""
jira:
  id: OTHER-789
  action_id: test_action_2
""")
    (source_dir / "jira-issue4.yaml").write_text("""
jira:
  id: PROJ-999
  action_id: test_action_3
""")

    # Create a topdir for new state directories
    topdir = tmp_path / "topdir"
    topdir.mkdir()

    # Run copy command with both filters
    result = runner.invoke(
        cli.main,
        ['--state-dir', str(source_dir),
         '--copy-state-dir',
         '--action-id-filter', 'test_.*',
         '--issue-id-filter', 'PROJ-.*',
         'list'],
        env={**os.environ, 'NEWA_STATEDIR_TOPDIR': str(topdir)})

    assert result.exit_code == 0

    # Find the newly created state directory
    state_dirs = [d for d in topdir.iterdir() if d.is_dir() and d.name.startswith('run-')]
    assert len(state_dirs) == 1
    dest_dir = state_dirs[0]

    # Only files matching both patterns should be copied
    copied_files = list(dest_dir.glob('jira-*.yaml'))
    assert len(copied_files) == 2

    # Verify correct files were copied
    from newa.utils.yaml_utils import yaml_parser
    for yaml_file in copied_files:
        yaml_data = yaml_parser().load(yaml_file.read_text())
        issue_id = yaml_data.get('jira', {}).get('id')
        action_id = yaml_data.get('jira', {}).get('action_id')
        assert issue_id.startswith('PROJ-')
        assert action_id.startswith('test_')


def test_extract_state_dir_no_filters(tmp_path):
    """Test extracting state directory without any filters."""
    import tarfile
    runner = CliRunner()

    # Create source archive with test YAML files
    source_archive = tmp_path / "source.tar.gz"
    temp_dir = tmp_path / "temp"
    temp_dir.mkdir()

    # Create sample YAML files
    (temp_dir / "jira-issue1.yaml").write_text("""
jira:
  id: PROJ-123
  action_id: action1
""")
    (temp_dir / "jira-issue2.yaml").write_text("""
jira:
  id: PROJ-456
  action_id: action2
""")

    # Create archive
    with tarfile.open(source_archive, 'w:gz') as tar:
        for yaml_file in temp_dir.glob('*.yaml'):
            tar.add(yaml_file, arcname=yaml_file.name)

    # Create destination directory
    dest_dir = tmp_path / "dest"

    # Run extract command without filters
    result = runner.invoke(
        cli.main,
        ['--state-dir', str(dest_dir), '--extract-state-dir', str(source_archive), 'list'])

    assert result.exit_code == 0
    # All files should be extracted
    assert len(list(dest_dir.glob('jira-*.yaml'))) == 2


def test_extract_state_dir_action_id_filter(tmp_path):
    """Test extracting state directory with action-id-filter."""
    import tarfile
    runner = CliRunner()

    # Create source archive with test YAML files
    source_archive = tmp_path / "source.tar.gz"
    temp_dir = tmp_path / "temp"
    temp_dir.mkdir()

    # Create sample YAML files with different action_ids
    (temp_dir / "jira-issue1.yaml").write_text("""
jira:
  id: PROJ-123
  action_id: test_action
""")
    (temp_dir / "jira-issue2.yaml").write_text("""
jira:
  id: PROJ-456
  action_id: other_action
""")
    (temp_dir / "jira-issue3.yaml").write_text("""
jira:
  id: PROJ-789
  action_id: test_action_2
""")

    # Create archive
    with tarfile.open(source_archive, 'w:gz') as tar:
        for yaml_file in temp_dir.glob('*.yaml'):
            tar.add(yaml_file, arcname=yaml_file.name)

    # Create destination directory
    dest_dir = tmp_path / "dest"

    # Run extract command with action_id filter
    result = runner.invoke(
        cli.main,
        ['--state-dir', str(dest_dir),
         '--extract-state-dir', str(source_archive),
         '--action-id-filter', 'test_.*',
         'list'])

    assert result.exit_code == 0
    # Only files matching action_id pattern should be kept
    extracted_files = list(dest_dir.glob('jira-*.yaml'))
    assert len(extracted_files) == 2

    # Verify correct files were kept
    from newa.utils.yaml_utils import yaml_parser
    for yaml_file in extracted_files:
        yaml_data = yaml_parser().load(yaml_file.read_text())
        action_id = yaml_data.get('jira', {}).get('action_id')
        assert action_id.startswith('test_')


def test_extract_state_dir_issue_id_filter(tmp_path):
    """Test extracting state directory with issue-id-filter."""
    import tarfile
    runner = CliRunner()

    # Create source archive with test YAML files
    source_archive = tmp_path / "source.tar.gz"
    temp_dir = tmp_path / "temp"
    temp_dir.mkdir()

    # Create sample YAML files with different issue IDs
    (temp_dir / "jira-issue1.yaml").write_text("""
jira:
  id: PROJ-123
  action_id: action1
""")
    (temp_dir / "jira-issue2.yaml").write_text("""
jira:
  id: OTHER-456
  action_id: action2
""")
    (temp_dir / "jira-issue3.yaml").write_text("""
jira:
  id: PROJ-789
  action_id: action3
""")

    # Create archive
    with tarfile.open(source_archive, 'w:gz') as tar:
        for yaml_file in temp_dir.glob('*.yaml'):
            tar.add(yaml_file, arcname=yaml_file.name)

    # Create destination directory
    dest_dir = tmp_path / "dest"

    # Run extract command with issue_id filter
    result = runner.invoke(
        cli.main,
        ['--state-dir', str(dest_dir),
         '--extract-state-dir', str(source_archive),
         '--issue-id-filter', 'PROJ-.*',
         'list'])

    assert result.exit_code == 0
    # Only files matching issue_id pattern should be kept
    extracted_files = list(dest_dir.glob('jira-*.yaml'))
    assert len(extracted_files) == 2

    # Verify correct files were kept
    from newa.utils.yaml_utils import yaml_parser
    for yaml_file in extracted_files:
        yaml_data = yaml_parser().load(yaml_file.read_text())
        issue_id = yaml_data.get('jira', {}).get('id')
        assert issue_id.startswith('PROJ-')


def test_extract_state_dir_both_filters(tmp_path):
    """Test extracting state directory with both action-id-filter and issue-id-filter."""
    import tarfile
    runner = CliRunner()

    # Create source archive with test YAML files
    source_archive = tmp_path / "source.tar.gz"
    temp_dir = tmp_path / "temp"
    temp_dir.mkdir()

    # Create sample YAML files with various combinations
    (temp_dir / "jira-issue1.yaml").write_text("""
jira:
  id: PROJ-123
  action_id: test_action
""")
    (temp_dir / "jira-issue2.yaml").write_text("""
jira:
  id: PROJ-456
  action_id: other_action
""")
    (temp_dir / "jira-issue3.yaml").write_text("""
jira:
  id: OTHER-789
  action_id: test_action_2
""")
    (temp_dir / "jira-issue4.yaml").write_text("""
jira:
  id: PROJ-999
  action_id: test_action_3
""")

    # Create archive
    with tarfile.open(source_archive, 'w:gz') as tar:
        for yaml_file in temp_dir.glob('*.yaml'):
            tar.add(yaml_file, arcname=yaml_file.name)

    # Create destination directory
    dest_dir = tmp_path / "dest"

    # Run extract command with both filters
    result = runner.invoke(
        cli.main,
        ['--state-dir', str(dest_dir),
         '--extract-state-dir', str(source_archive),
         '--action-id-filter', 'test_.*',
         '--issue-id-filter', 'PROJ-.*',
         'list'])

    assert result.exit_code == 0
    # Only files matching both patterns should be kept
    extracted_files = list(dest_dir.glob('jira-*.yaml'))
    assert len(extracted_files) == 2

    # Verify correct files were kept
    from newa.utils.yaml_utils import yaml_parser
    for yaml_file in extracted_files:
        yaml_data = yaml_parser().load(yaml_file.read_text())
        issue_id = yaml_data.get('jira', {}).get('id')
        action_id = yaml_data.get('jira', {}).get('action_id')
        assert issue_id.startswith('PROJ-')
        assert action_id.startswith('test_')


def test_copy_state_dir_preserves_event_files(tmp_path):
    """Test that event- files without jira section are preserved when filtering."""
    runner = CliRunner()

    # Create source directory with test YAML files
    source_dir = tmp_path / "source"
    source_dir.mkdir()

    # Create event file without jira section
    (source_dir / "event-12345-RHEL-9.yaml").write_text("""
event:
  id: '12345'
  type_: erratum
erratum:
  id: '12345'
  content_type: rpm
  respin_count: 1
  summary: Test erratum
  url: https://errata.example.com/12345
  builds: []
  release: RHEL-9.4.0
compose:
  id: RHEL-9.4.0-Nightly
""")
    # Create jira files with jira section
    (source_dir / "jira-issue1.yaml").write_text("""
jira:
  id: PROJ-123
  action_id: test_action
""")
    (source_dir / "jira-issue2.yaml").write_text("""
jira:
  id: OTHER-456
  action_id: other_action
""")

    # Create a topdir for new state directories
    topdir = tmp_path / "topdir"
    topdir.mkdir()

    # Run copy command with filters
    result = runner.invoke(
        cli.main,
        ['--state-dir', str(source_dir),
         '--copy-state-dir',
         '--action-id-filter', 'test_.*',
         'list'],
        env={**os.environ, 'NEWA_STATEDIR_TOPDIR': str(topdir)})

    assert result.exit_code == 0

    # Find the newly created state directory
    state_dirs = [d for d in topdir.iterdir() if d.is_dir() and d.name.startswith('run-')]
    assert len(state_dirs) == 1
    dest_dir = state_dirs[0]

    # Event file should be preserved
    assert (dest_dir / "event-12345-RHEL-9.yaml").exists()

    # Only matching jira file should be copied
    jira_files = list(dest_dir.glob('jira-*.yaml'))
    assert len(jira_files) == 1

    from newa.utils.yaml_utils import yaml_parser
    yaml_data = yaml_parser().load(jira_files[0].read_text())
    assert yaml_data.get('jira', {}).get('action_id') == 'test_action'


def test_extract_state_dir_preserves_event_files(tmp_path):
    """Test that event- files without jira section are preserved when filtering."""
    import tarfile
    runner = CliRunner()

    # Create source archive with test YAML files
    source_archive = tmp_path / "source.tar.gz"
    temp_dir = tmp_path / "temp"
    temp_dir.mkdir()

    # Create event file without jira section
    (temp_dir / "event-12345-RHEL-9.yaml").write_text("""
event:
  id: '12345'
  type_: erratum
erratum:
  id: '12345'
  content_type: rpm
  respin_count: 1
  summary: Test erratum
  url: https://errata.example.com/12345
  builds: []
  release: RHEL-9.4.0
compose:
  id: RHEL-9.4.0-Nightly
""")
    # Create jira files with jira section
    (temp_dir / "jira-issue1.yaml").write_text("""
jira:
  id: PROJ-123
  action_id: test_action
""")
    (temp_dir / "jira-issue2.yaml").write_text("""
jira:
  id: OTHER-456
  action_id: other_action
""")

    # Create archive
    with tarfile.open(source_archive, 'w:gz') as tar:
        for yaml_file in temp_dir.glob('*.yaml'):
            tar.add(yaml_file, arcname=yaml_file.name)

    # Create destination directory
    dest_dir = tmp_path / "dest"

    # Run extract command with filters
    result = runner.invoke(
        cli.main,
        ['--state-dir', str(dest_dir),
         '--extract-state-dir', str(source_archive),
         '--issue-id-filter', 'PROJ-.*',
         'list'])

    assert result.exit_code == 0

    # Event file should be preserved
    assert (dest_dir / "event-12345-RHEL-9.yaml").exists()

    # Only matching jira file should be kept
    jira_files = list(dest_dir.glob('jira-*.yaml'))
    assert len(jira_files) == 1

    from newa.utils.yaml_utils import yaml_parser
    yaml_data = yaml_parser().load(jira_files[0].read_text())
    assert yaml_data.get('jira', {}).get('id') == 'PROJ-123'
