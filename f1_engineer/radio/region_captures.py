from __future__ import annotations

import copy
import threading
import time


class RadioRegionCaptures:
    def __init__(self, client, *, notify=None, clock=time.monotonic):
        self.client = client
        self.notify = notify or (lambda event: None)
        self.clock = clock
        self._lock = threading.Lock()
        self._poll_lock = threading.Lock()
        self._desired = []
        self._owned = {}

    def replace(self, requests):
        with self._lock:
            self._desired = copy.deepcopy(requests)

    def _release(self, lease_id, owned):
        if owned["cleanup_verified"]:
            return True
        if self.clock() < owned["release_retry_at"]:
            return False
        result = self.client.region_capture("DELETE", lease_id)
        data = result.get("data") if result.get("status") == "available" else None
        verified = (isinstance(data, dict) and data.get("lease_id") == lease_id
                    and data.get("state") in ("released", "expired", "unavailable"))
        owned["release_retry_at"] = self.clock() + 10
        owned["cleanup_verified"] = verified
        self.notify({"event": "radio_region_capture_released", "lease_id": lease_id,
                     "status": result.get("status"), "reason": result.get("reason"),
                     "cleanup_verified": verified})
        return verified

    def poll(self):
        with self._poll_lock:
            return self._poll()

    def _poll(self):
        with self._lock:
            desired = copy.deepcopy(self._desired)
        wanted = {request["lease_id"] for request in desired}
        for lease_id in tuple(self._owned):
            if lease_id not in wanted:
                owned = self._owned[lease_id]
                owned["ending"] = True
                if self._release(lease_id, owned):
                    del self._owned[lease_id]
        output = []
        for request in desired:
            lease_id = request["lease_id"]
            started = self.clock()
            if lease_id not in self._owned:
                if len(self._owned) >= 3:
                    output.append({**request, "response": {"status": "unavailable",
                                   "reason": "region_capture_owner_budget_busy"},
                                   "request_age_s": 0, "received_at": started})
                    continue
                self._owned[lease_id] = {"renew_at": 0, "terminal": None,
                                         "ending": False, "release_retry_at": 0,
                                         "cleanup_verified": False}
            owned = self._owned[lease_id]
            if owned["ending"]:
                verified = self._release(lease_id, owned)
                output.append({**request, "response": {"status": "unavailable",
                               "reason": "region_capture_released" if verified else "region_capture_release_pending"},
                               "request_age_s": self.clock() - started, "received_at": self.clock()})
                continue
            result = owned["terminal"]
            if result is None and started >= owned["renew_at"]:
                result = self.client.region_capture("POST", lease_id, request["body"])
                self.notify({"event": "radio_region_capture_admission", "task_id": request["task_id"],
                             "version": request["version"], "lease_id": lease_id,
                             "status": result.get("status"), "reason": result.get("reason"),
                             "latency_s": self.clock() - started})
                if result.get("status") == "available":
                    owned["renew_at"] = self.clock() + 10
                elif result.get("status_code") in (401, 403, 404, 409, 422):
                    owned["terminal"] = result
            if owned["terminal"] is None and self.clock() < owned["renew_at"]:
                result = self.client.region_capture("GET", lease_id)
                if result.get("status_code") in (401, 403, 404, 409, 422):
                    owned["terminal"] = result
            with self._lock:
                still_wanted = any(item["lease_id"] == lease_id for item in self._desired)
            if not still_wanted:
                owned["ending"] = True
                if self._release(lease_id, owned):
                    del self._owned[lease_id]
                continue
            output.append({**request, "response": result,
                           "request_age_s": self.clock() - started, "received_at": self.clock()})
        return output

    def close(self):
        self.replace([])
        with self._poll_lock:
            self._poll()
            result = {"cleanup_verified": not self._owned, "pending_lease_ids": list(self._owned)}
        self.notify({"event": "radio_region_capture_cleanup", **result})
        return result
