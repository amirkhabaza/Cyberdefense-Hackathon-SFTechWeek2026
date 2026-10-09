"""#1 Alert: detect active exploitation in ClickHouse (tables created by proto_board/seed.py)."""
import json
import os

import config


def detect(force_stub=False):
    """Returns (alert_dict_or_None, mode)."""
    if force_stub or not config.have("CLICKHOUSE_HOST", "CLICKHOUSE_PASSWORD"):
        return json.loads((config.HERE / "fixtures" / "alert.json").read_text()), "stub"
    import clickhouse_connect
    c = clickhouse_connect.get_client(
        host=os.environ["CLICKHOUSE_HOST"], port=int(os.environ.get("CLICKHOUSE_PORT", "8443")),
        username=os.environ.get("CLICKHOUSE_USER", "default"), password=os.environ["CLICKHOUSE_PASSWORD"],
        database=os.environ.get("CLICKHOUSE_DATABASE", "aegis"), secure=True)
    total = c.query("SELECT sum(requests) / 60 FROM traffic_events WHERE ts >= now() - INTERVAL 61 SECOND "
                    "AND ts < now() - INTERVAL 1 SECOND").result_rows[0][0] or 0
    rows = c.query(
        "SELECT path, attack_type, sum(requests), uniqExact(src_ip), sum(requests) / 60, "
        "sumIf(confidence * requests, 1) / greatest(sum(requests), 1) "
        "FROM traffic_events WHERE is_malicious = 1 AND status < 400 AND attack_type = 'sql_injection' "
        "AND ts >= now() - INTERVAL 61 SECOND AND ts < now() - INTERVAL 1 SECOND "
        "GROUP BY path, attack_type ORDER BY 3 DESC LIMIT 1").result_rows
    if not rows:
        return None, "live"
    path, atype, reqs, srcs, rps, conf = rows[0]
    return {"path": path, "attack_type": atype, "requests_60s": int(reqs), "sources": int(srcs),
            "active_exploit_rps": float(rps), "total_rps": float(total),
            "malicious_pct": float(rps) / float(total) if total else 0, "confidence": float(conf), "source": "clickhouse"}, "live"
