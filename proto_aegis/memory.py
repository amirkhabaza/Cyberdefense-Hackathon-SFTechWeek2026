"""#2 Query / #3 Intel: vector memory (MongoDB Atlas Vector Search) with a local keyword fallback."""
import json
import os
import re

import config
import llm

INDEX = "intel_vector"


def _mongo_coll():
    from pymongo import MongoClient
    c = MongoClient(os.environ["MONGODB_URI"], serverSelectionTimeoutMS=6000)
    return c[os.environ.get("MONGODB_DB", "aegis")]["intel"]


def load_playbooks():
    return json.loads((config.HERE / "intel" / "playbooks.json").read_text())


def _words(s):
    return set(re.findall(r"[a-z0-9_]+", s.lower()))


def _local_search(query, k):
    q = _words(query)
    scored = []
    for d in load_playbooks():
        w = _words(d["title"] + " " + d["text"])
        scored.append((len(q & w) / (len(q) ** 0.5 * len(w) ** 0.5 or 1), d))
    scored.sort(key=lambda x: -x[0])
    return [{**d, "score": round(s, 3)} for s, d in scored[:k]]


def search(query, k=3):
    """Returns (docs, mode). Tries Atlas vector search, then falls back to local keyword search."""
    if config.have("MONGODB_URI") and config.have("OPENAI_API_KEY"):
        try:
            vec = llm.embed(query)
            docs = list(_mongo_coll().aggregate([
                {"$vectorSearch": {"index": INDEX, "path": "embedding", "queryVector": vec, "numCandidates": 50, "limit": k}},
                {"$project": {"_id": 0, "id": 1, "title": 1, "kind": 1, "text": 1, "score": {"$meta": "vectorSearchScore"}}},
            ]))
            if docs:
                return docs, "live"
        except Exception as e:
            print(f"     (MongoDB vector search unavailable: {str(e)[:120]}; using local memory)")
    return _local_search(query, k), "stub"
