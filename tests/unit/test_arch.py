from newa import Arch


def test_arch_ok():
    arch_list = Arch.architectures([Arch.MULTI])
    noarch_list = Arch.architectures([Arch.NOARCH])
    default_list = Arch.architectures()
    subset = [Arch.X86_64, Arch.S390X]
    intersect = Arch.architectures(subset)

    assert len(arch_list) == 4
    assert all(Arch(n) in arch_list for n in Arch.__members__.values()
               if n not in [Arch.MULTI, Arch.SRPMS, Arch.NOARCH])
    assert set(arch_list) == set(noarch_list)
    assert set(default_list) == {Arch.X86_64}
    assert set(subset) == set(intersect)
