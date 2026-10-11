import threading

from f1_engineer.processing.detail_demand import (
    MAX_ACTIVE_DETAIL_TASKS, MAX_DETAIL_COMMANDS, MAX_RECENT_DETAIL_TASKS,
    DetailCommand, DetailDemandQueue,
)


SESSION = "a" * 32
BINDINGS = [f"{index:064x}" for index in range(1, 9)]


def target_queue(clock=lambda: 0.0):
    queue = DetailDemandQueue(clock)
    queue.publish_targets(SESSION, tuple({"binding_id": binding, "car_index": index,
                                         "epoch": 1, "packet_format": 2025,
                                         "is_player": False}
                                        for index, binding in enumerate(BINDINGS)))
    return queue


def post(queue, index=0, task="task_1"):
    return queue.enqueue(DetailCommand("post", task, SESSION, BINDINGS[index]))


def apply_post(queue, index=0, task="task_1", sequence=7):
    command = queue.pop()
    assert command is not None
    return queue.apply(command, effective_sequence=sequence, target_current=True,
                       frame_ordinal=10)


def test_D01_queued_is_not_active_and_effective_sequence_is_owner_applied():
    queue = target_queue()
    status, queued = post(queue)
    assert status == 202 and queued["state"] == "queued"
    assert queue.get("task_1").state == "queued"
    applied = apply_post(queue)
    assert applied.state == "active" and applied.effective_sequence == 7
    assert applied.activation_frame_ordinal == 10


def test_D02_renewal_and_identity_conflict_preserve_active_task():
    queue = target_queue()
    post(queue)
    active = apply_post(queue)
    status, conflict = queue.enqueue(DetailCommand("post", "task_1", SESSION, BINDINGS[1]))
    assert status == 409 and conflict["reason"] == "detail_task_identity_conflict"
    status, _ = post(queue)
    assert status == 202 and queue.get("task_1").state == "active"
    renewed = apply_post(queue)
    assert renewed.activation_ordinal == active.activation_ordinal
    assert renewed.expires_at == active.expires_at


def test_D03_task_command_and_terminal_snapshots_are_bounded_and_duplicate_posts_coalesce():
    queue = target_queue()
    post(queue)
    status, _ = post(queue)
    assert status == 202 and queue.counts()[1] == 1
    apply_post(queue)
    for index in range(1, MAX_ACTIVE_DETAIL_TASKS):
        post(queue, index=index, task=f"task_{index+1}")
        apply_post(queue, index=index, task=f"task_{index+1}")
    assert queue.counts()[0] == MAX_ACTIVE_DETAIL_TASKS
    for index in range(MAX_DETAIL_COMMANDS):
        queue.enqueue(DetailCommand("release", "task_1", SESSION, BINDINGS[0]))
    assert queue.counts()[1] == MAX_DETAIL_COMMANDS
    assert queue.enqueue(DetailCommand("release", "task_1", SESSION, BINDINGS[0]))[1]["reason"] == "detail_command_budget_busy"
    queue.reject_queued_on_shutdown()
    for index in range(MAX_RECENT_DETAIL_TASKS + 3):
        task_id = f"terminal_{index}"
        queue.enqueue(DetailCommand("post", task_id, SESSION, BINDINGS[4]))
        command = queue.pop()
        queue.apply(command, effective_sequence=index + 1, target_current=False)
    terminal = [task for task in queue._tasks.values() if task.state in {"released", "expired", "rejected"}]
    assert len(terminal) <= MAX_RECENT_DETAIL_TASKS


def test_D04_release_of_one_overlapping_task_preserves_other_active_task():
    queue = target_queue()
    post(queue, 1, "one")
    apply_post(queue, 1, "one")
    post(queue, 1, "two")
    apply_post(queue, 1, "two")
    queue.enqueue(DetailCommand("release", "one", SESSION, BINDINGS[1]))
    command = queue.pop()
    assert queue.apply(command, effective_sequence=9, target_current=True).state == "released"
    assert [item.task_id for item in queue.active_tasks()] == ["two"]


def test_D05_fake_clock_expiry_and_renewal_at_deadline():
    now = [0.0]
    queue = target_queue(lambda: now[0])
    post(queue)
    task = apply_post(queue)
    assert queue.due_tasks() == ()
    now[0] = 299.999
    assert queue.due_tasks() == ()
    post(queue)
    renewed = apply_post(queue, sequence=8)
    assert renewed.state == "active" and renewed.expires_at == 599.999
    now[0] = 599.999
    assert queue.expire_one("task_1", effective_sequence=9).state == "expired"
    status, _ = post(queue)
    assert status == 202
    new = apply_post(queue, sequence=10)
    assert new.activation_ordinal == task.activation_ordinal + 1


def test_D06_new_activation_records_its_own_coverage_boundary():
    queue = target_queue()
    post(queue)
    first = apply_post(queue, sequence=11)
    queue.enqueue(DetailCommand("release", "task_1", SESSION, BINDINGS[0]))
    queue.apply(queue.pop(), effective_sequence=15, target_current=True)
    post(queue)
    second = apply_post(queue, sequence=20)
    assert first.effective_sequence == 11 and second.effective_sequence == 20
    assert second.activation_ordinal == 2


def test_D07_stale_session_or_binding_is_rejected_without_retargeting():
    queue = target_queue()
    post(queue, index=2)
    command = queue.pop()
    rejected = queue.apply(command, effective_sequence=2, target_current=False)
    assert rejected.state == "rejected" and rejected.reason == "detail_target_not_current"
    assert queue.get("task_1").binding_id == BINDINGS[2]


def test_D08_interruption_terminates_active_task_without_changing_other_task():
    queue = target_queue()
    post(queue, 0, "active")
    apply_post(queue, 0, "active")
    queue.enqueue(DetailCommand("post", "waiting", SESSION, BINDINGS[1]))
    queue.terminate_one("active", reason="detail_acquisition_interrupted", effective_sequence=3)
    queue.reject_queued_on_shutdown()
    assert queue.get("active").state == "expired"
    assert queue.get("waiting").state == "rejected"


def test_D09_journal_replay_uses_recorded_task_mutation_not_current_clock():
    now = [0.0]
    queue = target_queue(lambda: now[0])
    post(queue)
    original = apply_post(queue, sequence=10)
    recorded = (original.task_id, original.session_id, original.binding_id,
                original.activation_ordinal, original.effective_sequence)
    now[0] = 10000.0
    replay = target_queue(lambda: now[0])
    replay.enqueue(DetailCommand("post", recorded[0], recorded[1], recorded[2]))
    command = replay.pop()
    applied = replay.apply(command, effective_sequence=recorded[4], target_current=True)
    assert (applied.task_id, applied.session_id, applied.binding_id,
            applied.activation_ordinal, applied.effective_sequence) == recorded


def test_D10_owner_revalidates_target_after_it_disappears():
    queue = target_queue()
    post(queue)
    command = queue.pop()
    queue.publish_targets(SESSION, ())
    result = queue.apply(command, effective_sequence=3, target_current=False)
    assert result.state == "rejected" and result.reason == "detail_target_not_current"


def test_D11_concurrent_enqueue_and_read_only_snapshot_are_lock_safe():
    queue = target_queue()
    barrier = threading.Barrier(3)
    results = []
    def producer(index):
        barrier.wait()
        results.append(post(queue, index=index, task=f"thread_{index}"))
    threads = [threading.Thread(target=producer, args=(index,)) for index in range(2)]
    for thread in threads:
        thread.start()
    barrier.wait()
    snapshots = [queue.get(f"thread_{index}") for index in range(2)]
    for thread in threads:
        thread.join()
    assert all(status == 202 for status, _ in results)
    assert all(task is None or task.state in {"queued", "active"} for task in snapshots)
    assert queue.counts()[1] == 2


def test_D12_shutdown_resolves_queued_commands_without_worker_retention():
    queue = target_queue()
    post(queue)
    queue.reject_queued_on_shutdown()
    assert queue.counts()[1] == 0
    assert queue.get("task_1").state == "rejected"
    assert queue.get("task_1").last_command_reason == "detail_acquisition_interrupted"
