import json
import uuid


def enqueue(st, tx, kind, content, now, *, key=None, critical=False, versions=(), parent=None):
    existing = None
    if key and not critical:
        existing = tx.execute("SELECT * FROM inbox WHERE merge_key=? AND acked=0 AND critical=0", (key,)).fetchone()
    if existing:
        combined = sorted(set(json.loads(existing["versions"])) | set(versions))
        tx.execute("UPDATE inbox SET content=?,versions=?,parent=?,created=? WHERE event_id=?",
                   (content, json.dumps(combined), parent, now, existing["event_id"]))
        st.log(tx, "watcher", "event.aggregated", {"inbox_event_id": existing["event_id"],
                   "versions": combined, "kind": kind, "previous_parent": existing["parent"]}, parent=parent)
        return existing["event_id"]
    event_id = "env-" + uuid.uuid4().hex
    tx.execute("INSERT INTO inbox(event_id,kind,merge_key,content,versions,parent,created,critical) VALUES (?,?,?,?,?,?,?,?)",
               (event_id, kind, key, content, json.dumps(list(versions)), parent, now, int(critical)))
    st.log(tx, "watcher", "event.enqueued", {"inbox_event_id": event_id, "kind": kind,
               "content": content, "versions": list(versions), "critical": critical}, parent=parent)
    return event_id


def deliver(st, now, turn_id):
    """Persist message insertion AND ACK atomically before starting a model request."""
    with st.transaction() as tx:
        rows = tx.execute("SELECT * FROM inbox WHERE acked=0 AND (critical=1 OR created<=?) ORDER BY created,rowid",
                          (now - st.config.merge_window,)).fetchall()
        messages = st.meta("messages")
        for row in rows:
            event = st.log(tx, "runtime", "event.delivered", {"inbox_event_id": row["event_id"],
                           "source": "environment", "kind": row["kind"], "content": row["content"],
                           "versions": json.loads(row["versions"])}, parent=row["parent"], turn_id=turn_id)
            messages.append({"role": "user", "content": "[环境观察] " + row["content"]})
            tx.execute("UPDATE inbox SET acked=1 WHERE event_id=?", (row["event_id"],))
            st.log(tx, "runtime", "event.acked", {"inbox_event_id": row["event_id"]}, parent=event, turn_id=turn_id)
        st.set_meta(tx, "messages", messages)
    return len(rows)
