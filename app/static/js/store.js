/* Local-first data store + sync engine (offline editing).
 *
 * Pages read/write department data through window.Store, which persists to
 * IndexedDB (via Dexie) and queues an "outbox" of pending changes. The engine
 * pushes the outbox and pulls the delta from POST /api/sync on reconnect,
 * app-foreground, a manual trigger, and a light interval. Conflicts detected by
 * the server are held for the user to resolve (keep mine / keep theirs).
 *
 * The record shape per entity mirrors the server's sync serialization:
 *   { uuid, id, updated_at, parent_uuid?, ...fields }
 * `updated_at` is the server's last-known version, used as the optimistic-
 * concurrency base for the next edit. Timestamps are opaque strings.
 */
window.Store = (function () {
  "use strict";

  var ENTITIES = ["occupancy", "hydrant", "map_feature", "contact", "hazard"];
  var CHILD_OF = { map_feature: "occupancy", contact: "occupancy", hazard: "occupancy" };
  var META_KEYS = ["uuid", "id", "updated_at", "parent_uuid"];

  var db = new Dexie("preplanner");
  db.version(1).stores({
    occupancy: "uuid",
    hydrant: "uuid",
    map_feature: "uuid, parent_uuid",
    contact: "uuid, parent_uuid",
    hazard: "uuid, parent_uuid",
    outbox: "++id, [entity+uuid]",
    conflicts: "++id, [entity+uuid]",
    meta: "key"
  });

  var state = { user: null, syncing: false, needsLogin: false, status: {} };
  var dataListeners = [], statusListeners = [];
  var syncTimer = null;
  var readyResolve;
  var ready = new Promise(function (r) { readyResolve = r; });

  // --- meta ------------------------------------------------------------------
  function getMeta() {
    return db.meta.toArray().then(function (rows) {
      var m = {}; rows.forEach(function (r) { m[r.key] = r.value; }); return m;
    });
  }
  function setMeta(obj) {
    var puts = Object.keys(obj).map(function (k) { return db.meta.put({ key: k, value: obj[k] }); });
    return Promise.all(puts);
  }

  // --- public read/write -----------------------------------------------------
  function list(entity) { return db[entity].toArray(); }
  function get(entity, uuid) { return db[entity].get(uuid); }

  function create(entity, data, parent_uuid) {
    var uuid = (crypto.randomUUID && crypto.randomUUID()) || _uuid();
    var rec = Object.assign({ uuid: uuid, id: null, updated_at: null }, data);
    if (parent_uuid) rec.parent_uuid = parent_uuid;
    return db[entity].put(rec)
      .then(function () { return enqueue(entity, "create", uuid, data, null, parent_uuid); })
      .then(function () { changed(); return rec; });
  }

  function update(entity, uuid, data) {
    return db[entity].get(uuid).then(function (rec) {
      if (!rec) return null;
      Object.assign(rec, data);
      return db[entity].put(rec)
        .then(function () { return enqueue(entity, "update", uuid, data, rec.updated_at); })
        .then(function () { changed(); return rec; });
    });
  }

  function remove(entity, uuid) {
    return db[entity].get(uuid).then(function (rec) {
      return db[entity].delete(uuid)
        .then(function () { return enqueue(entity, "delete", uuid, null, rec ? rec.updated_at : null); })
        .then(function () { changed(); });
    });
  }

  // --- outbox with coalescing ------------------------------------------------
  function enqueue(entity, op, uuid, data, base, parent_uuid) {
    return db.outbox.where({ entity: entity, uuid: uuid }).toArray().then(function (pend) {
      if (op === "create") {
        return db.outbox.add({ entity: entity, op: "create", uuid: uuid,
          data: data || {}, base_updated_at: null, parent_uuid: parent_uuid || null });
      }
      if (op === "update") {
        var create = pend.find(function (e) { return e.op === "create"; });
        if (create) { create.data = Object.assign({}, create.data, data); return db.outbox.put(create); }
        var upd = pend.find(function (e) { return e.op === "update"; });
        if (upd) { upd.data = Object.assign({}, upd.data, data); return db.outbox.put(upd); }
        return db.outbox.add({ entity: entity, op: "update", uuid: uuid, data: data || {}, base_updated_at: base });
      }
      // delete: drop any pending create/update; a pending create means it never
      // reached the server, so create+delete cancels out entirely.
      var hadCreate = pend.some(function (e) { return e.op === "create"; });
      return db.outbox.bulkDelete(pend.map(function (e) { return e.id; })).then(function () {
        if (hadCreate) return;
        return db.outbox.add({ entity: entity, op: "delete", uuid: uuid, base_updated_at: base });
      });
    });
  }

  // --- sync engine -----------------------------------------------------------
  function sync() {
    if (state.syncing) return Promise.resolve();
    if (!navigator.onLine) { emitStatus(); return Promise.resolve(); }
    state.syncing = true; emitStatus();
    var pushed;
    return db.outbox.orderBy("id").toArray().then(function (rows) {
      pushed = rows;
      return getMeta();
    }).then(function (meta) {
      return fetch("/api/sync", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ops: pushedOps(pushed), last_synced_at: meta.last_synced_at || null })
      });
    }).then(function (res) {
      if (res.status === 401) { state.needsLogin = true; return null; }
      state.needsLogin = false;
      if (!res.ok) return null;
      return res.json();
    }).then(function (out) {
      if (out) return applyResponse(out, pushed);
    }).catch(function () {
      /* network error → stay offline, keep outbox */
    }).then(function () {
      state.syncing = false; changed(); return null;
    });
  }

  function pushedOps(rows) {
    return rows.map(function (e) {
      return { entity: e.entity, op: e.op, uuid: e.uuid, data: e.data,
               base_updated_at: e.base_updated_at, parent_uuid: e.parent_uuid };
    });
  }

  function applyResponse(out, pushed) {
    var chain = Promise.resolve();
    // 1. creates/updates that succeeded: adopt server id + updated_at.
    (out.applied || []).forEach(function (a) {
      if (a.deleted) return;
      chain = chain.then(function () {
        return db[a.entity].get(a.uuid).then(function (rec) {
          if (rec) { rec.id = a.id; rec.updated_at = a.updated_at; return db[a.entity].put(rec); }
        });
      });
    });
    // 2. drop the ops we pushed (processed — applied, conflicted, or gone).
    chain = chain.then(function () { return db.outbox.bulkDelete(pushed.map(function (e) { return e.id; })); });
    // 3. record conflicts for the user to resolve.
    (out.conflicts || []).forEach(function (c) {
      if (c.reason) return; // e.g. missing_parent — nothing to resolve interactively
      chain = chain.then(function () {
        return db[c.entity].get(c.uuid).then(function (mine) {
          return db.conflicts.add({ entity: c.entity, uuid: c.uuid, mine: mine, theirs: c.server, at: Date.now() });
        });
      });
    });
    // 4. apply pulled changes — but never clobber a record with a still-pending
    //    local edit or an open conflict.
    Object.keys(out.changes || {}).forEach(function (entity) {
      out.changes[entity].forEach(function (rec) {
        chain = chain.then(function () {
          return Promise.all([
            db.outbox.where({ entity: entity, uuid: rec.uuid }).count(),
            db.conflicts.where({ entity: entity, uuid: rec.uuid }).count()
          ]).then(function (counts) {
            if (counts[0] === 0 && counts[1] === 0) return db[entity].put(rec);
          });
        });
      });
    });
    // 5. apply deletions (unless a local edit is pending).
    (out.deletions || []).forEach(function (d) {
      chain = chain.then(function () {
        return db.outbox.where({ entity: d.entity, uuid: d.uuid }).count().then(function (n) {
          if (!n) return db[d.entity].delete(d.uuid);
        });
      });
    });
    // 6. advance the watermark.
    return chain.then(function () { return setMeta({ last_synced_at: out.server_time }); });
  }

  // --- conflicts -------------------------------------------------------------
  function listConflicts() { return db.conflicts.toArray(); }

  function resolveConflict(id, choice) {
    return db.conflicts.get(id).then(function (c) {
      if (!c) return;
      var work;
      if (choice === "theirs") {
        work = c.theirs ? db[c.entity].put(c.theirs) : db[c.entity].delete(c.uuid);
      } else { // keep mine: re-apply locally and re-queue an update on top of theirs
        var base = c.theirs ? c.theirs.updated_at : null;
        var data = Object.assign({}, c.mine);
        META_KEYS.forEach(function (k) { delete data[k]; });
        var rec = Object.assign({}, c.mine, { updated_at: base });
        work = db[c.entity].put(rec).then(function () {
          return db.outbox.add({ entity: c.entity, op: "update", uuid: c.uuid, data: data, base_updated_at: base });
        });
      }
      return work.then(function () { return db.conflicts.delete(id); }).then(function () { changed(); scheduleSync(); });
    });
  }

  // --- status + notifications ------------------------------------------------
  function subscribe(fn) { dataListeners.push(fn); return function () { dataListeners = dataListeners.filter(function (f) { return f !== fn; }); }; }
  function onStatus(fn) { statusListeners.push(fn); if (state.status) fn(state.status); }
  function changed() { dataListeners.forEach(function (f) { try { f(); } catch (e) {} }); emitStatus(); }

  function emitStatus() {
    Promise.all([db.outbox.count(), db.conflicts.count(), getMeta()]).then(function (r) {
      state.status = { online: navigator.onLine, syncing: state.syncing,
        pending: r[0], conflicts: r[1], lastSyncedAt: r[2].last_synced_at || null,
        needsLogin: state.needsLogin };
      statusListeners.forEach(function (f) { try { f(state.status); } catch (e) {} });
    });
  }

  function scheduleSync() { clearTimeout(syncTimer); syncTimer = setTimeout(sync, 800); }

  // --- lifecycle -------------------------------------------------------------
  function wipe() {
    return Promise.all(ENTITIES.concat(["outbox", "conflicts", "meta"]).map(function (t) { return db[t].clear(); }));
  }

  function init(user) {
    return db.open().then(function () {
      if (!user) { return wipe().then(function () { readyResolve(); }); }
      return getMeta().then(function (meta) {
        var switched = meta.user && meta.user.id !== user.id;
        return (switched ? wipe() : Promise.resolve())
          .then(function () { return setMeta({ user: user }); });
      }).then(function () {
        state.user = user;
        window.addEventListener("online", function () { sync(); });
        window.addEventListener("offline", emitStatus);
        document.addEventListener("visibilitychange", function () {
          if (document.visibilityState === "visible") sync();
        });
        setInterval(function () { if (navigator.onLine) sync(); }, 60000);
        readyResolve();
        return sync();
      });
    });
  }

  function _uuid() {  // fallback if crypto.randomUUID is unavailable
    return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, function (c) {
      var r = Math.random() * 16 | 0, v = c === "x" ? r : (r & 0x3 | 0x8);
      return v.toString(16);
    });
  }

  // Auto-init from the user injected by base.html.
  document.addEventListener("DOMContentLoaded", function () {
    init(window.CURRENT_USER || null);
  });

  return {
    ready: ready, list: list, get: get, create: create, update: update, remove: remove,
    sync: sync, wipe: wipe, subscribe: subscribe, onStatus: onStatus,
    conflicts: listConflicts, resolveConflict: resolveConflict,
    status: function () { return state.status; },
    CHILD_OF: CHILD_OF
  };
})();
