import asyncio

import pytest

from bridge import Bridge, SQLiteStore
from bridge.domain import RolloutTaskState
from bridge.errors import CapabilityUnavailable

def test_rollout_preflight_requires_a_provisioned_trust_store():
    bridge = Bridge()
    task = bridge.create_rollout("known-device", "image-v1") if False else bridge.rollouts.create("device-1", "image-v1")
    with pytest.raises(CapabilityUnavailable, match="trust_not_provisioned"):
        asyncio.run(bridge.preflight_rollout(task.task_id))

def test_rollout_persists_and_restart_waits_for_device_confirmation(tmp_path):
    store = SQLiteStore(str(tmp_path / "rollout.db"))
    bridge = Bridge(store)
    task = bridge.rollouts.create("device-1", "image-v2")
    task.state = RolloutTaskState.RUNNING
    bridge.rollouts._save(task)
    restarted = Bridge(store)
    recovered = restarted.rollouts.get(task.task_id)
    assert recovered.state is RolloutTaskState.AWAITING_CONFIRMATION
    assert recovered.error["reason"] == "bridge_restarted_during_update"
