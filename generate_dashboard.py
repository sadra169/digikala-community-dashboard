#!/usr/bin/env python3
"""
DigiKala Community Dashboard Generator
=======================================
Connects to MySQL, runs analytics queries, and outputs a self-contained HTML
dashboard file that can be opened in any browser and shared via email/Slack.

Usage:
    python3 generate_dashboard.py

Schedule (cron – daily at 6 AM):
    0 6 * * * cd /path/to/script && python3 generate_dashboard.py

Output:
    community_dashboard.html   (in the same directory)
"""

import sys
import json
from datetime import datetime

# ─── Auto-install dependencies ───────────────────────────────────────────────
import subprocess

_DEPS = {
    "mysql-connector-python": "mysql.connector",
    "jdatetime":              "jdatetime",
    "clickhouse-connect":     "clickhouse_connect",
}
for _pkg, _mod in _DEPS.items():
    try:
        __import__(_mod)
    except ImportError:
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", _pkg,
             "--break-system-packages", "-q"]
        )

import mysql.connector
import jdatetime
import clickhouse_connect

# ═══════════════════════════════════ CONFIG ══════════════════════════════════
# Credentials are loaded from config.py (gitignored) if present,
# otherwise from environment variables.  Never commit real passwords here.
try:
    from config import MYSQL_HOST, MYSQL_PORT, MYSQL_USER, MYSQL_PASSWORD
    from config import CH_HOST, CH_PORT, CH_USER, CH_PASSWORD
except ImportError:
    import os
    MYSQL_HOST     = os.environ.get("MYSQL_HOST",     "")
    MYSQL_PORT     = int(os.environ.get("MYSQL_PORT", "13306"))
    MYSQL_USER     = os.environ.get("MYSQL_USER",     "")
    MYSQL_PASSWORD = os.environ.get("MYSQL_PASSWORD", "")
    CH_HOST        = os.environ.get("CH_HOST",        "")
    CH_PORT        = int(os.environ.get("CH_PORT",    "28123"))
    CH_USER        = os.environ.get("CH_USER",        "")
    CH_PASSWORD    = os.environ.get("CH_PASSWORD",    "")

DB_CONFIG = {
    "host":              MYSQL_HOST,
    "port":              MYSQL_PORT,
    "user":              MYSQL_USER,
    "password":          MYSQL_PASSWORD,
    "database":          "social",
    "charset":           "utf8mb4",
    "connection_timeout": 30,
}

OUTPUT_FILE = "index.html"
LAUNCH_DATE = "2025-02-19 18:00:00"   # community launch date (keep fixed)
TREND_DAYS  = 60                       # days back for daily trend charts

BIGDATA_CONFIG = {                     # ClickHouse — bigdata schema
    "host":     CH_HOST,
    "port":     CH_PORT,
    "user":     CH_USER,
    "password": CH_PASSWORD,
}
# ═════════════════════════════════════════════════════════════════════════════


# ─── Helpers ──────────────────────────────────────────────────────────────────

PERSIAN_MONTHS = [
    "فروردین","اردیبهشت","خرداد","تیر","مرداد","شهریور",
    "مهر","آبان","آذر","دی","بهمن","اسفند"
]

def to_jalali_day(d_str):
    """Convert '2025-03-18' or a date object → '۱۸ خرداد'"""
    from datetime import date as _date
    if isinstance(d_str, _date):
        g = d_str
    else:
        parts = str(d_str).split("-")
        g = _date(int(parts[0]), int(parts[1]), int(parts[2]))
    j = jdatetime.date.fromgregorian(date=g)
    return f"{j.day} {PERSIAN_MONTHS[j.month - 1]}"

def to_jalali_month(m_str):
    """Convert '2025-03' → 'خرداد ۱۴۰۴'"""
    from datetime import date as _date
    parts = str(m_str).split("-")
    g = _date(int(parts[0]), int(parts[1]), 1)
    j = jdatetime.date.fromgregorian(date=g)
    return f"{PERSIAN_MONTHS[j.month - 1]} {j.year}"

def jalali_now():
    """Current datetime as a Persian string."""
    j = jdatetime.datetime.now()
    return f"{j.year}/{j.month:02d}/{j.day:02d}  {j.hour:02d}:{j.minute:02d}"

def run(cursor, sql):
    cursor.execute(sql)
    return cursor.fetchall()

def v(val, default=0):
    return val if val is not None else default

def fmt(n):
    try:
        return f"{int(n):,}"
    except Exception:
        return str(n) if n is not None else "—"

def J(obj):
    return json.dumps(obj, default=str, ensure_ascii=False)


# ─── Data Fetcher ─────────────────────────────────────────────────────────────

def fetch(conn):
    cur = conn.cursor()
    d = {}

    # ── Overview KPIs ─────────────────────────────────────────────────────────
    rows = run(cur, f"""
        SELECT count(DISTINCT user_id)
        FROM (
            SELECT user_id FROM social.community_user_reactions
            UNION ALL
            SELECT user_id FROM social.community_questions  WHERE verification_status='verified'
            UNION ALL
            SELECT user_id FROM social.community_answers    WHERE verification_status='verified'
            UNION ALL
            SELECT user_id FROM social.community_members
        ) t
    """)
    d["total_engaged"] = v(rows[0][0]) if rows else 0

    rows = run(cur, """
        SELECT count(*), count(DISTINCT user_id)
        FROM social.community_questions WHERE verification_status='verified'
    """)
    d["total_questions"]   = v(rows[0][0])
    d["total_questioners"] = v(rows[0][1])

    rows = run(cur, """
        SELECT count(*), count(DISTINCT user_id)
        FROM social.community_answers WHERE verification_status='verified'
    """)
    d["total_answers"]   = v(rows[0][0])
    d["total_answerers"] = v(rows[0][1])

    rows = run(cur, """
        SELECT count(*), count(DISTINCT user_id)
        FROM social.community_members
    """)
    d["total_joins"]    = v(rows[0][0])
    d["unique_members"] = v(rows[0][1])

    rows = run(cur, "SELECT count(*) FROM social.communities")
    d["total_communities"] = v(rows[0][0]) if rows else 0

    # Avg answers per question
    d["avg_answers"] = round(d["total_answers"] / d["total_questions"], 2) if d["total_questions"] else 0

    # ── DMU (DAU/MAU) ─────────────────────────────────────────────────────────
    for kind, table in [("answerers",  "community_answers"),
                        ("questioners","community_questions")]:
        rows = run(cur, f"""
            SELECT
                count(DISTINCT CASE WHEN created_at > DATE_SUB(CURDATE(), INTERVAL 30 DAY) THEN user_id END) m30,
                count(DISTINCT CASE WHEN created_at > DATE_SUB(CURDATE(), INTERVAL 1  DAY) THEN user_id END) d1
            FROM social.{table}
            WHERE verification_status='verified'
        """)
        m30 = v(rows[0][0])
        d1  = v(rows[0][1])
        d[f"mau_{kind}"] = m30
        d[f"dau_{kind}"] = d1
        d[f"dmu_{kind}"] = round(d1 / m30 * 100, 1) if m30 else 0

    # ── Response time KPIs (since launch, all-time) ────────────────────────────
    for hrs, key in [(1, "pct_1h"), (3, "pct_3h"), (24, "pct_24h")]:
        rows = run(cur, f"""
            SELECT ROUND(
                100.0 * COUNT(DISTINCT CASE
                    WHEN a.created_at <= q.created_at + INTERVAL {hrs} HOUR
                     AND a.verification_status='verified'
                     AND a.created_at > '{LAUNCH_DATE}'
                    THEN q.id END)
                / NULLIF(COUNT(DISTINCT q.id), 0), 2)
            FROM social.community_questions q
            LEFT JOIN social.community_answers a ON q.id = a.question_id
            WHERE q.verification_status='verified'
              AND q.created_at > '{LAUNCH_DATE}'
              AND q.created_at < SUBDATE(CURDATE(), 1)
        """)
        d[key] = v(rows[0][0]) if rows else 0

    # ── Author satisfaction ────────────────────────────────────────────────────
    rows = run(cur, """
        SELECT ROUND(tb1.n / NULLIF(tb2.total, 0) * 100, 2)
        FROM (
            SELECT count(DISTINCT CASE WHEN ur.user_id = q.user_id THEN q.id END) n
            FROM social.community_user_reactions ur
            LEFT JOIN social.community_answers a  ON a.id  = ur.entity_id
            LEFT JOIN social.community_questions q ON q.id = a.question_id
            WHERE reaction_type = 'like'
        ) tb1
        JOIN (SELECT count(*) total FROM social.community_questions WHERE answer_count > 0) tb2
    """)
    d["pct_author_liked"] = round(float(v(rows[0][0])), 2) if rows else 0

    # ── Trend: daily engaged users (since launch — full dataset for JS filtering)
    _L = LAUNCH_DATE[:10]
    rows = run(cur, f"""
        SELECT DATE(created_at) dt, count(DISTINCT user_id) cnt
        FROM (
            SELECT user_id, created_at FROM social.community_user_reactions
                WHERE created_at >= '{_L}' AND created_at < CURDATE()
            UNION ALL
            SELECT user_id, created_at FROM social.community_questions
                WHERE verification_status='verified'
                  AND created_at >= '{_L}' AND created_at < CURDATE()
            UNION ALL
            SELECT user_id, created_at FROM social.community_answers
                WHERE verification_status='verified'
                  AND created_at >= '{_L}' AND created_at < CURDATE()
            UNION ALL
            SELECT user_id, created_at FROM social.community_members
                WHERE created_at >= '{_L}' AND created_at < CURDATE()
        ) tb GROUP BY dt ORDER BY dt
    """)
    d["trend_eng_raw_dates"] = [str(r[0]) for r in rows]
    d["trend_eng_dates"]     = [to_jalali_day(r[0]) for r in rows]
    d["trend_eng_vals"]      = [v(r[1]) for r in rows]

    # ── Trend: daily questions (since launch) ─────────────────────────────────
    rows = run(cur, f"""
        SELECT DATE(created_at) dt, count(*) qs, count(DISTINCT user_id) qers
        FROM social.community_questions
        WHERE verification_status='verified'
          AND created_at >= '{_L}' AND created_at < CURDATE()
        GROUP BY dt ORDER BY dt
    """)
    d["trend_q_raw_dates"] = [str(r[0]) for r in rows]
    d["trend_q_dates"]     = [to_jalali_day(r[0]) for r in rows]
    d["trend_q_vals"]      = [v(r[1]) for r in rows]
    d["trend_qers"]        = [v(r[2]) for r in rows]

    # ── Trend: daily answers (since launch) ───────────────────────────────────
    rows = run(cur, f"""
        SELECT DATE(created_at) dt, count(*) ans, count(DISTINCT user_id) aers
        FROM social.community_answers
        WHERE verification_status='verified'
          AND created_at >= '{_L}' AND created_at < CURDATE()
        GROUP BY dt ORDER BY dt
    """)
    d["trend_a_raw_dates"] = [str(r[0]) for r in rows]
    d["trend_a_dates"]     = [to_jalali_day(r[0]) for r in rows]
    d["trend_a_vals"]      = [v(r[1]) for r in rows]
    d["trend_aers"]        = [v(r[2]) for r in rows]

    # ── Trend: community joins (since launch) ─────────────────────────────────
    rows = run(cur, f"""
        SELECT DATE(created_at) dt, count(*) joins
        FROM social.community_members
        WHERE created_at >= '{_L}' AND created_at < CURDATE()
        GROUP BY dt ORDER BY dt
    """)
    d["trend_joins_raw_dates"] = [str(r[0]) for r in rows]
    d["trend_joins_dates"]     = [to_jalali_day(r[0]) for r in rows]
    d["trend_joins_vals"]      = [v(r[1]) for r in rows]

    # ── Trend: 24h response rate (last 30 days — kept short, expensive query) ─
    rows = run(cur, f"""
        SELECT DATE(q.created_at) dt,
            ROUND(100.0 * COUNT(DISTINCT CASE
                WHEN a.created_at <= q.created_at + INTERVAL 24 HOUR
                 AND a.verification_status='verified'
                 AND a.created_at > '{LAUNCH_DATE}' AND a.created_at < CURDATE()
                THEN q.id END) / NULLIF(COUNT(DISTINCT q.id), 0), 2) pct
        FROM social.community_questions q
        LEFT JOIN social.community_answers a ON q.id = a.question_id
        WHERE q.verification_status='verified'
          AND q.created_at > DATE_SUB(CURDATE(), INTERVAL 30 DAY)
          AND q.created_at < CURDATE()
        GROUP BY dt ORDER BY dt
    """)
    d["resp_raw_dates"] = [str(r[0]) for r in rows]
    d["resp_dates"]     = [to_jalali_day(r[0]) for r in rows]
    d["resp_24h_vals"]  = [v(r[1]) for r in rows]

    # ── Monthly engaged community members ─────────────────────────────────────
    rows = run(cur, """
        WITH members AS (
            SELECT DISTINCT user_id, community_id FROM social.community_members
        ),
        q_ev AS (
            SELECT q.user_id, q.community_id, q.created_at
            FROM social.community_questions q
            WHERE q.verification_status='verified'
              AND q.created_at >= '2025-02-01' AND q.created_at < CURDATE()
        ),
        a_ev AS (
            SELECT a.user_id, q.community_id, a.created_at
            FROM social.community_answers a
            JOIN social.community_questions q ON q.id = a.question_id
            WHERE a.verification_status='verified'
              AND a.created_at >= '2025-02-01' AND a.created_at < CURDATE()
        ),
        r_ev AS (
            SELECT r.user_id, q.community_id, r.created_at
            FROM social.community_user_reactions r
            JOIN social.community_questions q ON q.id = r.entity_id
            WHERE r.created_at >= '2025-02-01' AND r.created_at < CURDATE()
            UNION ALL
            SELECT r.user_id, q2.community_id, r.created_at
            FROM social.community_user_reactions r
            JOIN social.community_answers a     ON a.id  = r.entity_id
            JOIN social.community_questions q2  ON q2.id = a.question_id
            WHERE r.created_at >= '2025-02-01' AND r.created_at < CURDATE()
        ),
        all_act AS (
            SELECT user_id, community_id, created_at FROM q_ev
            UNION ALL SELECT user_id, community_id, created_at FROM a_ev
            UNION ALL SELECT user_id, community_id, created_at FROM r_ev
        ),
        valid AS (
            SELECT aa.user_id, aa.created_at
            FROM all_act aa
            JOIN members m ON m.user_id = aa.user_id AND m.community_id = aa.community_id
        )
        SELECT DATE_FORMAT(created_at, '%Y-%m') mo, COUNT(DISTINCT user_id) cnt
        FROM valid
        GROUP BY mo ORDER BY mo
    """)
    d["monthly_months"] = [to_jalali_month(r[0]) for r in rows]
    d["monthly_vals"]   = [v(r[1]) for r in rows]

    # ── Top communities by members ─────────────────────────────────────────────
    rows = run(cur, "SELECT name, member_count FROM social.communities ORDER BY member_count DESC LIMIT 15")
    d["top_comm_names"]   = [r[0] for r in rows]
    d["top_comm_members"] = [v(r[1]) for r in rows]

    # ── Top communities by questions ──────────────────────────────────────────
    rows = run(cur, "SELECT name, question_count FROM social.communities ORDER BY question_count DESC LIMIT 15")
    d["top_comm_q_names"] = [r[0] for r in rows]
    d["top_comm_q_vals"]  = [v(r[1]) for r in rows]

    # ── Answer count distribution ──────────────────────────────────────────────
    rows = run(cur, """
        SELECT answer_group, cnt, ROUND(cnt / total_count * 100, 1) pct
        FROM (
            SELECT CASE WHEN answer_count > 3 THEN '3+' ELSE CAST(answer_count AS CHAR) END answer_group,
                   COUNT(*) cnt
            FROM social.community_questions WHERE verification_status='verified'
            GROUP BY answer_group
        ) g,
        (SELECT COUNT(*) total_count FROM social.community_questions WHERE verification_status='verified') t
        ORDER BY CASE WHEN answer_group = '3+' THEN 999 ELSE CAST(answer_group AS UNSIGNED) END
    """)
    d["ans_dist_labels"] = [r[0] for r in rows]
    d["ans_dist_vals"]   = [v(r[1]) for r in rows]
    d["ans_dist_pcts"]   = [v(r[2]) for r in rows]

    # Compute answer rate (% questions with ≥1 answer)
    ans_dict = {r[0]: v(r[2]) for r in rows}
    d["pct_answered"] = round(100 - float(ans_dict.get("0", 0)), 1)

    # ── Top upvoted questions ──────────────────────────────────────────────────
    rows = run(cur, """
        SELECT c.name, LEFT(q.body, 160), q.upvote_count, q.answer_count
        FROM social.community_questions q
        LEFT JOIN social.communities c ON c.id = q.community_id
        WHERE q.verification_status='verified'
        ORDER BY q.upvote_count DESC LIMIT 10
    """)
    d["top_upvoted"] = [
        {"community": r[0] or "—", "body": r[1] or "", "upvotes": v(r[2]), "answers": v(r[3])}
        for r in rows
    ]

    # ── Top answered questions ─────────────────────────────────────────────────
    rows = run(cur, """
        SELECT c.name, LEFT(q.body, 160), q.answer_count, q.upvote_count
        FROM social.community_questions q
        LEFT JOIN social.communities c ON c.id = q.community_id
        WHERE q.verification_status='verified'
        ORDER BY q.answer_count DESC LIMIT 10
    """)
    d["top_answered"] = [
        {"community": r[0] or "—", "body": r[1] or "", "answers": v(r[2]), "upvotes": v(r[3])}
        for r in rows
    ]

    cur.close()
    return d


# ─── ClickHouse: Traffic & DAU ────────────────────────────────────────────────

def fetch_bigdata():
    """Fetch Community DAU and Digikala DAU from ClickHouse bigdata schema.
    Returns a dict on success, or None if the connection fails."""
    try:
        client = clickhouse_connect.get_client(
            host=BIGDATA_CONFIG["host"],
            port=BIGDATA_CONFIG["port"],
            username=BIGDATA_CONFIG["user"],
            password=BIGDATA_CONFIG["password"],
            connect_timeout=15,
            send_receive_timeout=120,   # max 2 min per query
        )
        print("   ✅ Connected to ClickHouse")
    except Exception as e:
        print(f"   ⚠️  ClickHouse connection failed: {e}")
        return None

    d = {}
    try:
        # ── Community DAU (last 30 days) ──────────────────────────────────────
        print("   ⏳ Running community DAU query …", flush=True)
        community_dau_sql = """
            SELECT partition_date,
                   count(distinct session_id) AS sessions,
                   count(distinct user_id)    AS dau
            FROM (
                SELECT partition_date, session_id, user_id
                FROM bigdata.page_view
                WHERE page_type IN (
                    'question_page', 'community_page', 'select_community_page',
                    'question_select_product_page', 'community_create_question_page',
                    'search_in_community_page', 'answer_select_product_page',
                    'digi_q_search', 'community_homepage', 'community_explore_page',
                    'community_question_page', 'community_question_answer_page'
                )
                AND partition_date >= today() - 30
                AND partition_date < today()
                UNION ALL
                SELECT partition_date, session_id, user_id
                FROM bigdata.social_interactions
                WHERE event_title = 'community_homepage_view'
                AND partition_date >= today() - 30
                AND partition_date < today()
            )
            GROUP BY partition_date
            ORDER BY partition_date
        """
        res = client.query(community_dau_sql)
        rows = res.result_rows
        print(f"   ✅ Community DAU done ({len(rows)} rows)", flush=True)
        raw_dates = [str(r[0]) for r in rows]
        d["dau_raw_dates"]              = raw_dates
        d["dau_dates"]                  = [to_jalali_day(dt) for dt in raw_dates]
        d["dau_community_sessions"]     = [int(r[1]) for r in rows]
        d["dau_community_vals"]         = [int(r[2]) for r in rows]
        d["dau_community_yesterday"]    = d["dau_community_vals"][-1] if rows else 0
        d["dau_community_sess_yest"]    = d["dau_community_sessions"][-1] if rows else 0

        # ── Digikala total DAU + sessions (same period) ───────────────────────
        # uniq() = HyperLogLog approximation (~2% error) — 10-50x faster than count(distinct)
        print("   ⏳ Running Digikala DAU query …", flush=True)
        digi_dau_sql = """
            SELECT partition_date,
                   uniq(session_id) AS sessions,
                   uniq(user_id)    AS dau
            FROM bigdata.page_view
            WHERE partition_date >= today() - 30
            AND partition_date < today()
            GROUP BY partition_date
            ORDER BY partition_date
        """
        res2 = client.query(digi_dau_sql)
        digi_map_sess = {str(r[0]): int(r[1]) for r in res2.result_rows}
        digi_map_dau  = {str(r[0]): int(r[2]) for r in res2.result_rows}
        print(f"   ✅ Digikala DAU done ({len(res2.result_rows)} rows)", flush=True)
        d["dau_digikala_sessions"]  = [digi_map_sess.get(dt, 0) for dt in raw_dates]
        d["dau_digikala_vals"]      = [digi_map_dau.get(dt, 0) for dt in raw_dates]
        d["dau_digikala_yesterday"] = d["dau_digikala_vals"][-1] if d["dau_digikala_vals"] else 0
        d["dau_digikala_sess_yest"] = d["dau_digikala_sessions"][-1] if d["dau_digikala_sessions"] else 0

    except Exception as e:
        print(f"   ⚠️  ClickHouse query error: {e}")
        client.close()
        return None

    client.close()
    return d


# ─── HTML Generator ───────────────────────────────────────────────────────────

def build_upvoted_rows(items):
    rows = []
    for i, item in enumerate(items):
        body = item["body"][:120] + ("…" if len(item["body"]) > 120 else "")
        ans_class = "pill-green" if item["answers"] > 0 else "pill-red"
        rows.append(f"""
          <tr>
            <td class="td-rank">{i+1}</td>
            <td><span class="pill pill-brand">{item['community']}</span></td>
            <td class="td-question">{body}</td>
            <td class="td-num">👍 {fmt(item['upvotes'])}</td>
            <td class="td-num"><span class="pill {ans_class}">{fmt(item['answers'])}</span></td>
          </tr>""")
    return "".join(rows)


def build_answered_rows(items):
    rows = []
    for i, item in enumerate(items):
        body = item["body"][:120] + ("…" if len(item["body"]) > 120 else "")
        rows.append(f"""
          <tr>
            <td class="td-rank">{i+1}</td>
            <td><span class="pill pill-brand">{item['community']}</span></td>
            <td class="td-question">{body}</td>
            <td class="td-num"><span class="pill pill-green">{fmt(item['answers'])}</span></td>
            <td class="td-num">👍 {fmt(item['upvotes'])}</td>
          </tr>""")
    return "".join(rows)


def generate_html(d, generated_at):
    # Pre-serialize all chart data to JSON
    j_eng_dates    = J(d["trend_eng_dates"])
    j_eng_vals     = J(d["trend_eng_vals"])
    j_q_dates      = J(d["trend_q_dates"])
    j_q_vals       = J(d["trend_q_vals"])
    j_qers         = J(d["trend_qers"])
    j_a_dates      = J(d["trend_a_dates"])
    j_a_vals       = J(d["trend_a_vals"])
    j_aers         = J(d["trend_aers"])
    j_resp_dates   = J(d["resp_dates"])
    j_resp_24h     = J(d["resp_24h_vals"])
    j_monthly_mo   = J(d["monthly_months"])
    j_monthly_v    = J(d["monthly_vals"])
    j_comm_names   = J(list(reversed(d["top_comm_names"])))
    j_comm_mem     = J(list(reversed(d["top_comm_members"])))
    j_comm_q_names = J(list(reversed(d["top_comm_q_names"])))
    j_comm_q_vals  = J(list(reversed(d["top_comm_q_vals"])))
    j_ans_labels   = J([f"{l} ans ({p}%)" for l, p in zip(d["ans_dist_labels"], d["ans_dist_pcts"])])
    j_ans_vals     = J(d["ans_dist_vals"])
    j_joins_dates  = J(d["trend_joins_dates"])
    j_joins_vals   = J(d["trend_joins_vals"])

    # ── Traffic & Engagement ──────────────────────────────────────────────────
    dau_yesterday      = d.get("dau_community_yesterday", 0)
    digi_yesterday     = d.get("dau_digikala_yesterday", 0)
    digi_sess_yest     = d.get("dau_digikala_sess_yest", 0)
    comm_sess_yest     = d.get("dau_community_sess_yest", 0)
    deu_yesterday      = d["trend_eng_vals"][-1] if d.get("trend_eng_vals") else 0
    deu_dau_pct        = round(deu_yesterday / dau_yesterday * 100, 1) if dau_yesterday > 0 else "—"
    comm_digi_pct      = round(dau_yesterday / digi_yesterday * 100, 2) if digi_yesterday > 0 else "—"

    # Aligned DEU values for ClickHouse dates (for combo chart)
    _deu_map           = dict(zip(d.get("trend_eng_raw_dates", []), d.get("trend_eng_vals", [])))
    _dau_raw           = d.get("dau_raw_dates", [])
    _comm_vals         = d.get("dau_community_vals", [])
    _digi_vals         = d.get("dau_digikala_vals", [])
    _deu_aligned       = [_deu_map.get(dt, 0) for dt in _dau_raw]

    j_dau_dates        = J(d.get("dau_dates", []))
    j_dau_comm         = J(_comm_vals)
    j_deu_aligned      = J(_deu_aligned)
    j_deu_dau_daily    = J([
        round(_deu_map.get(dt, 0) / c * 100, 1) if c > 0 else 0
        for dt, c in zip(_dau_raw, _comm_vals)
    ])
    j_dau_share_pct    = J([
        round(c / g * 100, 2) if g > 0 else 0
        for c, g in zip(_comm_vals, _digi_vals)
    ])
    j_comm_sessions    = J(d.get("dau_community_sessions", []))
    j_digi_sessions    = J(d.get("dau_digikala_sessions", []))

    # Full datasets for JS time-range filtering (social schema charts)
    j_all_eng_raw      = J(d.get("trend_eng_raw_dates", []))
    j_all_eng_labels   = J(d.get("trend_eng_dates", []))
    j_all_eng_vals     = J(d.get("trend_eng_vals", []))
    j_all_q_raw        = J(d.get("trend_q_raw_dates", []))
    j_all_q_labels     = J(d.get("trend_q_dates", []))
    j_all_q_vals       = J(d.get("trend_q_vals", []))
    j_all_qers         = J(d.get("trend_qers", []))
    j_all_a_raw        = J(d.get("trend_a_raw_dates", []))
    j_all_a_labels     = J(d.get("trend_a_dates", []))
    j_all_a_vals       = J(d.get("trend_a_vals", []))
    j_all_aers         = J(d.get("trend_aers", []))
    j_all_joins_raw    = J(d.get("trend_joins_raw_dates", []))
    j_all_joins_labels = J(d.get("trend_joins_dates", []))
    j_all_joins_vals   = J(d.get("trend_joins_vals", []))

    upvoted_rows  = build_upvoted_rows(d["top_upvoted"])
    answered_rows = build_answered_rows(d["top_answered"])

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>DigiKala Community Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.4/dist/chart.umd.min.js"></script>
<style>
:root {{
  --brand:  #EE3844;
  --dark:   #12151E;
  --bg:     #F0F2F6;
  --card:   #FFFFFF;
  --text:   #2D3748;
  --muted:  #718096;
  --border: #E2E8F0;
  --green:  #38A169;
  --blue:   #3182CE;
  --orange: #DD6B20;
  --purple: #805AD5;
  --teal:   #2C7A7B;
}}
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: var(--bg); color: var(--text); }}

/* ── Header ── */
.header {{
  background: linear-gradient(135deg, #12151E 0%, #1A2035 55%, #1E2D55 100%);
  padding: 18px 32px;
  display: flex; align-items: center; justify-content: space-between;
  box-shadow: 0 2px 16px rgba(0,0,0,0.25);
  position: sticky; top: 0; z-index: 100;
}}
.header-left {{ display: flex; align-items: center; gap: 14px; }}
.logo {{ width: 42px; height: 42px; border-radius: 10px; background: var(--brand); display: flex; align-items: center; justify-content: center; font-weight: 900; font-size: 16px; color: #fff; letter-spacing: -0.5px; flex-shrink: 0; }}
.header h1 {{ color: #fff; font-size: 20px; font-weight: 700; }}
.header-sub {{ color: rgba(255,255,255,0.55); font-size: 12px; margin-top: 2px; }}
.header-right {{ text-align: right; }}
.updated {{ color: rgba(255,255,255,0.6); font-size: 12px; }}
.badge {{ display: inline-block; margin-top: 5px; background: rgba(238,56,68,0.18); color: #FF8080; border: 1px solid rgba(238,56,68,0.35); border-radius: 20px; padding: 2px 10px; font-size: 11px; font-weight: 600; }}

/* ── Layout ── */
.container {{ max-width: 1440px; margin: 0 auto; padding: 24px 28px 40px; }}
.section {{ margin-bottom: 28px; }}
.section-title {{
  font-size: 13px; font-weight: 700; color: var(--dark);
  margin-bottom: 14px; display: flex; align-items: center; gap: 8px;
  text-transform: uppercase; letter-spacing: 0.6px;
}}
.section-title::before {{ content: ""; width: 3px; height: 14px; background: var(--brand); border-radius: 2px; flex-shrink: 0; }}

/* ── Cards ── */
.card {{
  background: var(--card); border-radius: 14px; padding: 20px;
  box-shadow: 0 1px 3px rgba(0,0,0,0.06), 0 0 0 1px var(--border);
  transition: box-shadow 0.2s;
}}
.card:hover {{ box-shadow: 0 4px 14px rgba(0,0,0,0.09), 0 0 0 1px var(--border); }}

/* ── Time-range filter bar ── */
.filter-bar {{
  background: var(--card); border: 1px solid var(--border); border-radius: 12px;
  padding: 10px 18px; display: flex; align-items: center; gap: 14px; flex-wrap: wrap;
  margin-bottom: 22px; box-shadow: 0 1px 3px rgba(0,0,0,0.05);
}}
.filter-label {{ font-size: 11px; font-weight: 700; color: var(--muted); white-space: nowrap; text-transform: uppercase; letter-spacing: 0.5px; }}
.tf-group {{ display: flex; gap: 5px; flex-wrap: wrap; }}
.tf-btn {{
  background: var(--bg); border: 1px solid var(--border); border-radius: 20px;
  padding: 4px 13px; font-size: 11px; font-weight: 600; color: var(--muted); cursor: pointer;
  transition: all 0.15s; white-space: nowrap;
}}
.tf-btn:hover {{ background: var(--brand); color: #fff; border-color: var(--brand); }}
.tf-btn.active {{ background: var(--brand); color: #fff; border-color: var(--brand); }}
.date-inp {{
  border: 1px solid var(--border); border-radius: 8px; padding: 4px 10px;
  font-size: 11px; color: var(--text); background: var(--bg); cursor: pointer;
}}
.custom-range {{ display:none; align-items:center; gap:8px; }}
.custom-range span {{ font-size:11px; color:var(--muted); }}

/* ── KPI Grid ── */
.kpi-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(175px, 1fr)); gap: 14px; }}
.kpi-card {{ padding: 18px 20px; }}
.kpi-icon {{ font-size: 20px; margin-bottom: 10px; }}
.kpi-label {{ font-size: 10px; font-weight: 700; color: var(--muted); text-transform: uppercase; letter-spacing: 0.7px; margin-bottom: 6px; }}
.kpi-value {{ font-size: 26px; font-weight: 800; line-height: 1; color: var(--dark); }}
.kpi-sub {{ font-size: 11px; color: var(--muted); margin-top: 6px; }}
.kpi-brand  .kpi-value {{ color: var(--brand);  }}
.kpi-green  .kpi-value {{ color: var(--green);  }}
.kpi-blue   .kpi-value {{ color: var(--blue);   }}
.kpi-orange .kpi-value {{ color: var(--orange); }}
.kpi-purple .kpi-value {{ color: var(--purple); }}
.kpi-teal   .kpi-value {{ color: var(--teal);   }}

/* ── Chart Grids ── */
.grid-1   {{ display: grid; grid-template-columns: 1fr; gap: 14px; }}
.grid-2   {{ display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }}
.grid-3-1 {{ display: grid; grid-template-columns: 2fr 1fr; gap: 14px; }}
.chart-title {{ font-size: 13px; font-weight: 600; color: var(--dark); margin-bottom: 14px; }}
.chart-sub {{ font-size: 11px; font-weight: 400; color: var(--muted); }}
.chart-wrap          {{ position: relative; height: 220px; }}
.chart-wrap.h260     {{ height: 260px; }}
.chart-wrap.h300     {{ height: 300px; }}

/* ── Response Time Side Panel ── */
.resp-panel {{ display: flex; flex-direction: column; gap: 12px; }}
.resp-card {{ border-radius: 12px; padding: 18px; text-align: center; border: 1px solid var(--border); background: var(--card); }}
.resp-pct {{ font-size: 34px; font-weight: 800; line-height: 1; }}
.resp-label {{ font-size: 10px; font-weight: 700; color: var(--muted); text-transform: uppercase; letter-spacing: 0.6px; margin-top: 6px; }}

/* ── Tables ── */
.table-wrap {{ overflow-x: auto; }}
table {{ width: 100%; border-collapse: collapse; }}
thead th {{
  background: #F7FAFC; padding: 10px 14px;
  font-size: 10px; font-weight: 700; color: var(--muted);
  text-transform: uppercase; letter-spacing: 0.6px;
  border-bottom: 1px solid var(--border); white-space: nowrap;
  text-align: left;
}}
tbody tr {{ border-bottom: 1px solid var(--border); transition: background 0.12s; }}
tbody tr:last-child {{ border-bottom: none; }}
tbody tr:hover {{ background: #F7FAFC; }}
tbody td {{ padding: 10px 14px; font-size: 13px; vertical-align: middle; }}
.td-rank {{ color: var(--muted); font-size: 11px; width: 32px; }}
.td-question {{ max-width: 480px; color: var(--text); line-height: 1.4; }}
.td-num {{ text-align: right; white-space: nowrap; font-weight: 600; }}

/* ── Pills ── */
.pill {{ display: inline-block; padding: 2px 8px; border-radius: 20px; font-size: 11px; font-weight: 600; white-space: nowrap; }}
.pill-brand {{ background: rgba(238,56,68,0.12); color: #C0392B; }}
.pill-green {{ background: #C6F6D5; color: #276749; }}
.pill-red   {{ background: #FED7D7; color: #9B2335; }}
.pill-blue  {{ background: #BEE3F8; color: #2A69AC; }}

/* ── Footer ── */
.footer {{ text-align: center; padding: 20px; font-size: 11px; color: var(--muted); }}

@media (max-width: 900px) {{
  .grid-2, .grid-3-1 {{ grid-template-columns: 1fr; }}
  .kpi-grid {{ grid-template-columns: repeat(2, 1fr); }}
  .container {{ padding: 16px; }}
}}
</style>
</head>
<body>

<!-- ── Header ─────────────────────────────────────────────────────────── -->
<div class="header">
  <div class="header-left">
    <div class="logo">DQ</div>
    <div>
      <h1>DigiKala Community Dashboard</h1>
      <div class="header-sub">DigiQ — Community Health &amp; Engagement Metrics</div>
    </div>
  </div>
  <div class="header-right">
    <div class="updated">Updated: {generated_at}</div>
    <div class="badge">● Daily Refresh</div>
  </div>
</div>

<div class="container">

<!-- ── Overview KPIs ──────────────────────────────────────────────────── -->
<div class="section">
  <div class="section-title">Overview</div>
  <div class="kpi-grid">

    <div class="card kpi-card kpi-brand">
      <div class="kpi-icon">👥</div>
      <div class="kpi-label">Total Engaged Users</div>
      <div class="kpi-value">{fmt(d["total_engaged"])}</div>
      <div class="kpi-sub">All-time, all activity types</div>
    </div>

    <div class="card kpi-card kpi-blue">
      <div class="kpi-icon">❓</div>
      <div class="kpi-label">Questions</div>
      <div class="kpi-value">{fmt(d["total_questions"])}</div>
      <div class="kpi-sub">By {fmt(d["total_questioners"])} unique users</div>
    </div>

    <div class="card kpi-card kpi-green">
      <div class="kpi-icon">💬</div>
      <div class="kpi-label">Answers</div>
      <div class="kpi-value">{fmt(d["total_answers"])}</div>
      <div class="kpi-sub">By {fmt(d["total_answerers"])} unique users · avg {d["avg_answers"]} / Q</div>
    </div>

    <div class="card kpi-card kpi-orange">
      <div class="kpi-icon">🏘️</div>
      <div class="kpi-label">Community Members</div>
      <div class="kpi-value">{fmt(d["unique_members"])}</div>
      <div class="kpi-sub">{fmt(d["total_joins"])} total joins</div>
    </div>

    <div class="card kpi-card kpi-purple">
      <div class="kpi-icon">🏷️</div>
      <div class="kpi-label">Communities</div>
      <div class="kpi-value">{fmt(d["total_communities"])}</div>
      <div class="kpi-sub">Active communities</div>
    </div>

    <div class="card kpi-card kpi-teal">
      <div class="kpi-icon">✅</div>
      <div class="kpi-label">Answer Rate</div>
      <div class="kpi-value">{d["pct_answered"]}%</div>
      <div class="kpi-sub">Questions with ≥1 answer</div>
    </div>

    <div class="card kpi-card kpi-brand">
      <div class="kpi-icon">❤️</div>
      <div class="kpi-label">Author Satisfaction</div>
      <div class="kpi-value">{d["pct_author_liked"]}%</div>
      <div class="kpi-sub">Answers liked by the questioner</div>
    </div>

  </div>
</div>

<!-- ── Engagement Trends ───────────────────────────────────────────────── -->
<div class="section">
  <div class="section-title">Engagement Trends</div>
  <div class="grid-3-1">
    <div class="card">
      <div class="chart-title">Daily Engaged Users <span class="chart-sub">— last {TREND_DAYS} days</span></div>
      <div class="chart-wrap h260"><canvas id="cEngaged"></canvas></div>
    </div>
    <div class="card">
      <div class="chart-title">Monthly Engaged Members <span class="chart-sub">— since launch</span></div>
      <div class="chart-wrap h260"><canvas id="cMonthly"></canvas></div>
    </div>
  </div>
</div>

<!-- ── Time-range Filter Bar ──────────────────────────────────────────── -->
<div class="filter-bar">
  <span class="filter-label">📅 بازه نمودارهای تعامل (social):</span>
  <div class="tf-group">
    <button class="tf-btn" onclick="setTF(this,7)">۷ روز</button>
    <button class="tf-btn active" id="tf30" onclick="setTF(this,30)">۳۰ روز</button>
    <button class="tf-btn" onclick="setTF(this,90)">۳ ماه</button>
    <button class="tf-btn" onclick="setTF(this,0)">از ابتدا</button>
    <button class="tf-btn" onclick="setTF(this,-1)">سفارشی ▾</button>
  </div>
  <div class="custom-range" id="customRange">
    <span>از</span>
    <input type="date" id="tfFrom" class="date-inp">
    <span>تا</span>
    <input type="date" id="tfTo" class="date-inp">
    <button class="tf-btn active" onclick="applyCustom()">اعمال</button>
  </div>
</div>

<!-- ── Q&A Activity ────────────────────────────────────────────────────── -->
<div class="section">
  <div class="section-title">Questions &amp; Answers Activity</div>
  <div class="grid-2">
    <div class="card">
      <div class="chart-title">Daily Questions &amp; Questioners</div>
      <div class="chart-wrap"><canvas id="cQuestions"></canvas></div>
    </div>
    <div class="card">
      <div class="chart-title">Daily Answers &amp; Answerers</div>
      <div class="chart-wrap"><canvas id="cAnswers"></canvas></div>
    </div>
  </div>
</div>

<!-- ── DAU / MAU (DMU) ────────────────────────────────────────────────── -->
<div class="section">
  <div class="section-title">Daily / Monthly Active Ratio (DMU)</div>
  <div class="kpi-grid">

    <div class="card kpi-card kpi-blue">
      <div class="kpi-icon">📅</div>
      <div class="kpi-label">MAU — Answerers</div>
      <div class="kpi-value">{fmt(d["mau_answerers"])}</div>
      <div class="kpi-sub">Unique answerers, last 30 days</div>
    </div>

    <div class="card kpi-card">
      <div class="kpi-icon">📆</div>
      <div class="kpi-label">DAU — Answerers</div>
      <div class="kpi-value">{fmt(d["dau_answerers"])}</div>
      <div class="kpi-sub">Unique answerers, yesterday</div>
    </div>

    <div class="card kpi-card kpi-green">
      <div class="kpi-icon">📊</div>
      <div class="kpi-label">DMU — Answerers</div>
      <div class="kpi-value">{d["dmu_answerers"]}%</div>
      <div class="kpi-sub">DAU ÷ MAU</div>
    </div>

    <div class="card kpi-card kpi-orange">
      <div class="kpi-icon">📅</div>
      <div class="kpi-label">MAU — Questioners</div>
      <div class="kpi-value">{fmt(d["mau_questioners"])}</div>
      <div class="kpi-sub">Unique questioners, last 30 days</div>
    </div>

    <div class="card kpi-card">
      <div class="kpi-icon">📆</div>
      <div class="kpi-label">DAU — Questioners</div>
      <div class="kpi-value">{fmt(d["dau_questioners"])}</div>
      <div class="kpi-sub">Unique questioners, yesterday</div>
    </div>

    <div class="card kpi-card kpi-brand">
      <div class="kpi-icon">📊</div>
      <div class="kpi-label">DMU — Questioners</div>
      <div class="kpi-value">{d["dmu_questioners"]}%</div>
      <div class="kpi-sub">DAU ÷ MAU</div>
    </div>

  </div>
</div>

<!-- ── Traffic & Engagement ──────────────────────────────────────────── -->
<div class="section">
  <div class="section-title">Traffic &amp; Engagement</div>
  <div class="kpi-grid">

    <div class="card kpi-card kpi-blue">
      <div class="kpi-icon">👥</div>
      <div class="kpi-label">Community DAU</div>
      <div class="kpi-value">{fmt(dau_yesterday)}</div>
      <div class="kpi-sub">Unique visitors, yesterday</div>
    </div>

    <div class="card kpi-card kpi-brand">
      <div class="kpi-icon">⚡</div>
      <div class="kpi-label">DEU</div>
      <div class="kpi-value">{fmt(deu_yesterday)}</div>
      <div class="kpi-sub">Daily Engaged Users, yesterday</div>
    </div>

    <div class="card kpi-card kpi-green">
      <div class="kpi-icon">📈</div>
      <div class="kpi-label">DEU / DAU</div>
      <div class="kpi-value">{deu_dau_pct}%</div>
      <div class="kpi-sub">Engagement rate (yesterday)</div>
    </div>

    <div class="card kpi-card kpi-orange">
      <div class="kpi-icon">🌍</div>
      <div class="kpi-label">Digikala DAU</div>
      <div class="kpi-value">{fmt(digi_yesterday)}</div>
      <div class="kpi-sub">Total Digikala unique visitors, yesterday</div>
    </div>

    <div class="card kpi-card kpi-teal">
      <div class="kpi-icon">🌐</div>
      <div class="kpi-label">DigiQ / Digikala DAU</div>
      <div class="kpi-value">{comm_digi_pct}%</div>
      <div class="kpi-sub">Community share of Digikala traffic</div>
    </div>

    <div class="card kpi-card kpi-purple">
      <div class="kpi-icon">🔗</div>
      <div class="kpi-label">Community Sessions</div>
      <div class="kpi-value">{fmt(comm_sess_yest)}</div>
      <div class="kpi-sub">Total sessions, yesterday</div>
    </div>

    <div class="card kpi-card">
      <div class="kpi-icon">🔗</div>
      <div class="kpi-label">Digikala Sessions</div>
      <div class="kpi-value">{fmt(digi_sess_yest)}</div>
      <div class="kpi-sub">Total sessions, yesterday</div>
    </div>

  </div>
  <div class="grid-2" style="margin-top:14px">
    <div class="card">
      <div class="chart-title">DAU &amp; DEU — Community <span class="chart-sub">نسبت DEU/DAU بر روی محور راست</span></div>
      <div class="chart-wrap h300"><canvas id="cDauTrend"></canvas></div>
    </div>
    <div class="card">
      <div class="chart-title">Sessions: Community vs Digikala <span class="chart-sub">— last 30 days (محور دوگانه)</span></div>
      <div class="chart-wrap h300"><canvas id="cSessions"></canvas></div>
    </div>
  </div>
  <div class="grid-1" style="margin-top:14px">
    <div class="card">
      <div class="chart-title">Community DAU / Digikala DAU <span class="chart-sub">— سهم ترافیک کامیونیتی از کل دیجیکالا (%) — last 30 days</span></div>
      <div class="chart-wrap h220"><canvas id="cDauShare"></canvas></div>
    </div>
  </div>
</div>

<!-- ── Response Time ─────────────────────────────────────────────────── -->
<div class="section">
  <div class="section-title">Response Time Quality</div>
  <div class="grid-3-1">
    <div class="card">
      <div class="chart-title">% Questions Answered within 24h <span class="chart-sub">— last 30 days trend</span></div>
      <div class="chart-wrap h260"><canvas id="cResp"></canvas></div>
    </div>
    <div class="resp-panel">
      <div class="resp-card">
        <div class="resp-pct" style="color:var(--green)">{d["pct_24h"]}%</div>
        <div class="resp-label">Answered within 24 h</div>
      </div>
      <div class="resp-card">
        <div class="resp-pct" style="color:var(--blue)">{d["pct_3h"]}%</div>
        <div class="resp-label">Answered within 3 h</div>
      </div>
      <div class="resp-card">
        <div class="resp-pct" style="color:var(--brand)">{d["pct_1h"]}%</div>
        <div class="resp-label">Answered within 1 h</div>
      </div>
    </div>
  </div>
</div>

<!-- ── Community Rankings ────────────────────────────────────────────── -->
<div class="section">
  <div class="section-title">Community Rankings</div>
  <div class="grid-2">
    <div class="card">
      <div class="chart-title">Top Communities by Members</div>
      <div class="chart-wrap h300"><canvas id="cCommMem"></canvas></div>
    </div>
    <div class="card">
      <div class="chart-title">Top Communities by Questions</div>
      <div class="chart-wrap h300"><canvas id="cCommQ"></canvas></div>
    </div>
  </div>
</div>

<!-- ── Content Health ────────────────────────────────────────────────── -->
<div class="section">
  <div class="section-title">Content Health</div>
  <div class="grid-2">
    <div class="card">
      <div class="chart-title">Answer Count Distribution</div>
      <div class="chart-wrap h260"><canvas id="cAnsDist"></canvas></div>
    </div>
    <div class="card">
      <div class="chart-title">Daily Community Joins <span class="chart-sub">— last {TREND_DAYS} days</span></div>
      <div class="chart-wrap h260"><canvas id="cJoins"></canvas></div>
    </div>
  </div>
</div>

<!-- ── Top Questions ─────────────────────────────────────────────────── -->
<div class="section">
  <div class="section-title">Top Upvoted Questions</div>
  <div class="card">
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>#</th><th>Community</th><th>Question</th>
            <th style="text-align:right">Upvotes</th>
            <th style="text-align:right">Answers</th>
          </tr>
        </thead>
        <tbody>{upvoted_rows}</tbody>
      </table>
    </div>
  </div>
</div>

<div class="section">
  <div class="section-title">Most Answered Questions</div>
  <div class="card">
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>#</th><th>Community</th><th>Question</th>
            <th style="text-align:right">Answers</th>
            <th style="text-align:right">Upvotes</th>
          </tr>
        </thead>
        <tbody>{answered_rows}</tbody>
      </table>
    </div>
  </div>
</div>

</div><!-- /.container -->

<div class="footer">DigiKala Community Dashboard &nbsp;·&nbsp; Generated {generated_at} &nbsp;·&nbsp; Data: social schema</div>

<script>
Chart.defaults.font.family = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif";
Chart.defaults.font.size   = 11;
Chart.defaults.color       = "#718096";
Chart.defaults.plugins.tooltip.backgroundColor = "rgba(18,21,30,0.92)";
Chart.defaults.plugins.tooltip.padding         = 10;
Chart.defaults.plugins.tooltip.cornerRadius    = 8;
Chart.defaults.plugins.tooltip.titleFont       = {{ size: 12, weight: "600" }};

const C = {{
  red:    "#EE3844", green:  "#38A169", blue:   "#3182CE",
  orange: "#DD6B20", purple: "#805AD5", teal:   "#2C7A7B",
}};

function line(id, labels, datasets) {{
  return new Chart(document.getElementById(id), {{
    type: "line",
    data: {{ labels, datasets }},
    options: {{
      responsive: true, maintainAspectRatio: false,
      interaction: {{ mode: "index", intersect: false }},
      plugins: {{ legend: {{ position: "top", labels: {{ boxWidth: 10, padding: 12, usePointStyle: true }} }} }},
      scales: {{
        x: {{ grid: {{ display: false }}, ticks: {{ maxTicksLimit: 10, maxRotation: 0 }} }},
        y: {{ grid: {{ color: "#EDF2F7" }}, beginAtZero: true }}
      }}
    }}
  }});
}}

function bar(id, labels, data, color, horizontal) {{
  return new Chart(document.getElementById(id), {{
    type: "bar",
    data: {{ labels, datasets: [{{ data, backgroundColor: color, borderRadius: 4, borderSkipped: false }}] }},
    options: {{
      responsive: true, maintainAspectRatio: false,
      indexAxis: horizontal ? "y" : "x",
      plugins: {{ legend: {{ display: false }} }},
      scales: {{
        x: {{ grid: {{ display: !!horizontal }}, ticks: {{ maxTicksLimit: 6 }} }},
        y: {{ grid: {{ display: !horizontal }}, ticks: {{ font: {{ size: 10 }} }} }}
      }}
    }}
  }});
}}

// ─── Full datasets for time-range filtering ───────────────────────────────────
const FULL = {{
  eng:   {{ raw: {j_all_eng_raw},   lb: {j_all_eng_labels},   v: [{j_all_eng_vals}] }},
  q:     {{ raw: {j_all_q_raw},     lb: {j_all_q_labels},     v: [{j_all_q_vals}, {j_all_qers}] }},
  a:     {{ raw: {j_all_a_raw},     lb: {j_all_a_labels},     v: [{j_all_a_vals}, {j_all_aers}] }},
  joins: {{ raw: {j_all_joins_raw}, lb: {j_all_joins_labels}, v: [{j_all_joins_vals}] }},
}};

const charts = {{}};

function applyRange(days, from, to) {{
  const today = new Date().toISOString().slice(0,10);
  if (days > 0)  {{ to = today; const d = new Date(); d.setDate(d.getDate()-days); from = d.toISOString().slice(0,10); }}
  else if (days === 0) {{ from = '{LAUNCH_DATE[:10]}'; to = today; }}
  const MAP = {{ cEngaged: FULL.eng, cQuestions: FULL.q, cAnswers: FULL.a, cJoins: FULL.joins }};
  Object.entries(MAP).forEach(([id, src]) => {{
    const ch = charts[id]; if (!ch) return;
    const ix = src.raw.reduce((a,dt,i) => {{ if (dt>=from && dt<=to) a.push(i); return a; }}, []);
    ch.data.labels = ix.map(i => src.lb[i]);
    ch.data.datasets.forEach((ds,j) => {{ ds.data = ix.map(i => src.v[j][i]); }});
    ch.update('none');
  }});
}}

function setTF(el, days) {{
  document.querySelectorAll('.tf-btn').forEach(b => b.classList.remove('active'));
  el.classList.add('active');
  document.getElementById('customRange').style.display = (days === -1) ? 'flex' : 'none';
  if (days !== -1) applyRange(days);
}}

function applyCustom() {{
  const from = document.getElementById('tfFrom').value;
  const to   = document.getElementById('tfTo').value;
  if (from && to) applyRange(-1, from, to);
}}

// ─── Social-schema charts (filterable) ────────────────────────────────────────
charts.cEngaged = line("cEngaged", {j_eng_dates}, [{{
  label: "Engaged Users",
  data: {j_eng_vals},
  borderColor: C.red, backgroundColor: "rgba(238,56,68,0.1)",
  fill: true, tension: 0.35, pointRadius: 1.5, pointHoverRadius: 5, borderWidth: 2
}}]);

charts.cQuestions = line("cQuestions", {j_q_dates}, [
  {{ label: "Questions",   data: {j_q_vals}, borderColor: C.blue,   backgroundColor: "rgba(49,130,206,0.1)", fill: true, tension: 0.3, pointRadius: 1.5, borderWidth: 2 }},
  {{ label: "Questioners", data: {j_qers},   borderColor: C.purple, backgroundColor: "transparent", tension: 0.3, pointRadius: 1.5, borderWidth: 2, borderDash: [4,3] }}
]);

charts.cAnswers = line("cAnswers", {j_a_dates}, [
  {{ label: "Answers",   data: {j_a_vals}, borderColor: C.green,  backgroundColor: "rgba(56,161,105,0.1)", fill: true, tension: 0.3, pointRadius: 1.5, borderWidth: 2 }},
  {{ label: "Answerers", data: {j_aers},   borderColor: C.orange, backgroundColor: "transparent", tension: 0.3, pointRadius: 1.5, borderWidth: 2, borderDash: [4,3] }}
]);

charts.cJoins = bar("cJoins", {j_joins_dates}, {j_joins_vals}, "rgba(128,90,213,0.62)", false);

// Apply default 30-day view
applyRange(30);

// ─── Static charts ────────────────────────────────────────────────────────────
// Monthly engaged
bar("cMonthly", {j_monthly_mo}, {j_monthly_v}, "rgba(238,56,68,0.72)", false);

// 24h response trend (fixed 30-day window, not filterable)
line("cResp", {j_resp_dates}, [{{
  label: "% Answered in 24h",
  data: {j_resp_24h},
  borderColor: C.green, backgroundColor: "rgba(56,161,105,0.1)",
  fill: true, tension: 0.3, pointRadius: 1.5, borderWidth: 2
}}]);

// Top communities — members
bar("cCommMem", {j_comm_names}, {j_comm_mem}, "rgba(238,56,68,0.68)", true);

// Top communities — questions
bar("cCommQ", {j_comm_q_names}, {j_comm_q_vals}, "rgba(49,130,206,0.68)", true);

// Answer distribution (doughnut)
new Chart(document.getElementById("cAnsDist"), {{
  type: "doughnut",
  data: {{
    labels: {j_ans_labels},
    datasets: [{{
      data: {j_ans_vals},
      backgroundColor: [C.red, C.blue, C.green, C.orange, C.purple],
      borderWidth: 2, borderColor: "#fff", hoverOffset: 8
    }}]
  }},
  options: {{
    responsive: true, maintainAspectRatio: false,
    cutout: "62%",
    plugins: {{
      legend: {{ position: "right", labels: {{ boxWidth: 12, padding: 14, font: {{ size: 11 }} }} }}
    }}
  }}
}});

// ─── Traffic & Engagement charts (ClickHouse — fixed 30-day window) ───────────
// DAU + DEU combo (grouped bars) with DEU/DAU% line on second axis
new Chart(document.getElementById("cDauTrend"), {{
  type: "bar",
  data: {{
    labels: {j_dau_dates},
    datasets: [
      {{ type:"bar",  label:"Community DAU", data:{j_dau_comm},     backgroundColor:"rgba(49,130,206,0.75)", borderRadius:3, yAxisID:"yL", order:2 }},
      {{ type:"bar",  label:"DEU",           data:{j_deu_aligned},  backgroundColor:"rgba(238,56,68,0.72)",  borderRadius:3, yAxisID:"yL", order:2 }},
      {{ type:"line", label:"DEU/DAU %",     data:{j_deu_dau_daily},borderColor:C.green, backgroundColor:"transparent",
         pointRadius:2, pointHoverRadius:5, borderWidth:2, tension:0.3, yAxisID:"yR", order:1 }}
    ]
  }},
  options:{{
    responsive:true, maintainAspectRatio:false,
    interaction:{{mode:"index",intersect:false}},
    plugins:{{legend:{{position:"top",labels:{{boxWidth:10,padding:10,usePointStyle:true}}}}}},
    scales:{{
      yL:{{ type:"linear", position:"left",  grid:{{color:"#EDF2F7"}}, beginAtZero:true,
             title:{{display:true, text:"Users", font:{{size:10}}}} }},
      yR:{{ type:"linear", position:"right", grid:{{drawOnChartArea:false}}, beginAtZero:true,
             ticks:{{callback:v=>v+"%"}}, title:{{display:true, text:"DEU/DAU %", font:{{size:10}}}} }},
      x:{{ grid:{{display:false}}, ticks:{{maxTicksLimit:10,maxRotation:0}} }}
    }}
  }}
}});

// Sessions: Community (left axis) vs Digikala (right axis)
new Chart(document.getElementById("cSessions"), {{
  type: "line",
  data: {{
    labels: {j_dau_dates},
    datasets: [
      {{ label:"Community Sessions", data:{j_comm_sessions}, borderColor:C.blue,   backgroundColor:"rgba(49,130,206,0.08)",
         fill:true, tension:0.3, pointRadius:1.5, borderWidth:2, yAxisID:"yL" }},
      {{ label:"Digikala Sessions",  data:{j_digi_sessions}, borderColor:C.orange, backgroundColor:"rgba(221,107,32,0.08)",
         fill:true, tension:0.3, pointRadius:1.5, borderWidth:2, yAxisID:"yR" }}
    ]
  }},
  options:{{
    responsive:true, maintainAspectRatio:false,
    interaction:{{mode:"index",intersect:false}},
    plugins:{{legend:{{position:"top",labels:{{boxWidth:10,padding:10,usePointStyle:true}}}}}},
    scales:{{
      yL:{{ type:"linear", position:"left",  grid:{{color:"#EDF2F7"}}, beginAtZero:true,
             title:{{display:true, text:"Community", font:{{size:10}}}} }},
      yR:{{ type:"linear", position:"right", grid:{{drawOnChartArea:false}}, beginAtZero:true,
             title:{{display:true, text:"Digikala", font:{{size:10}}}} }},
      x:{{ grid:{{display:false}}, ticks:{{maxTicksLimit:10,maxRotation:0}} }}
    }}
  }}
}});

// Community DAU / Digikala DAU share %
line("cDauShare", {j_dau_dates}, [{{
  label: "Community / Digikala DAU %",
  data: {j_dau_share_pct},
  borderColor: C.teal, backgroundColor: "rgba(44,122,123,0.1)",
  fill: true, tension:0.35, pointRadius:2, pointHoverRadius:5, borderWidth:2
}}]);
</script>
</body>
</html>"""


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    print("=" * 55)
    print("  DigiKala Community Dashboard Generator")
    print("=" * 55)

    # Validate config
    if not DB_CONFIG["host"] or not DB_CONFIG["user"]:
        print("\n⚠️  No credentials found. Create a config.py (see config.example.py) or set env vars.\n")
        sys.exit(1)

    print(f"\n🔌 Connecting to {DB_CONFIG['host']}:{DB_CONFIG['port']} …")
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        print("   ✓ Connected")
    except mysql.connector.Error as e:
        print(f"\n❌ Connection failed: {e}")
        sys.exit(1)

    print("\n📊 Running queries …")
    try:
        data = fetch(conn)
    except mysql.connector.Error as e:
        print(f"\n❌ Query error: {e}")
        sys.exit(1)
    finally:
        conn.close()

    print(f"   ✓ Total engaged users : {data['total_engaged']:,}")
    print(f"   ✓ Questions           : {data['total_questions']:,}")
    print(f"   ✓ Answers             : {data['total_answers']:,}")
    print(f"   ✓ Communities         : {data['total_communities']:,}")

    print(f"\n🔌 Connecting to ClickHouse ({BIGDATA_CONFIG['host']}:{BIGDATA_CONFIG['port']}) …")
    bigdata = fetch_bigdata()
    if bigdata:
        data.update(bigdata)
        print(f"   ✓ Community DAU (yesterday) : {data.get('dau_community_yesterday', 0):,}")
        print(f"   ✓ Digikala DAU  (yesterday) : {data.get('dau_digikala_yesterday', 0):,}")
    else:
        print("   ⚠️  Traffic data unavailable — Traffic section will show zeros")
        data.update({
            "dau_dates":               [],
            "dau_community_vals":      [],
            "dau_digikala_vals":       [],
            "dau_community_yesterday": 0,
            "dau_digikala_yesterday":  0,
        })

    generated_at = jalali_now()
    print("\n🎨 Building HTML …")
    html = generate_html(data, generated_at)

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"\n✅ Dashboard saved → {OUTPUT_FILE}")
    print("   Open in any browser. Share the HTML file directly — it's self-contained.")
    print("\nTo schedule (cron, daily 6 AM):")
    print(f"   0 6 * * * cd $(pwd) && python3 generate_dashboard.py >> dashboard.log 2>&1\n")


if __name__ == "__main__":
    main()
