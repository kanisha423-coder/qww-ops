"""
Daily Bland dashboard runner.  

Does two things in one pass:

  1. Writes /data/dashboard.json (consumed by GitHub Pages site)

  2. Emails an HTML digest to your Gmail

Env vars required:

  BLAND_API_KEY          Bland AI API key

  GMAIL_ADDRESS          your Gmail (sender and recipient)

  GMAIL_APP_PASSWORD     Gmail app password

  QUILL_PATHWAY_ID       pathway ID for Quill

  WALTER_PATHWAY_ID      pathway ID for Walter

  WILLOW_PATHWAY_ID      pathway ID for Willow

  TIMEZONE               e.g. "America/New_York"

  SKIP_EMAIL             optional, "1" to skip email (for testing)

"""

import os

import sys

import json

from datetime import datetime, timedelta, timezone

from collections import Counter, defaultdict

from zoneinfo import ZoneInfo

import smtplib

from email.mime.multipart import MIMEMultipart

from email.mime.text import MIMEText

import requests

from jinja2 import Environment, FileSystemLoader

BLAND_API = "https://api.bland.ai/v1"

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def env(name, required=True, default=None):

    v = os.environ.get(name, default)

    if required and not v:

        print(f"ERROR: missing env var {name}", file=sys.stderr)

        sys.exit(1)

    return v

CFG = {

    "bland_key": env("BLAND_API_KEY"),

    "gmail_addr": env("GMAIL_ADDRESS"),

    "gmail_pass": env("GMAIL_APP_PASSWORD"),

    "pathways": {

        "Quill": env("QUILL_PATHWAY_ID"),

        "Walter": env("WALTER_PATHWAY_ID"),

        "Willow": env("WILLOW_PATHWAY_ID"),

    },

    "tz": ZoneInfo(env("TIMEZONE", required=False, default="UTC")),

    "skip_email": env("SKIP_EMAIL", required=False, default="") == "1",

    "slack_webhook": env("SLACK_WEBHOOK_URL", required=False, default=""),

}

# --------------------------------------------------------------------------

# Bland API

# --------------------------------------------------------------------------

def bland_get(path, params=None):

    r = requests.get(

        f"{BLAND_API}{path}",

        headers={"authorization": CFG["bland_key"]},

        params=params or {},

        timeout=30,

    )

    r.raise_for_status()

    return r.json()

def list_calls_since(start_dt):

    """Fetch all calls created on/after start_dt (UTC datetime)."""

    calls = []

    page_size = 1000

    cursor = 0

    while True:

        page = bland_get("/calls", params={"from": cursor, "to": cursor + page_size})

        batch = page.get("calls", [])

        if not batch:

            break

        calls.extend(batch)

        oldest = batch[-1].get("created_at")

        if oldest:

            try:

                if datetime.fromisoformat(oldest.replace("Z", "+00:00")) < start_dt:

                    break

            except Exception:

                pass

        if len(batch) < page_size:

            break

        cursor += page_size

    out = []

    for c in calls:

        created = c.get("created_at")

        if not created:

            continue

        try:

            dt = datetime.fromisoformat(created.replace("Z", "+00:00"))

            if dt >= start_dt:

                out.append(c)

        except Exception:

            continue

    return out

def get_call_detail(call_id):

    return bland_get(f"/calls/{call_id}")

# --------------------------------------------------------------------------

# Classification

# --------------------------------------------------------------------------

def bot_name_for(call_summary, call_detail):

    pid = call_detail.get("pathway_id") or call_summary.get("pathway_id")

    for name, target in CFG["pathways"].items():

        if pid and target and pid == target:

            return name

    return None

def passed(call_detail):

    """A pass is variables.passed == true."""

    vars_ = call_detail.get("variables", {}) or {}

    v = vars_.get("passed")

    if isinstance(v, bool):

        return v

    if isinstance(v, str):

        return v.strip().lower() in ("true", "1", "yes")

    return False

def dropout_question(call_detail):

    """Find the last question reached. Returns 'Q2' format or None."""

    vars_ = call_detail.get("variables", {}) or {}

    for key in ("last_question", "current_question", "question_reached", "dropout_question"):

        if key in vars_ and vars_[key] is not None:

            val = str(vars_[key]).strip()

            if val.upper().startswith("Q"):

                return val.upper()

            if val.isdigit():

                return f"Q{val}"

    return None

def caller_label(call_summary, call_detail):

    vars_ = call_detail.get("variables", {}) or {}

    first = vars_.get("first_name") or vars_.get("firstName")

    last = vars_.get("last_name") or vars_.get("lastName")

    if first and last:

        return f"{first} {last}".strip()

    if first:

        return str(first)

    phone = call_summary.get("from") or call_summary.get("to") or "unknown"

    return phone

# --------------------------------------------------------------------------

# Aggregation

# --------------------------------------------------------------------------

def build_metrics(calls_with_details):

    by_bot = defaultdict(lambda: {

        "total": 0, "passed": 0, "failed": 0,

        "unique_callers": set(), "unique_passers": set(),

        "dropout_counter": Counter(),

        "caller_attempts": defaultdict(lambda: {"attempts": 0, "passes": 0}),

        "daily": defaultdict(lambda: {"total": 0, "passed": 0, "failed": 0}),

    })

    for summary, detail in calls_with_details:

        bot = bot_name_for(summary, detail)

        if not bot:

            continue

        g = by_bot[bot]

        did_pass = passed(detail)

        caller = caller_label(summary, detail)

        dq = dropout_question(detail)

        g["total"] += 1

        g["unique_callers"].add(caller)

        if did_pass:

            g["passed"] += 1

            g["unique_passers"].add(caller)

        else:

            g["failed"] += 1

            g["dropout_counter"][dq or "Q0 (never started)"] += 1

        g["caller_attempts"][caller]["attempts"] += 1

        if did_pass:

            g["caller_attempts"][caller]["passes"] += 1

        try:

            created = datetime.fromisoformat(

                summary["created_at"].replace("Z", "+00:00")

            ).astimezone(CFG["tz"])

            day_key = created.strftime("%Y-%m-%d")

            g["daily"][day_key]["total"] += 1

            if did_pass:

                g["daily"][day_key]["passed"] += 1

            else:

                g["daily"][day_key]["failed"] += 1

        except Exception:

            pass

    out = {}

    for bot, g in by_bot.items():

        unique_caller_count = len(g["unique_callers"])

        unique_passer_count = len(g["unique_passers"])

        pass_rate = (g["passed"] / g["total"] * 100) if g["total"] else 0.0

        biggest = g["dropout_counter"].most_common(1)[0] if g["dropout_counter"] else (None, 0)

        leaderboard = []

        for name, s in g["caller_attempts"].items():

            if s["attempts"] >= 2:

                rate = s["passes"] / s["attempts"]

                leaderboard.append({

                    "name": name,

                    "passes": s["passes"],

                    "attempts": s["attempts"],

                    "rate": round(rate, 4),

                })

        leaderboard.sort(key=lambda x: (-x["rate"], -x["attempts"]))

        daily_sorted = sorted(g["daily"].items())

        out[bot] = {

            "total": g["total"],

            "passed": g["passed"],

            "failed": g["failed"],

            "unique_caller_count": unique_caller_count,

            "unique_passer_count": unique_passer_count,

            "pass_rate": round(pass_rate, 2),

            "biggest_drop": {"question": biggest[0], "count": biggest[1]},

            "dropout_breakdown": [

                {"question": q, "count": c}

                for q, c in g["dropout_counter"].most_common()

            ],

            "leaderboard": leaderboard,

            "daily": [

                {"date": day, "total": d["total"], "passed": d["passed"], "failed": d["failed"]}

                for day, d in daily_sorted

            ],

        }

    for bot in ("Quill", "Walter", "Willow"):

        if bot not in out:

            out[bot] = {

                "total": 0, "passed": 0, "failed": 0,

                "unique_caller_count": 0, "unique_passer_count": 0,

                "pass_rate": 0.0,

                "biggest_drop": {"question": None, "count": 0},

                "dropout_breakdown": [], "leaderboard": [], "daily": [],

            }

    return out

# --------------------------------------------------------------------------

# Output

# --------------------------------------------------------------------------

def write_json(mtd, yesterday, as_of):

    payload = {

        "generated_at": as_of.isoformat(),

        "month_label": as_of.strftime("%B %Y"),

        "yesterday_label": (as_of - timedelta(days=1)).strftime("%A, %b %-d"),

        "mtd": mtd,

        "yesterday": yesterday,

    }

    out_path = os.path.join(ROOT, "data", "dashboard.json")

    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    with open(out_path, "w") as f:

        json.dump(payload, f, indent=2, default=str)

    print(f"Wrote {out_path}")

def render_and_send_email(mtd, yesterday, as_of):
    tpl_env = Environment(

        loader=FileSystemLoader(os.path.join(ROOT, "templates")),

        autoescape=True,

    )

    tmpl = tpl_env.get_template("email.html.j2")

    html = tmpl.render(

        mtd=mtd, yesterday=yesterday, as_of=as_of,

        month_label=as_of.strftime("%B %Y"),

        yesterday_label=(as_of - timedelta(days=1)).strftime("%A, %b %-d"),

    )

    subject = f"Quill Walter & Willow — Daily Review {as_of.strftime('%b %-d, %Y')}"

    msg = MIMEMultipart("alternative")

    msg["Subject"] = subject

    msg["From"] = CFG["gmail_addr"]

    msg["To"] = CFG["gmail_addr"]

    msg.attach(MIMEText("HTML required to view.", "plain"))

    msg.attach(MIMEText(html, "html"))

    if CFG["skip_email"]:

        print("SKIP_EMAIL=1, not sending.")

        return

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:

        s.login(CFG["gmail_addr"], CFG["gmail_pass"])

        s.sendmail(CFG["gmail_addr"], [CFG["gmail_addr"]], msg.as_string())

    print("Email sent.")

# --------------------------------------------------------------------------

# Slack

# --------------------------------------------------------------------------

def send_slack_report(mtd, as_of):
    """Post a training score summary to Slack via Incoming Webhook."""
    webhook = CFG.get("slack_webhook", "")
    if not webhook:
        print("No SLACK_WEBHOOK_URL set, skipping Slack notification.")
        return

    lines = [
        f":bar_chart: *Training Score Report — {as_of.strftime('%b %-d, %Y %I:%M %p %Z')}*",
        "",
    ]

    for bot, data in sorted(mtd.items()):
        total = data.get("total", 0)
        passed = data.get("passed", 0)
        rate = data.get("pass_rate", 0.0)
        emoji = ":white_check_mark:" if rate >= 80 else ":warning:" if rate >= 50 else ":x:"
        lines.append(f"{emoji} *{bot}* — {passed}/{total} passed ({rate:.1f}%)")

        # Top 3 leaderboard
        board = data.get("leaderboard", [])[:3]
        if board:
            lines.append("  _Top trainees:_")
            for rank, entry in enumerate(board, 1):
                medal = [":first_place_medal:", ":second_place_medal:", ":third_place_medal:"][rank - 1]
                lines.append(
                    f"  {medal} {entry['name']} — {entry['passes']}/{entry['attempts']} "
                    f"({entry['rate']*100:.0f}%)"
                )
        lines.append("")

    payload = {"text": "\n".join(lines)}
    try:
        resp = requests.post(webhook, json=payload, timeout=10)
        resp.raise_for_status()
        print("Slack notification sent.")
    except Exception as e:
        print(f"Slack notification failed: {e}")

# --------------------------------------------------------------------------

# Main

# --------------------------------------------------------------------------

def main():

    now_local = datetime.now(CFG["tz"])

    mtd_start_local = now_local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    mtd_start_utc = mtd_start_local.astimezone(timezone.utc)

    print(f"Fetching calls since {mtd_start_utc.isoformat()} ...")

    calls = list_calls_since(mtd_start_utc)

    print(f"Got {len(calls)} calls.")

    enriched = []

    for i, c in enumerate(calls):

        try:

            d = get_call_detail(c["call_id"])

            enriched.append((c, d))

        except Exception as e:

            print(f"  skip {c.get('call_id')}: {e}")

        if (i + 1) % 50 == 0:

            print(f"  hydrated {i+1}/{len(calls)}")

    mtd = build_metrics(enriched)

    yesterday_start = (now_local - timedelta(days=1)).replace(

        hour=0, minute=0, second=0, microsecond=0

    )

    yesterday_end = yesterday_start + timedelta(days=1)

    yday_calls = []

    for (s, d) in enriched:

        try:

            dt = datetime.fromisoformat(s["created_at"].replace("Z", "+00:00")).astimezone(CFG["tz"])

            if yesterday_start <= dt < yesterday_end:

                yday_calls.append((s, d))

        except Exception:

            pass

    yesterday = build_metrics(yday_calls)

    write_json(mtd, yesterday, now_local)

    render_and_send_email(mtd, yesterday, now_local)

    send_slack_report(mtd, now_local)

if __name__ == "__main__":

    main()
