Name:           newa
Version:        0.1
Release:        %autorelease
Summary:        New Errata Workflow

License:        MIT
URL:            https://github.com/RedHatQE/newa
Source0:        %{pypi_source tmt}

BuildArch:      noarch
BuildRequires:  python3-devel

%py_provides    python3-newa

%description
The newa Python module and command line tool used for errata
workflow automation.

Note: Full Jira Cloud support (including search functionality) requires
python3-jira >= 3.10.5. On EPEL9 and Fedora 42, older versions are used
which may have limited Jira Cloud compatibility.

%prep
%autosetup -p1 -n newa-%{version}

%generate_buildrequires
# EPEL9 has python3-jira 3.5.0, Fedora 42 has 3.8.0, but pyproject.toml requires >=3.10.5
# for full Jira Cloud search support. We use -R to allow RPM to relax the version constraint
# and use whatever version is available in the distro. This allows the package to build but
# may limit Jira Cloud functionality on older distributions.
%pyproject_buildrequires -R

%build
export SETUPTOOLS_SCM_PRETEND_VERSION=%{version}
%pyproject_wheel

%install
%pyproject_install
%pyproject_save_files newa

%check
%pyproject_check_import

%files -n newa -f %{pyproject_files}
%doc README.md
%{_bindir}/newa

%changelog
* Thu June 06 2024 Miroslav Vadkerti <mvadkert@redhat.com> - 0.1-1
- Initial packaging
