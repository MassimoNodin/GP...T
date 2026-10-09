from argparse import Namespace
import json

import pytest

from scripts import benchmark_split_udp


def test_receiver_failure_preserves_samples_and_explicit_failed_result(tmp_path, monkeypatch):
    class FailedRuntime:
        state = 'listening'
        closed = False

        def __init__(self, store, **kwargs):
            self.store = store

        def start(self):
            pass

        def status(self):
            self.state = 'failed'
            return {'received': 1, 'state': 'failed', 'error': 'publication_failed'}

        def close(self):
            self.closed = True

    monkeypatch.setattr(benchmark_split_udp, 'LiveSessionRuntime', FailedRuntime)
    output = tmp_path / 'failed-run'
    args = Namespace(output=output, host='127.0.0.1', port=49077, duration_s=90,
                     expected_datagrams=33700)
    with pytest.raises(RuntimeError, match='publication_failed'):
        benchmark_split_udp.receive(args)
    failure = json.loads((output / 'failure.json').read_text())
    assert failure['passed'] is False
    assert failure['before_stop']['received'] == 1
    assert failure['samples'] == 1
    assert len(json.loads((output / 'samples.json').read_text())) == 1
    assert not (output / 'summary.json').exists()
    assert (output / 'evidence.sqlite3').exists()
