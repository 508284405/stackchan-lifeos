from bridge import Bridge, SQLiteStore
from bridge.domain import RolloutTaskState

def test_rollout_gate_and_preconditions_are_structured():
    bridge = Bridge()
    task = bridge.create_rollout("known-device", "image-v1") if False else bridge.rollouts.create("device-1", "image-v1")
    rejected = bridge.preflight_rollout(task.task_id)
    assert rejected.state is RolloutTaskState.REJECTED
    assert rejected.error["reason"] == "feature_gate_disabled"

def test_rollout_persists_and_restart_expires_running_task(tmp_path):
    store = SQLiteStore(str(tmp_path / "rollout.db"))
    bridge = Bridge(store)
    task = bridge.rollouts.create("device-1", "image-v2")
    task.state = RolloutTaskState.RUNNING
    bridge.rollouts._save(task)
    restarted = Bridge(store)
    recovered = restarted.rollouts.get(task.task_id)
    assert recovered.state is RolloutTaskState.EXPIRED
    assert recovered.error["reason"] == "bridge_restarted"
