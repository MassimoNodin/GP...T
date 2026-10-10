from pathlib import Path


def test_optional_four_core_units_reserve_disjoint_cpu_sets():
    units = Path(__file__).resolve().parents[1] / "scripts" / "ubuntu"
    backend = (units / "f1-engineer-four-core-cpu.conf").read_text()
    model = (units / "f1-engineer-ollama-four-core-cpu.conf").read_text()
    assert backend == "[Service]\nCPUAffinity=0 1\n"
    assert model == "[Service]\nCPUAffinity=2 3\nNice=5\n"
