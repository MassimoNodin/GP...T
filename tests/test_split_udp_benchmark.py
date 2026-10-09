from argparse import Namespace
import json
from collections import deque

import pytest

from scripts import benchmark_split_udp
from tests.test_session_evidence import admitted_packets
from tests.test_lap_tracking import LAP_RECORD
from tests.helpers import HEADER


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


def test_extended_fixture_uses_requested_lap_length_before_rich_packet_encoding():
    final = deque(admitted_packets(frames=5101, car_count=1, missing_telemetry=True,
                                  lap_frames=150), maxlen=1)[0]
    assert LAP_RECORD.unpack_from(final.payload, HEADER.size)[14] == 35


def test_rich_fixture_forwards_its_lap_length(monkeypatch):
    from scripts import benchmark_session_publication
    observed = []

    def fixture(**kwargs):
        observed.append(kwargs)
        return iter(())

    monkeypatch.setattr(benchmark_session_publication, 'admitted_packets', fixture)
    assert list(benchmark_session_publication.rich_packets(5101, 150)) == []
    assert observed == [{'frames': 5101, 'lap_frames': 150}]
