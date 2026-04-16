# Configure shebang options based on Python version
# Python 3.11+: use -P flag only (safe path)
# Python < 3.11: no flags (plain /usr/bin/python3)
%if 0%{?python3_version_nodots} >= 311
%global py3_shbang_opts -P
%else
%global py3_shbang_opts %{nil}
%endif

Name:           newa
Version:        0.1
Release:        %autorelease
Summary:        New Errata Workflow

License:        MIT
URL:            https://github.com/RedHatQE/newa
Source0:        %{pypi_source tmt}

BuildArch:      noarch
BuildRequires:  python3-devel python3-jira

%py_provides    python3-newa

%description
The newa Python module and command line tool used for errata
workflow automation.

%prep
%autosetup -p1 -n newa-%{version}

%generate_buildrequires
%pyproject_buildrequires

%build
export SETUPTOOLS_SCM_PRETEND_VERSION=%{version}
%pyproject_wheel

%install
%pyproject_install
%pyproject_save_files newa

# Install bash completion
install -D -m 0644 newa-completion.bash %{buildroot}%{_datadir}/bash-completion/completions/newa

%check
%pyproject_check_import

%files -n newa -f %{pyproject_files}
%doc README.md
%doc docs/agents/claude-newa.md
%{_bindir}/newa
%{_datadir}/bash-completion/completions/newa

%changelog
* Thu Jun 06 2024 Miroslav Vadkerti <mvadkert@redhat.com> - 0.1-1
- Initial packaging
