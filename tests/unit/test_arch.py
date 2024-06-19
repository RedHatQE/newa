from newa import Arch


def test_arch_ok():
    arch_list = Arch.architectures([Arch.MULTI])
    default_list = Arch.architectures()
    subset = [Arch.X86_64, Arch.S390X]
    intersect = Arch.architectures(subset)

    assert len(arch_list) == 4
    assert all(Arch(n) in arch_list for n in Arch.__members__.values() if n != Arch.MULTI)
    assert set(default_list) == {Arch.X86_64}
    assert set(subset) == set(intersect)
