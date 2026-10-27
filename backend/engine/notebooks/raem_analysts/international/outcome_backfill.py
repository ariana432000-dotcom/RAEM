# raem_analysts/international/outcome_backfill.py -- shared by AAPL, IBM, 0300_HK, 2222_SR
# The company-specific values come from each notebook's RAEM_PROFILE:
# @@TICKER@@ ticker, @@FILE@@ ticker as used in file names, @@LOG@@ trading-log name, @@SLOT:key@@ lines the profile supplies.
# >>> shared code
# ================================================================
# 📈 OUTCOME BACKFILL — RAEM-এর জন্য essential: episode-এ শুধু
# "কী decision নেওয়া হয়েছিল" না, "সেই decision-এর ফলাফল কী হলো" থাকা দরকার।
# ================================================================
# এই cell প্রতিবার pipeline শুরুতে চলবে এবং সব PENDING outcome_status
# episode খুঁজে, আজকের stock price আনিয়ে P&L হিসাব করে metadata
# আপডেট করবে (ChromaDB-তে real update, নতুন entry না)।
#
# Price আনার জন্য agent_utils-এর get_stock_data ব্যবহার করা হচ্ছে —
# Cell-এ (Market Analyst) যেভাবে কল হয় ঠিক একইভাবে।

from datetime import datetime, timedelta

def _fetch_latest_close(company: str, as_of_date: str):
    """get_stock_data দিয়ে as_of_date-এর কাছাকাছি সময়ের latest close price আনে।
    Cell 8 (Market Analyst)-এর CSV-parsing logic-এর সাথে consistent।"""
    try:
        from tradingagents.agents.utils.agent_utils import get_stock_data
    except ImportError:
        print("   ⚠️  get_stock_data import করা যায়নি — outcome backfill skip হচ্ছে।")
        return None

    end_dt = datetime.strptime(as_of_date, "%Y-%m-%d")
    start_dt = end_dt - timedelta(days=10)
    try:
        raw = get_stock_data.invoke({
            "symbol": company,
            "start_date": start_dt.strftime("%Y-%m-%d"),
            "end_date": as_of_date,
        })
    except Exception as e:
        print(f"   ⚠️  get_stock_data error: {e}")
        return None

    raw_csv = str(raw)
    csv_lines = [l for l in raw_csv.split("\n") if l.strip() and not l.startswith("#")]
    latest_close = None
    if len(csv_lines) > 1:
        for row in reversed(csv_lines[1:]):
            parts = row.split(",")
            if len(parts) >= 5 and parts[4].strip() and parts[4].strip() not in ("", "Close"):
                try:
                    latest_close = float(parts[4].strip())
                    break
                except ValueError:
                    pass
    return latest_close


def backfill_pending_outcomes(company: str, today_date: str, min_hours_held: float = 24.0) -> int:
    """
    PENDING episodes (যেগুলো session_id timestamp অনুযায়ী অন্তত min_hours_held
    ঘন্টা পুরোনো) খুঁজে, আজকের close price দিয়ে P&L হিসাব করে RESOLVED করে।

    ✅ CHANGED: আগে days_held (calendar-date difference) দিয়ে চেক হতো, তাই
    দিনে একাধিকবার (যেমন প্রতি ২ ঘণ্টায়) run করলে সেদিনের কোনো episode-ই
    সেদিন resolve হতো না -- min_days_held=1 মানে "আজকের episode আজ resolve
    হবে না", তার জন্য পরদিন পর্যন্ত অপেক্ষা করতে হতো। এখন session_id (যেটাতে
    ঘন্টা:মিনিট:সেকেন্ড আছে) থেকে hours_held হিসাব হয় -- তাই সকাল ১০টার
    episode বিকেল হলেই (২৪ ঘন্টা পার হলে, calendar date না বদলালেও) resolve
    হয়ে যাবে। session_id না থাকা পুরোনো episode-এর জন্য trade_date থেকে
    fallback হিসাব করা হয় (backward-compatible)।

    Returns: কতগুলো episode resolve হলো।
    """
    try:
        pending = episodic_memory.get(
            where={"$and": [{"company": company}, {"outcome_status": "PENDING"}]}
        )
    except Exception as e:
        print(f"   ⚠️  PENDING episodes query করতে সমস্যা: {e}")
        return 0

    if not pending["ids"]:
        print("   ℹ️  কোনো PENDING episode নেই — backfill করার কিছু নেই।")
        return 0

    today_close = _fetch_latest_close(company, today_date)
    if today_close is None:
        print("   ⚠️  আজকের close price আনা যায়নি — backfill skip হচ্ছে।")
        return 0

    resolved_count = 0
    skipped_too_recent = 0
    skipped_no_entry_price = 0
    skipped_bad_date = 0

    for ep_id, doc, meta in zip(pending["ids"], pending["documents"], pending["metadatas"]):
        ep_date = meta.get("trade_date", "")

        # ✅ NEW: hours_held from session_id timestamp (falls back to
        # midnight-of-trade_date if session_id is missing/malformed).
        session_id = meta.get("session_id", "")
        try:
            ep_dt = datetime.strptime(session_id, "%Y-%m-%d_%H-%M-%S")
        except ValueError:
            try:
                ep_dt = datetime.strptime(ep_date, "%Y-%m-%d")
            except ValueError:
                skipped_bad_date += 1
                continue

        try:
            now_dt = datetime.strptime(today_date, "%Y-%m-%d")
        except ValueError:
            skipped_bad_date += 1
            continue
        # today_date-এ শুধু date থাকে (time নেই), তাই day-end (23:59:59) ধরে
        # হিসাব করা হচ্ছে -- এতে "আজকের তারিখ"-এর মধ্যেই hours_held বেশি করে
        # দেখানোর ঝুঁকি নেই।
        hours_held = (now_dt.replace(hour=23, minute=59, second=59) - ep_dt).total_seconds() / 3600

        if hours_held < min_hours_held:
            skipped_too_recent += 1
            continue  # ✅ NEW: diagnostic counter (আগে সম্পূর্ণ silent ছিল)

        entry_price_str = meta.get("entry_price", "N/A")
        try:
            entry_price = float(entry_price_str)
        except (TypeError, ValueError):
            skipped_no_entry_price += 1  # ✅ NEW: diagnostic counter
            continue  # entry price না থাকলে P&L হিসাব করা সম্ভব না

        signal = meta.get("final_signal", "HOLD")
        if signal == "BUY":
            pnl_pct = ((today_close - entry_price) / entry_price) * 100
        elif signal == "SELL":
            pnl_pct = ((entry_price - today_close) / entry_price) * 100
        else:  # HOLD
            pnl_pct = 0.0

        outcome_label = "PROFITABLE" if pnl_pct > 0.5 else ("LOSS" if pnl_pct < -0.5 else "FLAT")

        new_meta = dict(meta)
        new_meta.update({
            "outcome_status": "RESOLVED",
            "exit_price": str(today_close),
            "pnl_pct": f"{pnl_pct:.2f}",
            "outcome_label": outcome_label,
            "hours_held": f"{hours_held:.1f}",
        })

        episodic_memory.update(ids=[ep_id], metadatas=[new_meta])
        print(f"   ✅ Resolved: {ep_date} ({hours_held:.1f}h old) | {signal} @ {entry_price:.2f} → {today_close:.2f} | P&L: {pnl_pct:+.2f}% ({outcome_label})")
        resolved_count += 1

    # ✅ NEW: এখন সবসময় বোঝা যাবে কেন resolved_count কম/শূন্য হলো
    if skipped_too_recent or skipped_no_entry_price or skipped_bad_date:
        print(
            f"   ℹ️  Skipped — too recent (< {min_hours_held}h old): {skipped_too_recent} | "
            f"no valid entry_price: {skipped_no_entry_price} | bad date: {skipped_bad_date}"
        )

    return resolved_count


print(f"{'='*60}")
print("📈 OUTCOME BACKFILL — পুরোনো episode-এ P&L আপডেট")
print(f"{'='*60}")
n_resolved = backfill_pending_outcomes(company="@@TICKER@@", today_date=TRADE_DATE, min_hours_held=48.0)
print(f"   মোট resolve হলো: {n_resolved} episode")
print(f"{'='*60}\n")
