from __future__ import annotations

import argparse
import hashlib
import json
import math
import socket
import sqlite3
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

from f1_engineer.processing.coordinator import PUBLICATION_BATCH_ROWS, SessionCoordinator
from f1_engineer.processing.evidence import EvidenceStore, encode
from f1_engineer.processing.runtime import LiveSessionRuntime
from f1_engineer.udp.models import RawDatagram
from tests.helpers import HEADER, make_datagram
from tests.test_car_damage import _damage_body
from tests.test_car_setups import _body as setups_body
from tests.test_car_status import _status_body
from tests.test_motion import _motion_body
from f1_engineer.analysis.session_comparison import compare_session_laps
from tests.test_lap_tracking import LAP_RECORD, _lap_body
from tests.test_session_evidence import admitted_packets, completed_pair
from tests.test_session_history import _body as history_body


def fingerprint(store):
    result = hashlib.sha256()
    with store.connect() as database:
        for row in database.execute("SELECT uid,occurrence,lifecycle,acquisition,context FROM sessions ORDER BY uid,occurrence"):
            result.update(encode(tuple(row)).encode())
        for row in database.execute("""SELECT s.uid,s.occurrence,a.* FROM attempts a JOIN sessions s ON s.id=a.session
            ORDER BY s.uid,s.occurrence,a.published_sequence,a.role,json_extract(a.payload,'$.car_index')"""):
            manifest = json.loads(row["manifest"])
            manifest.pop("driver")
            binding = database.execute("""SELECT epoch,format,car,tenure,start_frame,end_frame,fingerprint,payload
                FROM bindings WHERE id=?""", (row["driver"],)).fetchone()
            dispositions = [tuple(item) for item in database.execute(
                "SELECT sequence,state,reason FROM dispositions WHERE attempt=? ORDER BY sequence", (row["id"],))]
            result.update(encode((row["uid"], row["occurrence"], row["role"], manifest, row["payload"],
                                  tuple(binding) if binding else None, dispositions, row["rows"], row["bytes"])).encode())
            for chunk in manifest["chunks"]:
                content = database.execute("SELECT payload FROM chunks WHERE hash=?", (chunk,)).fetchone()[0]
                if hashlib.sha256(content.encode()).hexdigest() != chunk:
                    raise ValueError("chunk_checksum_mismatch")
        for row in database.execute("""SELECT uid,car,frame,epoch,format,sequence,gap_epoch,payload
            FROM staging ORDER BY uid,car,frame"""):
            result.update(encode(tuple(row)).encode())
    return result.hexdigest()


def replay(journal, output):
    measurements = {}
    for mode in ("serial", "batched"):
        store = EvidenceStore(output / f"{mode}.sqlite3")
        coordinator = SessionCoordinator(store, mode)
        count = 0
        started = time.perf_counter()
        try:
            with sqlite3.connect(journal.resolve().as_uri() + "?mode=ro", uri=True) as source:
                if source.execute("SELECT COUNT(DISTINCT generation) FROM journal").fetchone()[0] != 1:
                    raise ValueError("benchmark_requires_one_source_generation")
                cursor = source.execute("SELECT kind,payload,metadata FROM journal ORDER BY sequence")
                while rows := cursor.fetchmany(32):
                    for kind, payload, metadata in rows:
                        metadata = json.loads(metadata)
                        if kind == "datagram":
                            raw = RawDatagram(payload=bytes(payload), **{
                                key: metadata[key] for key in ("sequence", "captured_at_ns", "monotonic_ns", "source_host", "source_port")
                            })
                            if mode == "serial":
                                coordinator.ingest(raw)
                            else:
                                coordinator.journal(raw)
                                if coordinator.pending_publication == PUBLICATION_BATCH_ROWS:
                                    coordinator.publish_pending()
                            count += 1
                        elif kind == "gap":
                            coordinator.gap(metadata["reason"])
                        elif kind == "finish":
                            coordinator.finish()
                        else:
                            raise ValueError("unknown_journal_kind")
            coordinator.publish_pending()
            elapsed = time.perf_counter() - started
        finally:
            coordinator.close()
        measurements[mode] = {"datagrams": count, "elapsed_s": elapsed,
                              "datagrams_per_s": count / elapsed, "fingerprint": fingerprint(store),
                              "database_bytes": store.path.stat().st_size}
        print(json.dumps({"mode": mode, **measurements[mode]}), flush=True)
    if measurements["serial"]["fingerprint"] != measurements["batched"]["fingerprint"]:
        raise AssertionError("normalized_replay_diverged")
    return {"kind": "faithful_admitted_journal_replay", "source": str(journal),
            "gaps_preserved": True, "measurements": measurements, "parity": True}


def rich_packets(frames, lap_frames=150):
    additional = [(0, _motion_body()), (7, _status_body()), (10, _damage_body()),
                  (5, setups_body()), (11, history_body(car_index=0))]
    sequence = 0
    for raw in admitted_packets(frames=frames, lap_frames=lap_frames):
        header = HEADER.unpack_from(raw.payload)
        if header[5] == 2:
            position = (header[9] - 1) % lap_frames
            lap_number = 1 + (header[9] - 1) // lap_frames
            records = []
            for car in range(22):
                body = _lap_body(lap_number=lap_number, distance_m=position * 5,
                                 current_lap_time_ms=position * 50, last_lap_time_ms=lap_frames * 50 + car * 10,
                                 active_car_index=car)
                records.append(body[car * LAP_RECORD.size:(car + 1) * LAP_RECORD.size])
            raw = replace(raw, payload=raw.payload[:HEADER.size] + b"".join(records) + bytes((0, 255)))
        yield replace(raw, sequence=sequence)
        sequence += 1
        header = HEADER.unpack_from(raw.payload)
        if header[5] != 6:
            continue
        for packet_id, body in additional:
            yield make_datagram(packet_id=packet_id, session_uid=header[6], frame=header[9],
                                session_time=header[7], body=body, sequence=sequence)
            sequence += 1


def percentile(values, fraction):
    return sorted(values)[min(len(values) - 1, int(len(values) * fraction))] if values else None


def paced(output, duration, rate, batch_size, burst_rate, lap_frames):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as reserve:
        reserve.bind(("127.0.0.1", 0))
        port = reserve.getsockname()[1]
    store = EvidenceStore(output / "paced.sqlite3")
    runtime = LiveSessionRuntime(store, host="127.0.0.1", port=port, publication_batch_size=batch_size)
    runtime.start()
    comparisons = ThreadPoolExecutor(max_workers=1)
    pending_comparison = None
    comparison_error = None
    sent = 0
    samples = []
    publication = []
    delay = []
    started = next_send = time.perf_counter()
    next_sample = 0.0
    try:
        if runtime.state != "listening":
            raise RuntimeError(runtime.status())
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
            for raw in rich_packets(math.ceil(duration * max(rate, burst_rate) / 7) + 1, lap_frames):
                if next_send - started >= duration:
                    break
                remaining = next_send - time.perf_counter()
                if remaining > 0:
                    time.sleep(remaining)
                sender.sendto(raw.payload, ("127.0.0.1", port))
                sent += 1
                elapsed = time.perf_counter() - started
                if elapsed >= 2 * lap_frames * 7 / rate + 1 and pending_comparison is None:
                    def compare():
                        session, target, reference = completed_pair(store)
                        before = runtime.processed
                        report = compare_session_laps(store, session, target["id"], reference["id"])
                        return {"report": report, "processed_before": before, "processed_after": runtime.processed,
                                "target_rows": target["rows"], "reference_rows": reference["rows"]}
                    pending_comparison = comparisons.submit(compare)
                if elapsed >= next_sample:
                    status = runtime.status()
                    samples.append({"elapsed_s": elapsed, **status})
                    if status["last_publication_s"]:
                        publication.append(status["last_publication_s"])
                        delay.append(status["last_publication_delay_s"])
                    next_sample = elapsed + 0.05
                next_send += 1 / (burst_rate if 30 <= next_send - started < 40 else rate)
        drain_started = time.perf_counter()
        deadline = drain_started + 10
        while runtime.processed < sent and time.perf_counter() < deadline and runtime.state != "failed":
            time.sleep(0.02)
        drain_s = time.perf_counter() - drain_started
        before_stop = runtime.status()
        try:
            comparison = pending_comparison.result(timeout=10) if pending_comparison else None
        except Exception as exception:
            comparison = None
            comparison_error = f"{type(exception).__name__}: {exception}"
    finally:
        runtime.close()
        comparisons.shutdown(wait=True)
    comparison_retained = store.report(comparison["report"]["id"]) == comparison["report"] if comparison else False
    with store.connect() as database:
        readiness = [tuple(row) for row in database.execute("""SELECT a.role,d.state,COUNT(*) FROM attempts a
            JOIN dispositions d ON d.attempt=a.id WHERE d.sequence=(SELECT MAX(sequence) FROM dispositions WHERE attempt=a.id)
            GROUP BY a.role,d.state""")]
    (output / "samples.json").write_text(json.dumps(samples), encoding="utf8")
    result = {"kind": "synthetic_advancing_seven_packet_kind_load", "duration_s": duration,
              "offered_base_rate": rate, "burst_rate": burst_rate, "batch_size": batch_size, "sent": sent,
              "lap_frames": lap_frames, "comparison": comparison, "comparison_error": comparison_error,
              "comparison_retained_after_stop": comparison_retained,
              "drain_s": drain_s, "before_stop": before_stop, "readiness": readiness,
              "sampled_publication_p95_s": percentile(publication, 0.95),
              "sampled_oldest_delay_p95_s": percentile(delay, 0.95),
              "sampled_oldest_delay_max_s": max(delay, default=0),
              "database_bytes": store.path.stat().st_size,
              "passed": (before_stop["received"] == sent == before_stop["processed"] and before_stop["dropped"] == 0
                         and comparison_retained and comparison["target_rows"] > 100 and comparison["reference_rows"] > 100
                         and before_stop["processed"] > comparison["processed_after"])}
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--journal", type=Path)
    parser.add_argument("--duration-s", type=float, default=90)
    parser.add_argument("--rate", type=float, default=355)
    parser.add_argument("--burst-rate", type=float, default=530)
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--lap-frames", type=int, default=150)
    parser.add_argument("--output", type=Path, default=Path("data") / f"publication-benchmark-{uuid.uuid4().hex[:10]}")
    args = parser.parse_args()
    if args.duration_s <= 0 or args.rate <= 0 or args.burst_rate <= 0 or args.lap_frames < 2:
        parser.error("duration and rates must be positive")
    args.output.mkdir(parents=True, exist_ok=False)
    result = replay(args.journal, args.output) if args.journal else paced(
        args.output, args.duration_s, args.rate, args.batch_size, args.burst_rate, args.lap_frames)
    (args.output / "summary.json").write_text(json.dumps(result, indent=2), encoding="utf8")
    printable = dict(result)
    if result.get("comparison"):
        comparison = result["comparison"]
        printable["comparison"] = {key: value for key, value in comparison.items() if key != "report"}
        printable["comparison"]["report"] = {
            key: comparison["report"][key] for key in ("id", "policy", "lap_time_difference_ms", "coverage", "qualifications")
        }
        printable["comparison"]["supported_braking_zones"] = sum(
            zone["supported"] for zone in comparison["report"]["braking_zones"])
    print(json.dumps(printable, indent=2), flush=True)
    if result.get("passed") is False:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
