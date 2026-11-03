"""usage operations and persistence."""

import json
import uuid
from datetime import datetime, timezone

from fastapi import HTTPException

from .. import pricing
from .common import audit


def now():
    return datetime.now(timezone.utc).isoformat()


def begin_attempt(db, render):
    attempt = uuid.uuid4().hex
    with db() as c:
        c.execute(
            "INSERT INTO usage_ledger(attempt_id,render_id,job_id,user,model,kind,rate_card_version,rates_json,started_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (
                attempt,
                render["id"],
                render["job_id"],
                render["user"],
                render["model"],
                render["kind"],
                pricing.VERSION,
                json.dumps(pricing.rate_card(render["model"])),
                now(),
            ),
        )
    return attempt


def record_response(db, attempt, response):
    raw = getattr(response, "usage", None)
    if hasattr(raw, "model_dump"):
        raw = raw.model_dump(mode="json")
    if not isinstance(raw, dict):
        raw = None
    with db() as c:
        c.execute("BEGIN IMMEDIATE")
        row = c.execute(
            "SELECT * FROM usage_ledger WHERE attempt_id=?", (attempt,)
        ).fetchone()
        if not row:
            raise RuntimeError("Missing provider-attempt record")
        if row["received_at"] is not None:
            return  # A duplicated callback cannot rewrite an accepted receipt.
        measured = pricing.estimate(raw, json.loads(row["rates_json"]))
        c.execute(
            """UPDATE usage_ledger SET status='response_received',request_id=?,usage_json=?,
            input_tokens=?,image_input_tokens=?,text_input_tokens=?,output_tokens=?,image_output_tokens=?,text_output_tokens=?,
            cost_microusd=?,meter_status=?,received_at=? WHERE attempt_id=?""",
            (
                getattr(response, "_request_id", None),
                json.dumps(raw),
                *(
                    measured[key]
                    for key in (
                        "input_tokens",
                        "image_input_tokens",
                        "text_input_tokens",
                        "output_tokens",
                        "image_output_tokens",
                        "text_output_tokens",
                        "cost_microusd",
                        "meter_status",
                    )
                ),
                now(),
                attempt,
            ),
        )


def finish_attempt(db, attempt, succeeded):
    if not attempt:
        return
    with db() as c:
        c.execute(
            "UPDATE usage_ledger SET status=CASE WHEN ? THEN 'completed' WHEN received_at IS NOT NULL THEN 'failed' ELSE 'unknown' END,finished_at=? WHERE attempt_id=?",
            (succeeded, now(), attempt),
        )


def summary(db):
    with db() as c:
        c.execute("BEGIN")  # All dashboard totals describe the same database snapshot.
        totals = dict(
            c.execute("""SELECT COUNT(*) requests, COALESCE(SUM(cost_microusd),0) cost,
            COALESCE(SUM(cost_microusd IS NULL),0) unreported,
            COALESCE(SUM(status IN ('started','response_received')),0) pending,
            COALESCE(SUM(image_input_tokens),0) image_input_tokens, COALESCE(SUM(text_input_tokens),0) text_input_tokens,
            COALESCE(SUM(image_output_tokens),0) image_output_tokens, COALESCE(SUM(text_output_tokens),0) text_output_tokens,
            MIN(started_at) tracking_since FROM usage_ledger""").fetchone()
        )
        groups = {}
        for group in ("user", "model"):
            rows = c.execute(f"""SELECT {group},COUNT(*) requests,COALESCE(SUM(cost_microusd),0) cost,
                SUM(cost_microusd IS NULL) unreported FROM usage_ledger GROUP BY {group} ORDER BY cost DESC""").fetchall()
            groups["by_" + group] = [
                {
                    group: row[group],
                    "requests": row["requests"],
                    "tracked_usd": row["cost"] / 1_000_000,
                    "unreported": row["unreported"],
                }
                for row in rows
            ]
        checkpoint = c.execute("SELECT * FROM usage_budget WHERE id=1").fetchone()
        budget = None
        if checkpoint:
            later = c.execute(
                "SELECT COALESCE(SUM(cost_microusd),0) cost,COUNT(*)-COUNT(cost_microusd) unknown FROM usage_ledger WHERE id>?",
                (checkpoint["ledger_boundary"],),
            ).fetchone()
            balance = checkpoint["balance_microusd"]
            remaining = max(0, balance - later["cost"])
            percent = round(remaining / balance * 100, 1) if balance else 0
            budget = {
                "balance_checkpoint_usd": balance / 1_000_000,
                "estimated_remaining_usd": remaining / 1_000_000,
                "tracked_since_checkpoint_usd": later["cost"] / 1_000_000,
                "remaining_percent": percent,
                "alert_percent": checkpoint["alert_percent"],
                "updated_at": checkpoint["updated_at"],
                "unreported_since_checkpoint": later["unknown"],
                "incomplete": bool(later["unknown"]),
                "low": percent <= checkpoint["alert_percent"],
            }
    totals["tracked_spend_usd"] = totals.pop("cost") / 1_000_000
    return {
        **totals,
        **groups,
        "balance_estimate": budget,
        "rate_card_version": pricing.VERSION,
        "pricing_source": pricing.SOURCE,
        "notice": "Estimated StudioFlow spending only. Missing usage is unknown, not free. Excludes other apps, taxes and unrecorded historical requests. Check OpenAI Billing for the actual balance.",
    }


def get_usage(workspace, user):
    return {**summary(workspace.db), "can_edit_budget": workspace.is_admin(user)}


def save_checkpoint(workspace, body, user):
    if not workspace.is_admin(user):
        raise HTTPException(
            403, "Only a workspace owner can update the balance checkpoint"
        )
    with workspace.db() as c:
        c.execute("BEGIN IMMEDIATE")
        if c.execute(
            "SELECT 1 FROM usage_ledger WHERE status IN ('started','response_received') LIMIT 1"
        ).fetchone():
            raise HTTPException(
                409,
                "Wait for active image requests to finish, then check OpenAI Billing again before saving the checkpoint",
            )
        boundary = c.execute("SELECT COALESCE(MAX(id),0) FROM usage_ledger").fetchone()[
            0
        ]
        amount = int(body.balance_checkpoint_usd * 1_000_000)
        c.execute(
            """INSERT INTO usage_budget VALUES(1,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
            balance_microusd=excluded.balance_microusd,ledger_boundary=excluded.ledger_boundary,
            alert_percent=excluded.alert_percent,updated_at=excluded.updated_at,updated_by=excluded.updated_by""",
            (amount, boundary, body.alert_percent, now(), user),
        )
        audit(
            c,
            user,
            "usage_checkpoint_updated",
            "",
            f"{body.balance_checkpoint_usd} USD; threshold {body.alert_percent}%",
        )
    return {"ok": True}
