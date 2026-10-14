"""Read-only event queries with a stable audit sequence cursor."""


def events_after(store, sequence=0, limit=100):
    import json
    return [json.loads(row[0]) for row in store.db.execute(
        "SELECT payload FROM trace WHERE sequence>? ORDER BY sequence LIMIT ?", (sequence, limit))]
