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
        self._desired = []
        self._owned = {}

    def replace(self, requests):
        with self._lock:
            self._desired = copy.deepcopy(requests)

    def _release(self, lease_id):
        result = self.client.region_capture("DELETE", lease_id)
        self.notify({"event": "radio_region_capture_released", "lease_id": lease_id,
                     "status": result.get("status"), "reason": result.get("reason")})

    def poll(self):
        with self._lock:
            desired = copy.deepcopy(self._desired)
        wanted = {request["lease_id"] for request in desired}
        for lease_id in tuple(self._owned):
            if lease_id not in wanted:
                self._release(lease_id)
                del self._owned[lease_id]
        output = []
        for request in desired:
            lease_id = request["lease_id"]
            owned = self._owned.setdefault(lease_id, {"renew_at": 0, "terminal": None})
            started = self.clock()
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
                self._release(lease_id)
                del self._owned[lease_id]
                continue
            output.append({**request, "response": result,
                           "request_age_s": self.clock() - started, "received_at": self.clock()})
        return output

    def close(self):
        self.replace([])
        for lease_id in tuple(self._owned):
            self._release(lease_id)
            del self._owned[lease_id]
