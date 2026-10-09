from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import math
from pathlib import Path
import socket
import time

from f1_engineer.analysis.session_comparison import compare_session_laps
from f1_engineer.processing.evidence import EvidenceStore
from f1_engineer.processing.runtime import LiveSessionRuntime
from scripts.benchmark_session_publication import percentile, rich_packets
from tests.test_session_evidence import completed_pair


def current_rss_bytes() -> int | None:
    status = Path('/proc/self/status')
    if not status.exists():
        return None
    for line in status.read_text().splitlines():
        if line.startswith('VmRSS:'):
            return int(line.split()[1]) * 1024
    return None


def send(args) -> None:
    sent = 0
    maximum_lateness = 0.0
    started = next_send = time.perf_counter()
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
        for raw in rich_packets(math.ceil(args.duration_s * max(args.rate, args.burst_rate) / 7) + 1, args.lap_frames):
            if next_send - started >= args.duration_s:
                break
            remaining = next_send - time.perf_counter()
            if remaining > 0:
                time.sleep(remaining)
            maximum_lateness = max(maximum_lateness, time.perf_counter() - next_send)
            sender.sendto(raw.payload, (args.host, args.port))
            sent += 1
            elapsed = next_send - started
            next_send += 1 / (args.burst_rate if 30 <= elapsed < 40 else args.rate)
    print(json.dumps({'sender_host': socket.gethostname(), 'sent': sent,
                      'elapsed_s': time.perf_counter() - started,
                      'maximum_sender_lateness_s': maximum_lateness}), flush=True)


def receive(args) -> None:
    args.output.mkdir(parents=True, exist_ok=False)
    store = EvidenceStore(args.output / 'evidence.sqlite3')
    runtime = LiveSessionRuntime(store, host=args.host, port=args.port)
    runtime.start()
    samples = []
    comparison = None
    pending = None
    first_received = None
    ready_deadline = time.monotonic() + 120
    comparison_after = 8.0
    with ThreadPoolExecutor(max_workers=1) as comparisons:
        try:
            if runtime.state != 'listening':
                raise RuntimeError(runtime.status())
            print(json.dumps({'ready': True, 'host': args.host, 'port': args.port}), flush=True)
            while True:
                now = time.monotonic()
                status = runtime.status()
                if first_received is None and status['received']:
                    first_received = now
                if first_received is None:
                    if now > ready_deadline:
                        raise RuntimeError('No synthetic UDP received within 120 seconds')
                    time.sleep(0.05)
                    continue
                elapsed = now - first_received
                samples.append({'elapsed_s': elapsed, 'rss_bytes': current_rss_bytes(), **status})
                if runtime.state == 'failed':
                    raise RuntimeError(status)
                if pending is not None and pending.done():
                    try:
                        comparison = pending.result()
                    except (StopIteration, ValueError):
                        comparison_after = elapsed + 1
                    pending = None
                if elapsed >= comparison_after and comparison is None and pending is None:
                    def compare():
                        session, target, reference = completed_pair(store)
                        before = runtime.processed
                        report = compare_session_laps(store, session, target['id'], reference['id'])
                        return {'report': report, 'processed_before': before,
                                'processed_after': runtime.processed, 'target_rows': target['rows'],
                                'reference_rows': reference['rows']}
                    pending = comparisons.submit(compare)
                if elapsed >= args.duration_s + 1 and status['queue_depth'] == 0 and status['pending_publication'] == 0:
                    break
                if elapsed >= args.duration_s + 15:
                    raise RuntimeError('Synthetic UDP backlog did not drain within 15 seconds')
                time.sleep(0.05)
            before_stop = runtime.status()
            if pending is not None:
                comparison = pending.result(timeout=10)
        finally:
            runtime.close()
    reopened = EvidenceStore(store.path)
    retained = bool(comparison and reopened.report(comparison['report']['id']) == comparison['report'])
    publications = [sample['last_publication_s'] for sample in samples if sample['last_publication_s']]
    delays = [sample['last_publication_delay_s'] for sample in samples if sample['last_publication_s']]
    rss = [sample['rss_bytes'] for sample in samples if sample['rss_bytes'] is not None]
    metrics = {'sampled_publication_p95_s': percentile(publications, 0.95),
               'sampled_oldest_delay_p95_s': percentile(delays, 0.95),
               'sampled_oldest_delay_max_s': max(delays, default=0),
               'sampled_rss_peak_bytes': max(rss, default=None)}
    checks = {
        'lossless': before_stop['received'] == before_stop['processed'] == args.expected_datagrams
                    and before_stop['dropped'] == 0 and before_stop['socket_errors'] == 0,
        'comparison_retained': retained and comparison['target_rows'] > 100 and comparison['reference_rows'] > 100,
        'ingestion_continued': bool(comparison and before_stop['processed'] > comparison['processed_after']),
        'publication_p95': metrics['sampled_publication_p95_s'] is not None and metrics['sampled_publication_p95_s'] <= 0.050,
        'oldest_delay_p95': metrics['sampled_oldest_delay_p95_s'] is not None and metrics['sampled_oldest_delay_p95_s'] <= 0.250,
        'oldest_delay_max': metrics['sampled_oldest_delay_max_s'] <= 1.0,
    }
    with store.connect() as database:
        checks['sqlite_integrity'] = database.execute('PRAGMA quick_check').fetchone()[0] == 'ok'
    result = {'kind': 'two_host_synthetic_udp', 'duration_s': args.duration_s, 'before_stop': before_stop,
              'metrics': metrics, 'checks': checks, 'database_bytes': store.path.stat().st_size,
              'comparison_id': comparison['report']['id'] if comparison else None,
              'passed': all(checks.values())}
    (args.output / 'samples.json').write_text(json.dumps(samples))
    (args.output / 'summary.json').write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2), flush=True)
    if not result['passed']:
        raise SystemExit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description='Synthetic two-host UDP acceptance; not live-game evidence')
    parser.add_argument('mode', choices=('send', 'receive'))
    parser.add_argument('--host', required=True)
    parser.add_argument('--port', type=int, default=49077)
    parser.add_argument('--duration-s', type=float, default=90)
    parser.add_argument('--rate', type=float, default=355)
    parser.add_argument('--burst-rate', type=float, default=530)
    parser.add_argument('--lap-frames', type=int, default=150)
    parser.add_argument('--output', type=Path, default=Path('data/migration-split-udp'))
    parser.add_argument('--expected-datagrams', type=int, default=33700)
    args = parser.parse_args()
    if (not 1 <= args.port <= 65535 or not 40 <= args.duration_s <= 1800
            or not 0 < args.rate <= 2000 or not 0 < args.burst_rate <= 2000
            or args.lap_frames < 2 or args.expected_datagrams < 1):
        parser.error('Invalid bounded benchmark configuration')
    (send if args.mode == 'send' else receive)(args)


if __name__ == '__main__':
    main()
