from __future__ import annotations

import copy
import re
import threading
import time
import uuid


class RadioDetailDemands:
    def __init__(self, client, *, clock=time.monotonic, notify=None):
        self.client = client
        self.clock = clock
        self.notify = notify or (lambda event: None)
        self._lock = threading.Lock()
        self._poll_lock = threading.Lock()
        self._desired = []
        self._owned = {}

    @staticmethod
    def _valid(request):
        if (not isinstance(request, dict)
                or set(request) != {"task_id", "version", "session_id", "scope", "opponent"}
                or any(type(request.get(key)) is not int or request[key] < 1
                       for key in ("task_id", "version"))
                or not isinstance(request.get("session_id"), str)
                or re.fullmatch(r"[a-f0-9]{32}", request["session_id"]) is None):
            return False
        scope, opponent = request["scope"], request["opponent"]
        return (isinstance(scope, dict) and set(scope) == {"session_uid", "packet_format", "epoch"}
                and type(scope["session_uid"]) is int and scope["session_uid"] > 0
                and type(scope["epoch"]) is int and scope["epoch"] >= 0
                and type(scope["packet_format"]) is int and scope["packet_format"] in (2025, 2026)
                and isinstance(opponent, dict)
                and set(opponent) == {"car_index", "tenure_ordinal", "participant_identity_fingerprint"}
                and type(opponent["car_index"]) is int and 0 <= opponent["car_index"] < 24
                and type(opponent["tenure_ordinal"]) is int and opponent["tenure_ordinal"] >= 1
                and isinstance(opponent["participant_identity_fingerprint"], str)
                and re.fullmatch(r"[a-f0-9]{64}", opponent["participant_identity_fingerprint"]) is not None)

    def replace(self, requests):
        copied = copy.deepcopy(requests)
        if (not isinstance(copied, list) or len(copied) > 4
                or any(not self._valid(item) for item in copied)
                or len({item["task_id"] for item in copied}) != len(copied)):
            raise ValueError("detail_descriptors_invalid")
        with self._lock:
            self._desired = copied

    def _wanted(self, request):
        with self._lock:
            return request in self._desired

    @staticmethod
    def _binding(request, response):
        rows = response.get("data") if response.get("status") == "available" else None
        if not isinstance(rows, list) or len(rows) > 24:
            return None
        matches = []
        for row in rows:
            if not isinstance(row, dict) or row.get("is_player") is not False:
                continue
            expected = {**request["opponent"], "epoch": request["scope"]["epoch"],
                        "packet_format": request["scope"]["packet_format"]}
            if all(type(row.get(key)) is type(value) and row[key] == value
                   for key, value in expected.items()):
                matches.append(row.get("binding_id"))
        if (len(matches) != 1 or not isinstance(matches[0], str)
                or re.fullmatch(r"[a-f0-9]{64}", matches[0]) is None):
            return None
        return matches[0]

    @staticmethod
    def _snapshot(owned, response):
        data = response.get("data") if response.get("status") == "available" else None
        if (not isinstance(data, dict)
                or any(data.get(key) != value for key, value in owned["body"].items())
                or not isinstance(data.get("state"), str)
                or data.get("state") not in {"queued", "active", "released", "expired", "rejected"}):
            return None
        if data["state"] == "active" and (
                type(data.get("activation_ordinal")) is not int or data["activation_ordinal"] < 1
                or type(data.get("activation_frame_ordinal")) is not int
                or data["activation_frame_ordinal"] < 0):
            return None
        return data

    def _release(self, owned):
        if not owned["posted"] or owned["terminal"]:
            return True
        response = self.client.detail_task("GET", owned["body"]["task_id"])
        data = self._snapshot(owned, response)
        if data and data["state"] in {"released", "expired", "rejected"}:
            return True
        if not owned["release_sent"]:
            result = self.client.detail_task("DELETE", owned["body"]["task_id"])
            owned["release_sent"] = True
            self.notify({"event": "radio_detail_release_requested", "detail_task_id": owned["body"]["task_id"],
                         "status": result.get("status"), "cleanup_verified": False})
        return False

    def poll(self):
        with self._poll_lock:
            return self._poll()

    def _poll(self):
        with self._lock:
            desired = copy.deepcopy(self._desired)
        for owner_id, owned in tuple(self._owned.items()):
            if owned["request"] not in desired or owned["ending"]:
                owned["ending"] = True
                if self._release(owned):
                    if owned["request"] not in desired:
                        del self._owned[owner_id]
                    else:
                        owned["terminal"] = "detail_identity_unavailable"
        targets = {}
        output = []
        for request in desired:
            if not self._wanted(request):
                continue
            owned = next((item for item in self._owned.values() if item["request"] == request), None)
            if owned is None:
                if len(self._owned) >= 4:
                    output.append({**request, "state": "unavailable", "reason": "detail_owner_budget_busy"})
                    continue
                owner_id = "radio_" + uuid.uuid4().hex
                owned = {"request": request, "body": {"task_id": owner_id, "session_id": request["session_id"]},
                         "posted": False, "release_sent": False, "terminal": None,
                         "ending": False, "renew_at": 0, "activation": None,
                         "renewal_token": None, "admitted_at": None}
                self._owned[owner_id] = owned
            state, reason = "waiting", owned["terminal"]
            if owned["terminal"] or owned["ending"]:
                state = "unavailable"
                reason = reason or "detail_release_pending"
            else:
                session = request["session_id"]
                if session not in targets:
                    targets[session] = self.client.detail_targets(session)
                binding = self._binding(request, targets[session])
                if targets[session].get("status") != "available":
                    state, reason = "unavailable", "detail_targets_unverified"
                elif binding is None or (owned["posted"] and binding != owned["body"]["binding_id"]):
                    reason = "detail_identity_unavailable"
                    if owned["posted"]:
                        owned["ending"] = True
                        self._release(owned)
                    elif targets[session].get("status") == "available":
                        owned["terminal"] = reason
                    state = "unavailable"
                else:
                    owned["body"]["binding_id"] = binding
                    if not owned["posted"] and self._wanted(request):
                        owned["posted"] = True
                        owned["admitted_at"] = self.clock()
                        result = self.client.detail_task("POST", owned["body"]["task_id"], owned["body"])
                        self.notify({"event": "radio_detail_admission", "task_id": request["task_id"],
                                     "version": request["version"], "detail_task_id": owned["body"]["task_id"],
                                     "status": result.get("status"),
                                     "latency_s": self.clock() - owned["admitted_at"]})
                        owned["renew_at"] = self.clock() + 120
                        if result.get("status_code") in (401, 403, 404, 409, 422):
                            owned["terminal"] = "detail_admission_rejected"
                    data = self._snapshot(owned, self.client.detail_task("GET", owned["body"]["task_id"]))
                    if owned["terminal"]:
                        state, reason = "unavailable", owned["terminal"]
                    elif data is None:
                        state, reason = "unavailable", "detail_status_unverified"
                    elif data["state"] in {"released", "expired", "rejected"}:
                        owned["terminal"] = "detail_" + data["state"]
                        state, reason = "unavailable", owned["terminal"]
                    elif data["state"] == "active" and data.get("last_command_state") == "applied":
                        activation = (data["activation_ordinal"], data["activation_frame_ordinal"])
                        if owned["activation"] is not None and owned["activation"] != activation:
                            owned["ending"] = True
                            state, reason = "unavailable", "detail_activation_changed"
                            self._release(owned)
                        else:
                            if owned["activation"] is None:
                                self.notify({"event": "radio_detail_activated", "task_id": request["task_id"],
                                             "version": request["version"],
                                             "detail_task_id": owned["body"]["task_id"],
                                             "latency_s": self.clock() - owned["admitted_at"]})
                            owned["activation"] = activation
                            token = (data.get("effective_sequence"), data.get("expires_at"))
                            if owned["renewal_token"] is not None and owned["renewal_token"] != token:
                                owned["renewal_token"] = None
                                owned["renew_at"] = self.clock() + 120
                            state = "active" if owned["renewal_token"] is None else "waiting"
                            if owned["renewal_token"] is not None:
                                reason = "detail_renewal_pending"
                            elif self.clock() >= owned["renew_at"] and self._wanted(request):
                                owned["renewal_token"] = token
                                self.client.detail_task("POST", owned["body"]["task_id"], owned["body"])
                                owned["renew_at"] = self.clock() + 120
                                state, reason = "waiting", "detail_renewal_pending"
                    elif data.get("last_command_state") == "rejected":
                        owned["ending"] = True
                        state, reason = "unavailable", "detail_command_rejected"
                        self._release(owned)
            if not self._wanted(request):
                owned["ending"] = True
                self._release(owned)
                continue
            output.append({**copy.deepcopy(request), "detail_task_id": owned["body"]["task_id"],
                           "binding_id": owned["body"].get("binding_id"),
                           "activation": owned["activation"], "state": state, "reason": reason})
        return output

    def close(self):
        self.replace([])
        with self._poll_lock:
            self._poll()
            result = {"cleanup_verified": not self._owned, "pending_detail_task_ids": list(self._owned)}
        self.notify({"event": "radio_detail_cleanup", **result})
        return result
