"""Offline-first catalog and immutable Drive journal (community preview).

Drive is not a transactional SQL server. Each change is a separate immutable
JSON file, not an overwrite of a shared database file. Concurrent branches are
three-way merged; overlapping changes remain recoverable and block editing that
record until the user explicitly resolves them. SQLite is only a local cache.
"""
from __future__ import annotations

from copy import deepcopy
from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from threading import RLock
from types import SimpleNamespace
from uuid import uuid4
import hashlib
from database.catalog_rules import baseline_scope, baseline_id, event_rank, changed_leaves, apply_path, catalog_event_name

TABLES = frozenset({"drivers", "chassis", "tracks", "setups", "inventory_items",
                   "inventory_item_revisions", "inventory_movements", "app_docs"})
SYSTEM_FIELDS = {"updated_at", "revision"}
_MISSING = object()


class CatalogConflict(RuntimeError):
    pass


def _now():
    return datetime.now(timezone.utc).isoformat()


def merge(base, current, incoming, path=""):
    """Merge independent edits; return field paths for overlapping changes."""
    if current == incoming or incoming == base:
        return deepcopy(current) if current is not _MISSING else _MISSING, []
    if current == base:
        return deepcopy(incoming) if incoming is not _MISSING else _MISSING, []
    if all(isinstance(value, dict) for value in (base, current, incoming)):
        result, conflicts = {}, []
        for key in sorted(set(base) | set(current) | set(incoming)):
            field_path = f"{path}.{key}" if path else key
            if not path and key in SYSTEM_FIELDS:
                result[key] = deepcopy(incoming.get(key, current.get(key)))
                continue
            value, overlap = merge(base.get(key, _MISSING), current.get(key, _MISSING),
                                   incoming.get(key, _MISSING), field_path)
            if value is not _MISSING:
                result[key] = value
            conflicts.extend(overlap)
        return result, conflicts
    # Stable IDs permit independent additions/edits in runs and audit history.
    if all(isinstance(value, list) for value in (base, current, incoming)) and all(
            isinstance(item, dict) and item.get("id")
            for values in (base, current, incoming) for item in values):
        mapped = [{item["id"]: item for item in values}
                  for values in (base, current, incoming)]
        values, conflicts = merge(*mapped, path)
        order = list(dict.fromkeys(item["id"] for items in (current, incoming) for item in items))
        return [values[key] for key in order if key in values], conflicts
    return deepcopy(current) if current is not _MISSING else _MISSING, [path or "record"]


class DriveStore:
    def __init__(self, path: Path, transport=None):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = RLock()
        self.sync_lock = RLock()
        self.transport = transport
        self.last_sync_error = None
        with self.connection() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS records (
                    collection TEXT, key TEXT, body TEXT,
                    PRIMARY KEY(collection, key));
                CREATE TABLE IF NOT EXISTS events (
                    id TEXT PRIMARY KEY, body TEXT NOT NULL,
                    uploaded INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS conflicts (
                    event_id TEXT PRIMARY KEY, collection TEXT, key TEXT,
                    body TEXT NOT NULL);
            """)

    @contextmanager
    def connection(self):
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def table(self, name):
        if name not in TABLES:
            raise ValueError(f"Unknown catalog collection: {name}")
        return Query(self, name)

    def rows(self, connection, table):
        return [json.loads(row[0]) for row in connection.execute(
            "SELECT body FROM records WHERE collection=? ORDER BY key", (table,))]

    def _events(self, connection):
        return [json.loads(row[0]) for row in connection.execute("SELECT body FROM events")]

    def _append(self, connection, table, before, after, resolves=(), parents=None):
        key = (after or before)["key" if table == "app_docs" else "id"]
        if not resolves and connection.execute(
                "SELECT 1 FROM conflicts WHERE collection=? AND key=?", (table, key)).fetchone():
            raise CatalogConflict("This record has conflicting team edits. Resolve it in Settings before saving.")
        related = [event for event in self._events(connection)
                   if event["table"] == table and event["key"] == key]
        referenced = {parent for event in related for parent in event["parents"]}
        event = {"schema": 1, "id": str(uuid4()), "table": table, "key": key,
                 "parents": parents if parents is not None else [event["id"] for event in related if event["id"] not in referenced],
                 "before": before, "after": after, "created_at": _now(),
                 "resolves": list(resolves)}
        connection.execute("INSERT INTO events(id,body) VALUES (?,?)",
                           (event["id"], json.dumps(event)))
        if after is None:
            connection.execute("DELETE FROM records WHERE collection=? AND key=?", (table, key))
        else:
            connection.execute("INSERT OR REPLACE INTO records VALUES (?,?,?)",
                               (table, key, json.dumps(after)))
        return event

    def _rebuild(self, connection):
        events = {event["id"]: event for event in self._events(connection)}
        done, state, conflicts = set(), {}, {}
        ancestors, part_clocks, part_definitions, part_notices = {}, {}, {}, {}
        while len(done) < len(events):
            ready = sorted(event_id for event_id, event in events.items()
                           if event_id not in done and set(event["parents"]) <= done)
            if not ready:
                raise ValueError("Drive journal has missing parents or a cycle; local cache was not replaced.")
            for event_id in ready:
                event = events[event_id]
                ancestors[event_id] = set(event["parents"])
                for parent in event["parents"]:
                    ancestors[event_id].update(ancestors[parent])
                slot = (event["table"], event["key"])
                current = state.get(slot)
                value, overlap = merge(event["before"], current, event["after"])
                part_edit = (event["table"] == "inventory_items" and event["after"] is not None
                             and (event["before"] is None or event["after"].get("revision") != event["before"].get("revision")))
                if part_edit:
                    part_definitions.setdefault(slot, []).append(event)
                # Definitions merge leaf-by-leaf. Provenance compares causal order
                # first, then the recorded local timestamp and immutable event ID.
                if (event["table"] == "inventory_items"
                        and all(isinstance(row, dict) for row in (event["before"], current, event["after"]))
                        and event["after"].get("quantity") == event["before"].get("quantity")
                        and event["after"].get("archived_at") == event["before"].get("archived_at")
                        and event["after"].get("category") == current.get("category")
                        and not event.get("resolves")):
                    value = deepcopy(current)
                    clocks = part_clocks.setdefault(slot, {})
                    for path in changed_leaves(event["before"], event["after"]):
                        if not path or path[0] in {"id", "created_at", "created_by", "revision", "revision_id", "updated_at", "updated_by", "_merge_notice"}:
                            continue
                        related = [previous for other_path, previous in clocks.items()
                                   if path[:len(other_path)] == other_path or other_path[:len(path)] == path]
                        previous = max(related, key=event_rank) if related else None
                        if previous and previous["id"] not in ancestors[event_id]:
                            part_notices.setdefault(slot, set()).add(".".join(path))
                        if (not previous or previous["id"] in ancestors[event_id]
                                or event_rank(event) > event_rank(previous)):
                            value = apply_path(value, event["after"], path)
                            clocks[path] = event
                    newest = max([event, *clocks.values()], key=event_rank)
                    for field in ("updated_at", "updated_by"):
                        if field in newest["after"]:
                            value[field] = newest["after"][field]
                    overlap = []
                if (event["table"] == "inventory_items"
                        and all(isinstance(row, dict) for row in (event["before"], current, event["after"]))
                        and event["after"].get("quantity") != event["before"].get("quantity")
                        and current.get("quantity") != event["before"].get("quantity")):
                    overlap = list(dict.fromkeys([*overlap, "quantity"]))
                if overlap and not event.get("resolves"):
                    conflicts[event_id] = {"event": event, "fields": overlap, "current": current}
                else:
                    state[slot] = deepcopy(event["after"] if event.get("resolves") else value)
                    for resolved in event.get("resolves", []):
                        if resolved in conflicts and (conflicts[resolved]["event"]["table"],
                                                     conflicts[resolved]["event"]["key"]) == slot:
                            conflicts.pop(resolved)
                done.add(event_id)
        for slot, definitions in part_definitions.items():
            current = state.get(slot)
            if not current:
                continue
            heads = [event for event in definitions if not any(
                event["id"] in ancestors[other["id"]] for other in definitions if other is not event)]
            if any(detail["event"]["table"] == slot[0] and detail["event"]["key"] == slot[1]
                   for detail in conflicts.values()):
                continue
            if len(heads) < 2:
                if heads:
                    current["revision_id"] = heads[0]["after"].get("revision_id") or "event:" + heads[0]["id"]
                    current["revision"] = heads[0]["after"].get("revision", 1)
                continue
            identity = "merge:" + hashlib.sha256("|".join(sorted(event["id"] for event in heads)).encode()).hexdigest()
            current["revision_id"] = identity
            current["revision"] = max(len(definitions), *(event["after"].get("revision", 1) for event in heads))
            current["_merge_notice"] = {"source_revisions": sorted(event["after"].get("revision_id") or "event:" + event["id"] for event in heads),
                                        "overlapping_fields": sorted(part_notices.get(slot, set())),
                                        "message": "Concurrent edits combined; original revisions are preserved. Overlapping fields use the newest recorded local change."}
            snapshot = deepcopy(current)
            newest_definition = max(heads, key=event_rank)["after"]
            # Stock movements are not definition revisions. They must not mutate
            # the immutable merged snapshot or create same-ID/different-body events.
            for field in ("quantity", "updated_at", "updated_by"):
                if field in newest_definition:
                    snapshot[field] = deepcopy(newest_definition[field])
                else:
                    snapshot.pop(field, None)
            state[("inventory_item_revisions", identity)] = {"id": identity, "item_id": current["id"],
                "revision": current["revision"], "snapshot": snapshot, "created_at": snapshot.get("updated_at"),
                "created_by": snapshot.get("updated_by"), "source_revisions": current["_merge_notice"]["source_revisions"]}
        connection.execute("DELETE FROM records")
        connection.execute("DELETE FROM conflicts")
        connection.executemany("INSERT INTO records VALUES (?,?,?)",
                               [(table, key, json.dumps(value)) for (table, key), value in state.items()
                                if value is not None])
        connection.executemany("INSERT INTO conflicts VALUES (?,?,?,?)", [
            (event_id, detail["event"]["table"], detail["event"]["key"], json.dumps(detail))
            for event_id, detail in conflicts.items()])

    def pending_count(self):
        with self.connection() as connection:
            return connection.execute("SELECT count(*) FROM events WHERE uploaded=0").fetchone()[0]

    def baseline_duplicates(self):
        """Return legacy duplicate groups for explicit review, never discard data."""
        with self.lock, self.connection() as connection:
            groups = {}
            for row in self.rows(connection, "setups"):
                scope = baseline_scope(row)
                if scope is not None:
                    groups.setdefault(scope, []).append(row)
            return [rows for rows in groups.values() if len(rows) > 1]

    def consolidate_baselines(self, selected_id, expected):
        """Keep the reviewed variant; tombstones preserve every original journal entry."""
        with self.lock, self.connection() as connection:
            selected = next((row for row in expected if row["id"] == selected_id), None)
            if not selected or baseline_scope(selected) is None:
                raise ValueError("Choose a baseline from the reviewed group.")
            current = [row for row in self.rows(connection, "setups")
                       if baseline_scope(row) == baseline_scope(selected)]
            if sorted(current, key=lambda row: row["id"]) != sorted(expected, key=lambda row: row["id"]):
                raise CatalogConflict("These baselines changed during review. Refresh before consolidating.")
            for row in current:
                if row["id"] != selected_id:
                    self._append(connection, "setups", row, None)

    def queue_items(self):
        from offline_store import PendingSyncItem
        items = []
        with self.connection() as connection:
            for row in connection.execute("SELECT id,body FROM events WHERE uploaded=0 ORDER BY id"):
                event = json.loads(row["body"])
                items.append(PendingSyncItem(f"catalog:{event['id']}", catalog_event_name(event),
                                             "Catalog change", "Google Drive", error=self.last_sync_error))
        for conflict in self.conflicts():
            event = conflict["event"]
            items.append(PendingSyncItem(f"conflict:{event['id']}", catalog_event_name(event),
                                         "Conflicting edit", "Google Drive",
                                         error="Review this shared edit in Settings. Both versions have been preserved."))
        return items

    def conflicts(self):
        with self.connection() as connection:
            result = [json.loads(row[0]) for row in connection.execute("SELECT body FROM conflicts")]
            for entry in result:
                event = entry["event"]
                current = connection.execute("SELECT body FROM records WHERE collection=? AND key=?",
                                             (event["table"], event["key"])).fetchone()
                entry["current"] = json.loads(current[0]) if current else None
            return result

    def resolve(self, table, key, value, expected=_MISSING):
        with self.lock, self.connection() as connection:
            entries = [json.loads(row[0]) for row in connection.execute(
                "SELECT body FROM conflicts WHERE collection=? AND key=?", (table, key))]
            if not entries:
                raise ValueError("This conflict has already been resolved. Refresh the list.")
            current = next((row for row in self.rows(connection, table)
                            if row["key" if table == "app_docs" else "id"] == key), None)
            if expected is not _MISSING and current != expected:
                raise CatalogConflict("The team record changed while this comparison was open. Refresh and review it again.")
            if value is not None and value.get("key" if table == "app_docs" else "id") != key:
                raise ValueError("Resolution cannot change the record identity.")
            self._append(connection, table, current, value,
                         resolves=[entry["event"]["id"] for entry in entries])
            self._rebuild(connection)

    def sync(self, read_only=False):
        try:
            result = self._sync(read_only=read_only)
            self.last_sync_error = None
            return result
        except Exception as exc:
            self.last_sync_error = str(exc)
            raise

    def _sync(self, read_only=False):
        """Pull, append pending changes, pull again. Failed uploads stay durable."""
        from database.drive_transport import DriveJournal
        import config
        if self.transport is None:
            if not config.GOOGLE_DRIVE_FOLDER_ID:
                return {"pending": self.pending_count(), "conflicts": len(self.conflicts()), "local_only": True}
            self.transport = DriveJournal(config.GOOGLE_DRIVE_FOLDER_ID)
        # Serialize network passes separately. Local catalog reads/saves must
        # never wait for a slow Google request to release the database lock.
        with self.sync_lock:
            self._pull(persist_revisions=not read_only)
            if read_only:
                return {"pending": self.pending_count(), "conflicts": len(self.conflicts()), "local_only": False}
            with self.lock, self.connection() as connection:
                pending = [json.loads(row[0]) for row in connection.execute(
                    "SELECT body FROM events WHERE uploaded=0 ORDER BY id")]
            waiting = {event["id"]: event for event in pending}
            for event in self._ordered_pending(waiting):
                from qt.workers import check_interruption
                check_interruption()
                self.transport.append(event)
                with self.lock, self.connection() as connection:
                    connection.execute("UPDATE events SET uploaded=1 WHERE id=?", (event["id"],))
            self._pull()
        return {"pending": self.pending_count(), "conflicts": len(self.conflicts()), "local_only": False}

    @staticmethod
    def _ordered_pending(waiting):
        pending = dict(waiting)
        while pending:
            ready = sorted(key for key, event in pending.items() if not (set(event["parents"]) & pending.keys()))
            if not ready:
                raise ValueError("Catalog outbox contains a dependency cycle.")
            for key in ready:
                yield pending.pop(key)

    def _pull(self, persist_revisions=True):
        with self.connection() as connection:
            known = {row[0] for row in connection.execute("SELECT id FROM events")}
        incoming = self.transport.read_unknown(known)
        with self.lock, self.connection() as connection:
            for event in incoming:
                self._validate_event(event)
                connection.execute("INSERT OR IGNORE INTO events VALUES (?,?,1)",
                                   (event["id"], json.dumps(event)))
                connection.execute("UPDATE events SET uploaded=1 WHERE id=?", (event["id"],))
            self._rebuild(connection)
            if persist_revisions:
                recorded = {event["key"] for event in self._events(connection)
                            if event["table"] == "inventory_item_revisions"}
                for revision in self.rows(connection, "inventory_item_revisions"):
                    if str(revision["id"]).startswith("merge:") and revision["id"] not in recorded:
                        self._append(connection, "inventory_item_revisions", None, revision)

    def save_part_edit(self, item_id, payload, revision_id=None, revision=None):
        """Save from an identified historical definition without losing intervening edits."""
        with self.lock, self.connection() as connection:
            current = next((row for row in self.rows(connection, "inventory_items") if row["id"] == item_id), None)
            if not current:
                raise ValueError("This part no longer exists.")
            if current.get("revision_id") == revision_id or (not revision_id and current.get("revision") == revision):
                before = current
                parents = None
            else:
                matches = [row for row in self.rows(connection, "inventory_item_revisions") if row["item_id"] == item_id
                           and (row["id"] == revision_id if revision_id else row["revision"] == revision)]
                if len(matches) != 1:
                    raise CatalogConflict("This older revision is ambiguous. Reload the part before saving.")
                before = matches[0]["snapshot"]
                source_ids = before.get("_merge_notice", {}).get("source_revisions", [before.get("revision_id")])
                parents = [event["id"] for event in self._events(connection) if event["table"] == "inventory_items"
                           and (event.get("after") or {}).get("revision_id") in source_ids]
                if not parents:
                    raise CatalogConflict("This revision's history could not be verified. Reload the part before saving.")
            after = {**deepcopy(before), **deepcopy(payload), "id": item_id}
            after["quantity"] = current.get("quantity", 1)
            after["archived_at"] = current.get("archived_at")
            query = Query(self, "inventory_items")
            query._stamp(after, before)
            self._append(connection, "inventory_items", before, after, parents=parents)
            query._revision(connection, after)
            self._rebuild(connection)
            saved = next(row for row in self.rows(connection, "inventory_items") if row["id"] == item_id)
            return deepcopy(saved)

    @staticmethod
    def _validate_event(event):
        if not isinstance(event, dict) or event.get("schema") != 1 or event.get("table") not in TABLES:
            raise ValueError("Unsupported Drive journal record.")
        if not isinstance(event.get("id"), str) or not isinstance(event.get("key"), str):
            raise ValueError("Invalid Drive journal identity.")
        if not isinstance(event.get("parents"), list) or not all(isinstance(item, str) for item in event["parents"]):
            raise ValueError("Invalid Drive journal parents.")
        identity = "key" if event["table"] == "app_docs" else "id"
        for field in ("before", "after"):
            value = event.get(field)
            if value is not None and (not isinstance(value, dict) or value.get(identity) != event["key"]):
                raise ValueError("Drive journal record identity does not match its contents.")
        if event.get("before") is None and event.get("after") is None:
            raise ValueError("An empty Drive journal change is invalid.")

    def rpc(self, name, values):
        if name != "adjust_inventory_stock":
            raise ValueError(f"Unsupported operation: {name}")
        return StockAdjustment(self, values)


class Query:
    def __init__(self, store, table):
        self.store, self.name = store, table
        self.action, self.payload, self.filters = "select", None, []
        self.columns, self.sort, self.maximum = "*", None, None

    def select(self, columns="*"):
        self.columns = columns
        return self

    def eq(self, field, value):
        self.filters.append(lambda row: row.get(field) == value)
        return self

    def neq(self, field, value):
        self.filters.append(lambda row: row.get(field) != value)
        return self

    def like(self, field, pattern):
        import re
        expression = "".join(".*" if char == "%" else "." if char == "_"
                             else re.escape(char) for char in pattern)
        regex = re.compile(expression, re.DOTALL)
        self.filters.append(lambda row: isinstance(row.get(field), str)
                            and regex.fullmatch(row[field]) is not None)
        return self

    def is_(self, field, value):
        if value != "null":
            raise ValueError("Only null filtering is supported.")
        return self.eq(field, None)

    def order(self, field, desc=False):
        self.sort = field, desc
        return self

    def limit(self, count):
        self.maximum = count
        return self

    def insert(self, payload):
        self.action, self.payload = "insert", deepcopy(payload)
        return self

    def upsert(self, payload, on_conflict="id"):
        if on_conflict != ("key" if self.name == "app_docs" else "id"):
            raise ValueError("Unsupported conflict key.")
        self.action, self.payload = "upsert", deepcopy(payload)
        return self

    def update(self, payload):
        self.action, self.payload = "update", deepcopy(payload)
        return self

    def delete(self):
        self.action = "delete"
        return self

    def execute(self):
        with self.store.lock, self.store.connection() as connection:
            rows = [row for row in self.store.rows(connection, self.name)
                    if all(predicate(row) for predicate in self.filters)]
            if self.sort:
                field, desc = self.sort
                rows.sort(key=lambda row: (row.get(field) is not None, row.get(field)), reverse=desc)
            if self.maximum is not None:
                rows = rows[:self.maximum]
            if self.action == "select":
                if self.name == "setups":
                    for row in rows:
                        for table, field in (("drivers", "driver"), ("chassis", "chassis")):
                            row[field] = next((item for item in self.store.rows(connection, table)
                                               if item["id"] == row.get(f"{field}_id")), None)
                if self.columns != "*" and "(" not in self.columns:
                    rows = [{key: row.get(key) for key in self.columns.split(",")} for row in rows]
                return SimpleNamespace(data=deepcopy(rows))
            results = []
            payloads = self.payload if isinstance(self.payload, list) else [self.payload]
            if self.action in ("insert", "upsert"):
                for payload in payloads:
                    key_field = "key" if self.name == "app_docs" else "id"
                    after = deepcopy(payload)
                    if self.name == "setups" and baseline_scope(after) is not None and not after.get("id"):
                        after["id"] = baseline_id(after)
                    after.setdefault(key_field, str(uuid4()))
                    before = next((row for row in rows if row[key_field] == after[key_field]), None)
                    if before is not None and self.action == "insert":
                        raise ValueError("A record with that identity already exists.")
                    after = {**(before or {}), **after}
                    self._check_baseline_scope(connection, after)
                    self._stamp(after, before)
                    self.store._append(connection, self.name, before, after)
                    self._revision(connection, after)
                    results.append(after)
            else:
                if not self.filters:
                    raise ValueError("Unfiltered catalog writes are not allowed.")
                for before in rows:
                    after = {**before, **self.payload} if self.action == "update" else None
                    if after is not None:
                        self._check_baseline_scope(connection, after)
                        self._stamp(after, before)
                    self.store._append(connection, self.name, before, after)
                    if after is not None:
                        self._revision(connection, after)
                        results.append(after)
            return SimpleNamespace(data=deepcopy(results))

    def _check_baseline_scope(self, connection, after):
        if self.name != "setups" or baseline_scope(after) is None:
            return
        if any(row["id"] != after["id"] and baseline_scope(row) == baseline_scope(after)
               for row in self.store.rows(connection, "setups")):
            raise ValueError("A baseline already exists for this kart/track. Open the existing baseline to edit it.")

    def _stamp(self, after, before):
        after.setdefault("created_at", _now())
        after["updated_at"] = _now()
        if before and after["updated_at"] == before.get("updated_at"):
            from datetime import timedelta
            after["updated_at"] = (datetime.fromisoformat(after["updated_at"]) + timedelta(microseconds=1)).isoformat()
        if self.name == "inventory_items":
            after["revision"] = (before or {}).get("revision", 0) + 1
            after["revision_id"] = str(uuid4())
            after.pop("_merge_notice", None)
            after.setdefault("quantity", 1)

    def _revision(self, connection, after):
        if self.name != "inventory_items":
            return
        revision = {"id": after["revision_id"], "item_id": after["id"], "revision": after["revision"],
                    "snapshot": deepcopy(after), "created_at": after["updated_at"],
                    "created_by": after.get("updated_by") or after.get("created_by")}
        self.store._append(connection, "inventory_item_revisions", None, revision)


class StockAdjustment:
    def __init__(self, store, values):
        self.store, self.values = store, values

    def execute(self):
        from inventory_fields import MAX_INVENTORY_QUANTITY
        with self.store.lock, self.store.connection() as connection:
            item = next((row for row in self.store.rows(connection, "inventory_items")
                         if row["id"] == self.values["p_item_id"]), None)
            if item is None:
                raise ValueError("Part no longer exists.")
            quantity = round(item["quantity"] + self.values["p_delta"], 3)
            if not 0 <= quantity <= MAX_INVENTORY_QUANTITY:
                raise ValueError("Stock quantity is outside its allowed range.")
            after = {**item, "quantity": quantity, "updated_at": _now()}
            self.store._append(connection, "inventory_items", item, after)
            movement = {"id": str(uuid4()), "item_id": item["id"], "quantity_delta": self.values["p_delta"],
                        "quantity_before": item["quantity"],
                        "quantity_after": quantity, "reason": self.values["p_reason"],
                        "notes": self.values["p_notes"], "created_by": self.values["p_created_by"],
                        "created_at": _now()}
            self.store._append(connection, "inventory_movements", None, movement)
            return SimpleNamespace(data=[after])
