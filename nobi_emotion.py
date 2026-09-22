#!/usr/bin/env python3
"""NOBI emotion / self-regulate loop (v2 brain).

Every run (scheduled every 30 min):
  1. Reads real closed deals (MQL5\\Files\\nobi_deals.sig) and computes mood:
       calm -> cautious -> strict -> paused based on loss streaks + drawdown.
  2. Auto-tunes the bridge: raises min_abs when losing (fewer, higher-quality
     signals) and pauses trading (min_abs huge) when bleeding; restores when
     recovery is confirmed. The bridge hot-reloads nobi_config.json live.
  3. Auto-applies the best NET-of-cost config from nobi_backtest.py
     (recommended_v2.json) when it clears PF>=2 with >=20 trades.
  4. Flags "call the AI assistant" (ai/assistant_call.txt) on strict/pause.

State: ai/emotion_state.json  Log: ai/emotions.log
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
AI_DIR = HERE / "ai"
STATE = AI_DIR / "emotion_state.json"
LOG = AI_DIR / "emotions.log"
CALL = AI_DIR / "assistant_call.txt"
RECO = AI_DIR / "recommended_v2.json"
CFG = HERE / "nobi_config.json"
DEALS = HERE.parent / "D0E8209F77C8CF37AD8BF550E51FF075" / "MQL5" / "Files" / "nobi_deals.sig"
if not DEALS.exists():
    DEALS = Path(r"C:\Users\Administrator\AppData\Roaming\MetaQuotes\Terminal\D0E8209F77C8CF37AD8BF550E51FF075\MQL5\Files\nobi_deals.sig")

FACTOR = {"calm": 1.0, "cautious": 1.5, "strict": 2.0, "paused": 100000.0}


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def log(msg: str) -> None:
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write(f"{now()}  {msg}\n")


def tail(path: Path, n: int = 400) -> list[str]:
    rows = []
    with open(path, "rb") as fh:
        fh.seek(0, 2)
        size = fh.tell()
        pos, chunk, tailb = size, 1 << 20, b""
        while pos > 0 and len(rows) < n:
            pos = max(0, pos - chunk)
            fh.seek(pos)
            buf = fh.read(min(chunk, size - pos)) + tailb
            parts = buf.split(b"\n")
            tailb = parts[0]
            for ln in reversed(parts[1:]):
                if ln.strip():
                    rows.append(ln.decode("utf-8", "replace"))
                    if len(rows) >= n:
                        break
    rows.reverse()
    return rows


def load_deals() -> list[dict]:
    if not DEALS.exists() or DEALS.stat().st_size == 0:
        return []
    out = []
    for ln in tail(DEALS, 400):
        p = ln.strip().split("|")
        if len(p) < 8:
            continue
        try:
            out.append({"profit": float(p[7]), "t": p[1], "side": p[3]})
        except ValueError:
            continue
    return out


def load_state() -> dict:
    if STATE.exists():
        try:
            return json.loads(STATE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"mood": "calm", "loss_streak": 0, "applied_reco": ""}


def save_state(st: dict) -> None:
    STATE.write_text(json.dumps(st, indent=2), encoding="utf-8")


def load_cfg() -> dict:
    with open(CFG, "r", encoding="utf-8") as fh:
        return json.load(fh)


def save_cfg(cfg: dict) -> None:
    tmp = CFG.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cfg, indent=4), encoding="utf-8")
    tmp.replace(CFG)


def mood_of(deals: list[dict], st: dict) -> tuple[str, int, float]:
    if not deals:
        return "calm", 0, 0.0
    streak = 0
    for d in reversed(deals):
        if d["profit"] < 0:
            streak += 1
        else:
            break
    last = deals[-200:]
    eq = 0.0
    peak = 0.0
    mdd = 0.0
    wins = 0
    for d in last:
        eq += d["profit"]
        if eq > peak:
            peak = eq
        mdd = max(mdd, peak - eq)
        if d["profit"] > 0:
            wins += 1
    dd_pct = (mdd / peak) if peak > 0 else 0.0
    wr = wins / len(last)
    if streak >= 8 or dd_pct >= 0.15:
        mood = "paused"
    elif streak >= 5 or dd_pct >= 0.08:
        mood = "strict"
    elif streak >= 3 or dd_pct >= 0.04:
        mood = "cautious"
    elif streak == 0 and wr >= 0.45 and st.get("mood") != "calm":
        mood = "calm"          # confirmed recovery
    else:
        mood = st.get("mood", "calm")
    return mood, streak, round(dd_pct, 4)


def apply_reco(cfg: dict, st: dict) -> dict:
    """Auto-apply the latest PF>=2 NET-of-cost config from nobi_backtest."""
    if not RECO.exists():
        return st
    try:
        reco = json.loads(RECO.read_text(encoding="utf-8"))
    except Exception:
        return st
    gen = reco.get("generated", "")
    best = reco.get("best", {})
    stats = best.get("stats", {})
    if not gen or stats.get("pf", 0) < 2.0 or stats.get("n", 0) < 20 or reco.get("cost_usd", 0) < 20:
        return st
    if gen == st.get("applied_reco"):
        return st
    bc = reco.get("bridge", {})
    if bc.get("epoch_sec") and bc.get("min_abs") is not None:
        cfg["epoch_sec"] = int(bc["epoch_sec"])
        cfg["min_abs"] = float(bc["min_abs"])
        save_cfg(cfg)
        st["applied_reco"] = gen
        log(f"AUTO-APPLIED reco {gen} -> epoch={cfg['epoch_sec']}s min_abs={cfg['min_abs']} "
            f"(net PF={stats['pf']} n={stats['n']})")
    return st


def main() -> int:
    cfg = load_cfg()
    st = load_state()
    deals = load_deals()
    mood, streak, dd = mood_of(deals, st)

    base = st.get("min_abs_base", cfg.get("min_abs", 25.0))
    old_mood = st.get("mood", "calm")
    if mood == "calm" and old_mood == "calm" and streak == 0:
        target = base
    else:
        target = base * FACTOR[mood] if mood != "paused" else FACTOR["paused"]

    cfg["min_abs"] = round(target, 1)
    save_cfg(cfg)

    changed = mood != old_mood or abs(cfg["min_abs"] - st.get("last_min_abs", -1)) > 0.01
    st.update({
        "mood": mood, "loss_streak": streak, "drawdown_pct": dd,
        "wins": sum(1 for d in deals[-200:] if d["profit"] > 0),
        "trades": len(deals[-200:]),
        "min_abs": cfg["min_abs"], "min_abs_base": base,
        "last_min_abs": cfg["min_abs"], "updated": now(),
    })
    st = apply_reco(cfg, st)
    save_state(st)
    log(f"mood={mood} streak={streak} dd={dd:.1%} min_abs={cfg['min_abs']} "
        f"(base={base}) trades={len(deals[-200:])} changed={changed}")

    if changed and mood in ("strict", "paused"):
        CALL.write_text(
            f"{now()}  NOBI AI requests assistant review: mood={mood}, "
            f"loss_streak={streak}, dd={dd:.1%}, min_abs now {cfg['min_abs']}\n",
            encoding="utf-8")
        log(f"assistant_call.txt written ({mood})")
    return 0


if __name__ == "__main__":
    sys.exit(main())