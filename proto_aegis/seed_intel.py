#!/usr/bin/env python3
"""Embed intel/playbooks.json with OpenAI and load it into MongoDB Atlas, plus create the vector search index."""
import os
import sys

import config
import llm
import memory


def main():
    if not (config.have("MONGODB_URI") and config.have("OPENAI_API_KEY")):
        sys.exit("Set MONGODB_URI and OPENAI_API_KEY in .env first.")
    coll = memory._mongo_coll()
    docs = memory.load_playbooks()
    for d in docs:
        d["embedding"] = llm.embed(d["title"] + ". " + d["text"])
        coll.replace_one({"id": d["id"]}, d, upsert=True)
    print(f"Loaded {len(docs)} intel documents into {coll.full_name}.")
    try:
        from pymongo.operations import SearchIndexModel
        coll.create_search_index(SearchIndexModel(
            name=memory.INDEX, type="vectorSearch",
            definition={"fields": [{"type": "vector", "path": "embedding", "numDimensions": len(docs[0]["embedding"]),
                                    "similarity": "cosine"}]}))
        print(f"Created vector index '{memory.INDEX}' (it can take a minute to become queryable).")
    except Exception as e:
        print(f"Index not created ({str(e)[:150]}). Create a vector index named '{memory.INDEX}' on 'embedding' in the Atlas UI.")


if __name__ == "__main__":
    main()
