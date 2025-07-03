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
