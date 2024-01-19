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

%check
%pyproject_check_import

%files -n newa -f %{pyproject_files}
%doc README.md
%{_bindir}/newa

%changelog
* Tue June 06 2024 Miroslav Vadkerti <mvadkert@redhat.com> - 0.1-1
- Initial packaging
