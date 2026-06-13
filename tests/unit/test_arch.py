from newa import Arch


def test_arch_ok():
    arch_list = Arch.architectures([Arch.MULTI])
    noarch_list = Arch.architectures([Arch.NOARCH])
    noarch_list_rhel7 = Arch.architectures([Arch.NOARCH], compose='RHEL-7')
    default_list = Arch.architectures()
    default_list_rhel7 = Arch.architectures(compose='rhel-7.9')
    exp_default_list = [Arch.X86_64, Arch.S390X, Arch.PPC64LE, Arch.AARCH64]
    subset = [Arch.X86_64, Arch.S390X]
    intersect = Arch.architectures(subset)

    assert len(arch_list) == 4
    assert all(Arch(n) in default_list for n in ['x86_64', 's390x', 'ppc64le', 'aarch64'])
    assert all(Arch(n) in default_list_rhel7 for n in ['x86_64', 's390x', 'ppc64le', 'ppc64'])
    assert set(arch_list) == set(noarch_list)
    assert set(default_list_rhel7) == set(noarch_list_rhel7)
    assert set(default_list) == set(exp_default_list)
    assert set(subset) == set(intersect)


def test_arch_rhel6_default():
    default = Arch.architectures(compose='RHEL-6.10-ZStream')
    assert set(default) == {Arch.X86_64, Arch.S390X, Arch.I386}


def test_arch_rhel6_preset_includes_i386():
    preset = [Arch.I386, Arch.S390X, Arch.S390, Arch.X86_64]
    result = Arch.architectures(preset, compose='RHEL-6.10-ZStream')
    assert Arch.I386 in result
    assert Arch.X86_64 in result
    assert Arch.S390X in result
    assert Arch.S390 not in result


def test_arch_rhel6_noarch_returns_default():
    result = Arch.architectures([Arch.NOARCH], compose='RHEL-6.10-ZStream')
    assert set(result) == {Arch.X86_64, Arch.S390X, Arch.I386}


def test_arch_rhel6_multi_returns_default():
    result = Arch.architectures([Arch.MULTI], compose='RHEL-6.10-ZStream')
    assert set(result) == {Arch.X86_64, Arch.S390X, Arch.I386}


def test_arch_i386_excluded_without_rhel6():
    preset = [Arch.I386, Arch.X86_64, Arch.S390X]
    result = Arch.architectures(preset)
    assert Arch.I386 not in result

    result_rhel8 = Arch.architectures(preset, compose='RHEL-8.10.0.Z.MAIN')
    assert Arch.I386 not in result_rhel8

    result_rhel9 = Arch.architectures(preset, compose='RHEL-9.6.0.Z.MAIN')
    assert Arch.I386 not in result_rhel9
