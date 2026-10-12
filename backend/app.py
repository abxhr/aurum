"""Wealth Tracker v3 — Flask API.
Run: python backend/app.py → http://localhost:5000
"""
import os, csv, io, json, threading, time as _time
from datetime import datetime, date
from flask import Flask, jsonify, request, send_from_directory, redirect, make_response, Response, stream_with_context

from database import get_db, init_db, seed_if_empty, ASSET_CLASSES, base_ccy as CCY
import prices as P
import auth
import cards as C
import email_sync as ES
import transactions as T
import market as M
import ai_assistant as AI
import bank_statements as BS
import analytics as AN
import ibkr as IB
import etoro as EO
import budget as BG
import agent as AG
import payslips as PS
import feeds as FD

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
app = Flask(__name__, static_folder=None)
SAFE_CLASSES = {"cash", "savings", "bond"}


# ─── auth gate and sign-in routes live in authroutes.py ──────────────────────
import notify as NT
import authroutes
import security as SEC
app.register_blueprint(authroutes.bp)
app.before_request(authroutes.gate)
app.after_request(SEC.add_headers)
app.config.update(MAX_CONTENT_LENGTH=8*1024*1024, JSON_SORT_KEYS=False, PROPAGATE_EXCEPTIONS=False)

@app.errorhandler(Exception)
def _unhandled(e):
    """Never send a stack trace or an internal message to the browser; keep it in the log."""
    from werkzeug.exceptions import HTTPException
    if isinstance(e, HTTPException):
        return jsonify({"error":e.name}), e.code
    app.logger.exception("unhandled error on %s", request.path)
    return jsonify({"error":"Something went wrong."}), 500

@app.route("/")
def index(): return send_from_directory(os.path.join(BASE,"templates"),"index.html")

@app.route("/static/<path:p>")
def static_files(p): return send_from_directory(os.path.join(BASE,"static"),p)


# ─── helpers ──────────────────────────────────────────────────────────────────
def latest_fx():
    with get_db() as conn:
        rows = conn.execute(
            "SELECT currency,rate_to_base FROM fx_rates WHERE (currency,as_of) IN (SELECT currency,MAX(as_of) FROM fx_rates GROUP BY currency)"
        ).fetchall()
    fx = {r["currency"]:r["rate_to_base"] for r in rows}; fx.setdefault(CCY(),1.0); return fx

def native_value(h):
    if h["price_mode"] in ("ticker","nav") and h["units"]: return (h["last_price"] or 0)*h["units"]
    return h["manual_value"] or 0

def value_base(h, fx):
    v = native_value(h)*fx.get(h["currency"],1.0)
    return -v if h["is_liability"] else v

def serialize(h, fx):
    d = dict(h); d["native_value"]=round(native_value(h),2); d["value_base"]=round(value_base(h,fx),2)
    if h["cost_basis"]:
        d["pnl_native"]=round(native_value(h)-h["cost_basis"],2)
        d["pnl_pct"]=round((native_value(h)-h["cost_basis"])/h["cost_basis"]*100,1) if h["cost_basis"] else 0
    else:
        d["pnl_native"]=None; d["pnl_pct"]=None
    return d

def setting(k, default=None):
    with get_db() as conn:
        r = conn.execute("SELECT value FROM settings WHERE key=?", (k,)).fetchone()
    return r["value"] if r else default


# ─── holdings ─────────────────────────────────────────────────────────────────
@app.get("/api/holdings")
def holdings():
    cls = request.args.get("class")
    fx = latest_fx()
    q = "SELECT * FROM holdings WHERE archived=0"; args=[]
    if cls and cls!="all": q+=" AND asset_class=?"; args.append(cls)
    q+=" ORDER BY sort_order,id"
    with get_db() as conn: rows=conn.execute(q,args).fetchall()
    return jsonify([serialize(r,fx) for r in rows])

@app.post("/api/holdings")
def add_holding():
    b = request.json or {}; now=datetime.utcnow().isoformat()
    with get_db() as conn:
        mx=conn.execute("SELECT COALESCE(MAX(sort_order),0)+1 s FROM holdings").fetchone()["s"]
        cur=conn.execute(
            """INSERT INTO holdings
               (name,asset_class,platform,geography,currency,price_mode,symbol,units,
                last_price,price_ccy,manual_value,cost_basis,target_pct,is_liability,notes,sort_order,created_at,updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (b.get("name","New"),b.get("asset_class","cash"),b.get("platform",""),b.get("geography",""),
             b.get("currency",CCY()),b.get("price_mode","manual"),b.get("symbol",""),b.get("units",0) or 0,
             b.get("last_price",0) or 0,b.get("price_ccy",b.get("currency",CCY())),b.get("manual_value",0) or 0,
             b.get("cost_basis",0) or 0,b.get("target_pct",0) or 0,1 if b.get("is_liability") else 0,
             b.get("notes",""),mx,now,now))
        nid=cur.lastrowid
    if b.get("price_mode") in ("ticker","nav") and b.get("symbol"): _refresh_one(nid)
    return jsonify({"id":nid}),201

@app.put("/api/holdings/<int:hid>")
def update_holding(hid):
    b = request.json or {}
    cols=["name","asset_class","platform","geography","currency","price_mode","symbol",
          "units","last_price","price_ccy","manual_value","cost_basis","target_pct","is_liability","notes"]
    sets,vals=[],[]
    for c in cols:
        if c in b: sets.append(f"{c}=?"); vals.append(1 if c=="is_liability" and b[c] else (0 if c=="is_liability" else b[c]))
    if sets:
        sets.append("updated_at=?"); vals.append(datetime.utcnow().isoformat()); vals.append(hid)
        with get_db() as conn: conn.execute(f"UPDATE holdings SET {', '.join(sets)} WHERE id=?",vals)
    if b.get("price_mode") in ("ticker","nav") and b.get("symbol"): _refresh_one(hid)
    return jsonify({"ok":True})

@app.delete("/api/holdings/<int:hid>")
def delete_holding(hid):
    with get_db() as conn:
        h=conn.execute("SELECT platform,symbol FROM holdings WHERE id=?",(hid,)).fetchone()
        conn.execute("UPDATE holdings SET archived=1 WHERE id=?",(hid,))
    # a holding that came from IBKR would otherwise be re-created by the next sync
    if h and h["symbol"] and "ibkr" in (h["platform"] or "").lower(): IB.ignore(h["symbol"])
    if h and h["symbol"] and "etoro" in (h["platform"] or "").lower(): EO.ignore(h["symbol"])
    return jsonify({"ok":True})

@app.post("/api/holdings/<int:hid>/quick-update")
def quick_update(hid):
    """Quick manual balance update from the UI."""
    b = request.json or {}
    with get_db() as conn:
        conn.execute("UPDATE holdings SET manual_value=?,updated_at=? WHERE id=?",
                     (float(b.get("value",0)), datetime.utcnow().isoformat(), hid))
    return jsonify({"ok":True})


# ─── search + quotes ──────────────────────────────────────────────────────────
@app.get("/api/search")
def search():
    q=request.args.get("q","").strip(); kind=request.args.get("kind","all")
    if len(q)<2: return jsonify([])
    results=[]
    if kind in ("all","security"): results+=P.search_symbols(q)
    if kind in ("all","mutual_fund"): results+=P.search_mutual_funds(q)
    return jsonify(results)

@app.get("/api/quote")
def quote_lookup():
    sym=request.args.get("symbol",""); mode=request.args.get("mode","ticker")
    res=P.nav(sym) if mode=="nav" else P.quote(sym)
    return jsonify(res or {})


# ─── live price refresh ───────────────────────────────────────────────────────
def _refresh_one(hid):
    fx=latest_fx()
    with get_db() as conn: h=conn.execute("SELECT * FROM holdings WHERE id=?",(hid,)).fetchone()
    if not h or h["price_mode"] not in ("ticker","nav") or not h["symbol"]: return None
    res=P.nav(h["symbol"]) if h["price_mode"]=="nav" else P.quote(h["symbol"])
    if not res or not res.get("price"): return None
    price=res["price"]; pccy=res.get("currency",h["currency"])
    if pccy!=h["currency"]:
        pr=fx.get(pccy,1.0); hr=fx.get(h["currency"],1.0) or 1.0; price=price*pr/hr; pccy=h["currency"]
    with get_db() as conn:
        conn.execute("UPDATE holdings SET last_price=?,price_ccy=?,price_updated=? WHERE id=?",
                     (round(price,4),pccy,datetime.utcnow().isoformat(),hid))
    return {"id":hid,"price":round(price,4)}

@app.post("/api/refresh")
def refresh_all():
    threading.Thread(target=_run_mail_jobs, daemon=True).start()
    ibkr_res=None
    if IB.configured():
        try: ibkr_res=_ibkr_sync()
        except Exception as e: ibkr_res={"ok":False,"error":str(e)}
    if EO.configured():
        try: EO.sync(take_snapshot)
        except Exception as e: print("eToro sync failed:", e)
    with get_db() as conn:
        curs=[r["currency"] for r in conn.execute("SELECT DISTINCT currency FROM holdings WHERE archived=0").fetchall()]
    today=date.today().isoformat(); fx_updated={}
    for c in curs:
        if c==CCY(): continue
        r=P.fx_to_base(c)
        if r:
            fx_updated[c]=r
            with get_db() as conn: conn.execute("INSERT OR REPLACE INTO fx_rates VALUES (?,?,?)",(c,r,today))
    with get_db() as conn:
        ids=[r["id"] for r in conn.execute("SELECT id FROM holdings WHERE archived=0 AND price_mode IN ('ticker','nav') AND symbol!=''").fetchall()]
    updated=[u for hid in ids if (u:=_refresh_one(hid))]
    return jsonify({"fx":fx_updated,"prices":updated,"live":bool(updated or fx_updated),"ibkr":ibkr_res})



# ─── Interactive Brokers (read-only Flex sync) ────────────────────────────────
def _ensure_fx(ccy):
    """Make sure holdings in this currency can be valued in the app currency."""
    if ccy==CCY() or ccy in latest_fx(): return
    r=P.fx_to_base(ccy)
    if r:
        with get_db() as conn: conn.execute("INSERT OR REPLACE INTO fx_rates VALUES (?,?,?)",(ccy,r,date.today().isoformat()))

def _ibkr_sync():
    return IB.sync(ensure_fx=_ensure_fx, take_snapshot=take_snapshot)

@app.get("/api/ibkr/status")
def ibkr_status(): return jsonify(IB.status())

@app.put("/api/ibkr/config")
def ibkr_config():
    b=request.json or {}
    IB.save_config(b.get("token",""), b.get("query_id"), b.get("interval_hours"))
    return jsonify(IB.status())

@app.delete("/api/ibkr/config")
def ibkr_disconnect():
    IB.disconnect(); return jsonify(IB.status())

@app.delete("/api/ibkr/ignored")
def ibkr_clear_ignored():
    IB.clear_ignored(); return jsonify(IB.status())

@app.post("/api/ibkr/sync")
def ibkr_sync_now():
    try: return jsonify(_ibkr_sync())
    except Exception as e: return jsonify({"ok":False,"error":str(e)}),502


# ─── payslips + emailed-balance feeds ─────────────────────────────────────────
def _mailbox_or_error():
    try:
        box = ES.open_mailbox()
    except ES.GmailAuthError as e:
        return None, (jsonify({"ok": False, "error": str(e)}), 502)
    except Exception as e:
        return None, (jsonify({"ok": False, "error": f"Could not reach Gmail: {e}"}), 502)
    if not box:
        return None, (jsonify({"ok": False, "error": "Connect Gmail first (Settings > Gmail Email Sync)."}), 400)
    return box, None

@app.get("/api/income-basis")
def income_basis(): return jsonify(AN.income_basis())

@app.get("/api/payslips")
def payslips_summary(): return jsonify(PS.summary())

@app.get("/api/payslips/<int:pid>")
def payslip_detail(pid):
    d = PS.detail(pid)
    return jsonify(d) if d else (jsonify({"error": "not found"}), 404)

@app.put("/api/payslips/config")
def payslip_config():
    pw = (request.json or {}).get("password")
    if pw: PS._set(PS.PASSWORD_KEY, pw)
    return jsonify({"ok": True})

@app.post("/api/payslips/scan")
def payslips_scan():
    box, err = _mailbox_or_error()
    if err: return err
    return jsonify(PS.scan(box))

@app.get("/api/feeds")
def feeds_list(): return jsonify(FD.list_feeds())

@app.post("/api/feeds")
def feeds_add():
    b = request.json or {}
    if not b.get("holding_id") or not b.get("sender"): return jsonify({"ok": False, "error": "Pick the account and enter the sender address"}), 400
    return jsonify({"ok": True, "id": FD.save_feed(b)})

@app.put("/api/feeds/<int:fid>")
def feeds_update(fid):
    FD.save_feed(request.json or {}, fid); return jsonify({"ok": True})

@app.delete("/api/feeds/<int:fid>")
def feeds_delete(fid):
    FD.delete_feed(fid); return jsonify({"ok": True})

@app.post("/api/feeds/<int:fid>/<action>")
def feeds_run(fid, action):
    box, err = _mailbox_or_error()
    if err: return err
    if action == "test":
        feed = next((f for f in FD.list_feeds() if f["id"] == fid), None)
        if not feed: return jsonify({"ok": False, "error": "Unknown feed"}), 404
        full = FD._get(fid)
        return jsonify(FD.test_feed(box, full))
    return jsonify(FD.sync_feed(box, fid, take_snapshot))

@app.get("/api/feeds/discover")
def feeds_discover():
    q = (request.args.get("q") or "").strip()
    if not q: return jsonify({"ok": False, "error": "Type a bank name or sender to search for"}), 400
    box, err = _mailbox_or_error()
    if err: return err
    return jsonify({"ok": True, "hits": FD.discover(box, q)})

_jobs_lock = threading.Lock()

def _run_mail_jobs():
    """Everything that reads email: payslips, bank balance feeds, bank statements, card statements."""
    if not _jobs_lock.acquire(blocking=False): return
    try:
        try: box = ES.open_mailbox()
        except Exception as e:
            print("mail jobs: mailbox unavailable:", e); return
        if not box: return
        before = NT.snapshot()
        try:
            if PS._setting(PS.PASSWORD_KEY): PS.scan(box)
        except Exception as e: print("mail jobs: payslips failed:", e)
        try: FD.sync_all(box, take_snapshot)
        except Exception as e: print("mail jobs: feeds failed:", e)
        try:
            cfg = BS.get_config()
            if cfg.get("current_holding_id") or cfg.get("savings_holding_id"): BS.sync_now(take_snapshot_fn=take_snapshot)
        except Exception as e: print("mail jobs: bank statement failed:", e)
        try: ES.sync_emails(90)
        except Exception as e: print("mail jobs: card statements failed:", e)
        try:
            import bank_tx as BT
            BT.sync(3)
        except Exception as e: print("mail jobs: bank transactions failed:", e)
    finally:
        _jobs_lock.release()


# ─── eToro (read-only API keys) ───────────────────────────────────────────────
@app.get("/api/etoro/status")
def etoro_status(): return jsonify(EO.status())

@app.put("/api/etoro/config")
def etoro_config():
    b=request.json or {}
    EO.save_config(b.get("api_key",""), b.get("user_key",""))
    if "include_cfd" in b: EO.set_include_cfd(bool(b["include_cfd"]))
    return jsonify(EO.status())

@app.delete("/api/etoro/config")
def etoro_disconnect():
    EO.disconnect(); return jsonify(EO.status())

@app.delete("/api/etoro/ignored")
def etoro_clear_ignored():
    EO.clear_ignored(); return jsonify(EO.status())

@app.post("/api/etoro/sync")
def etoro_sync_now():
    try: return jsonify(EO.sync(take_snapshot))
    except Exception as e: return jsonify({"ok":False,"error":str(e)}),502


# ─── AI agent ─────────────────────────────────────────────────────────────────
@app.get("/api/agent/state")
def agent_state():
    return jsonify({"settings": AG.get_settings(), "profile": AG.get_profile(), "memories": AG.list_memories(), "conversations": AG.list_conversations(),
                    "key": AG.key_status(), "models": AG.MODELS, "openai_models": AG.OPENAI_MODELS,
                    "sdk": {"anthropic": AG.anthropic is not None, "openai": AG.openai_sdk is not None}})

@app.put("/api/agent/settings")
def agent_settings():
    return jsonify({"settings": AG.save_settings(request.json or {}), "key": AG.key_status()})

@app.put("/api/agent/profile")
def agent_profile():
    return jsonify(AG.save_profile(request.json or {}))

@app.post("/api/agent/memories")
def agent_memory_add():
    try: mid=AG.add_memory((request.json or {}).get("text",""))
    except ValueError as e: return jsonify({"ok":False,"error":str(e)}),400
    return jsonify({"ok":True,"id":mid,"memories":AG.list_memories()})

@app.delete("/api/agent/memories")
def agent_memory_clear():
    AG.delete_memory(None); return jsonify({"ok":True})

@app.delete("/api/agent/memories/<int:mid>")
def agent_memory_delete(mid):
    AG.delete_memory(mid); return jsonify({"ok":True})

@app.get("/api/agent/conversations")
def agent_conversations(): return jsonify(AG.list_conversations())

@app.get("/api/agent/conversations/<int:cid>")
def agent_conversation(cid):
    c=AG.get_conversation(cid)
    return jsonify(c) if c else (jsonify({"error":"not found"}),404)

@app.delete("/api/agent/conversations/<int:cid>")
def agent_conversation_delete(cid):
    AG.delete_conversation(cid); return jsonify({"ok":True})

@app.delete("/api/agent/conversations")
def agent_conversations_clear():
    AG.delete_conversation(None); return jsonify({"ok":True})

@app.post("/api/agent/actions/<int:aid>/<verb>")
def agent_action(aid, verb):
    if verb=="apply":
        ok,msg=AG.apply_action(aid)
        return jsonify({"ok":ok,"message":msg})
    if verb=="dismiss":
        AG.dismiss_action(aid); return jsonify({"ok":True})
    return jsonify({"ok":False}),404

@app.post("/api/agent/test")
def agent_test():
    try: return jsonify(AG.test_connection())
    except Exception as e: return jsonify({"ok":False,"error":str(e)[:300]}),502

@app.post("/api/agent/chat")
def agent_chat():
    b=request.json or {}
    def gen():
        for ev in AG.run_turn(b.get("conv_id"), b.get("text","")):
            yield "data: "+json.dumps(ev, ensure_ascii=False)+"\n\n"
    return Response(stream_with_context(gen()), mimetype="text/event-stream",
                    headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no"})

# ─── FX ───────────────────────────────────────────────────────────────────────
@app.get("/api/fx")
def get_fx(): return jsonify(latest_fx())

@app.put("/api/fx")
def put_fx():
    b=request.json or {}
    with get_db() as conn:
        conn.execute("INSERT OR REPLACE INTO fx_rates VALUES (?,?,?)",(b["currency"],float(b["rate"]),date.today().isoformat()))
    return jsonify({"ok":True})


# ─── snapshots + history ──────────────────────────────────────────────────────
def take_snapshot():
    fx=latest_fx(); today=date.today().isoformat()
    with get_db() as conn:
        rows=conn.execute("SELECT * FROM holdings WHERE archived=0").fetchall()
        conn.execute("DELETE FROM snapshots WHERE snap_date=?",(today,))
        for r in rows:
            conn.execute("INSERT INTO snapshots(holding_id,snap_date,value_base) VALUES (?,?,?)",
                         (r["id"],today,round(value_base(r,fx),2)))
    return today

@app.post("/api/snapshot")
def snapshot():
    return jsonify({"ok":True,"date":take_snapshot()})

@app.get("/api/history")
def history():
    with get_db() as conn:
        rows=conn.execute("SELECT snap_date,ROUND(SUM(value_base),2) total FROM snapshots GROUP BY snap_date ORDER BY snap_date").fetchall()
    return jsonify([dict(r) for r in rows])

@app.get("/api/holdings/<int:hid>/history")
def holding_history(hid):
    with get_db() as conn:
        rows=conn.execute(
            "SELECT snap_date,value_base FROM snapshots WHERE holding_id=? ORDER BY snap_date",
            (hid,)).fetchall()
    return jsonify([dict(r) for r in rows])


# ─── bank account statement sync (non-card) ──
@app.get("/api/bank-statement/config")
def bank_statement_config():
    return jsonify(BS.get_config())

@app.put("/api/bank-statement/config")
def bank_statement_save_config():
    BS.save_config(request.json or {})
    return jsonify({"ok":True})

@app.post("/api/bank-statement/sync")
def bank_statement_sync():
    return jsonify(BS.sync_now(take_snapshot_fn=take_snapshot))


# ─── summary ──────────────────────────────────────────────────────────────────
@app.get("/api/summary")
def summary():
    # one snapshot per day is saved automatically so the net-worth history builds itself
    with get_db() as conn:
        has_today=conn.execute("SELECT 1 FROM snapshots WHERE snap_date=? LIMIT 1",(date.today().isoformat(),)).fetchone()
        has_holdings=conn.execute("SELECT 1 FROM holdings WHERE archived=0 LIMIT 1").fetchone()
    if has_holdings and not has_today:
        take_snapshot()
    fx=latest_fx()
    with get_db() as conn: rows=conn.execute("SELECT * FROM holdings WHERE archived=0").fetchall()
    by_class,by_geo,by_ccy,target_by_class={},{},{},{}
    safe=growth=total=0.0; class_counts={}
    for r in rows:
        v=value_base(r,fx); total+=v
        by_class[r["asset_class"]]=by_class.get(r["asset_class"],0)+v
        by_geo[r["geography"]]=by_geo.get(r["geography"],0)+v
        by_ccy[r["currency"]]=by_ccy.get(r["currency"],0)+v
        class_counts[r["asset_class"]]=class_counts.get(r["asset_class"],0)+1
        if r["target_pct"]: target_by_class[r["asset_class"]]=target_by_class.get(r["asset_class"],0)+r["target_pct"]
        (safe if r["asset_class"] in SAFE_CLASSES else growth).__class__  # dummy
        if r["asset_class"] in SAFE_CLASSES: safe+=v
        else: growth+=v
    def m(d): return [{"label":k,"value":round(v,2),"pct":round(v/total*100,1) if total else 0} for k,v in sorted(d.items(),key=lambda x:-x[1])]
    allocation_drift=None
    if target_by_class and abs(sum(target_by_class.values())-100)<=5:      # targets that do not add up to ~100% are leftovers, not a plan
        allocation_drift=[]
        for cls in set(by_class)|set(target_by_class):
            actual_pct=round(by_class.get(cls,0)/total*100,1) if total else 0
            tgt_pct=round(target_by_class.get(cls,0),1)
            allocation_drift.append({"label":cls,"actual_pct":actual_pct,"target_pct":tgt_pct,"drift":round(actual_pct-tgt_pct,1)})
        allocation_drift.sort(key=lambda x:-abs(x["drift"]))
    dues=C.get_upcoming_dues(30)
    return jsonify({
        "total_base":round(total,2),"safe_base":round(safe,2),"growth_base":round(growth,2),
        "safe_pct":round(safe/total*100,1) if total else 0,"growth_pct":round(growth/total*100,1) if total else 0,
        "by_class":m(by_class),"by_geography":m(by_geo),"by_currency":m(by_ccy),
        "allocation_drift":allocation_drift,
        "class_counts":class_counts,"fx":fx,"base_currency":CCY(),
        "upcoming_dues":len(dues),"overdue":sum(1 for d in dues if d["urgency"]=="overdue"),
    })


# ─── credit cards ─────────────────────────────────────────────────────────────
@app.get("/api/cards")
def list_cards():
    cards=C.get_all_cards()
    for card in cards:
        stmts=C.get_statements(card["id"],12)
        card["latest_statement"]=stmts[0] if stmts else None
        card["unpaid_count"]=sum(1 for s in stmts[:3] if s["status"] in ("unpaid","partial"))
        card["statement_pattern"]=C.statement_pattern(card,stmts)
    return jsonify(cards)

@app.post("/api/cards")
def add_card():
    nid=C.add_card(request.json or {}); return jsonify({"id":nid}),201

@app.put("/api/cards/<int:cid>")
def update_card(cid):
    C.update_card(cid, request.json or {}); return jsonify({"ok":True})

@app.delete("/api/cards/<int:cid>")
def delete_card(cid):
    C.delete_card(cid); return jsonify({"ok":True})

@app.get("/api/cards/<int:cid>/statements")
def card_statements(cid):
    return jsonify(C.get_statements(cid))

@app.post("/api/cards/<int:cid>/statements")
def add_card_statement(cid):
    nid=C.add_statement(cid, request.json or {}); return jsonify({"id":nid}),201

@app.get("/api/statements/<int:sid>")
def statement_detail(sid):
    with get_db() as conn:
        row=conn.execute(
            """SELECT s.*, c.name card_name, c.bank, c.currency, c.card_color
               FROM card_statements s JOIN credit_cards c ON s.card_id=c.id WHERE s.id=?""",
            (sid,)).fetchone()
    if not row: return jsonify({"error":"not found"}),404
    stmt=dict(row)
    stmt["transactions"]=T.get_transactions(sid)
    return jsonify(stmt)

@app.post("/api/statements/<int:sid>/pay")
def pay_statement(sid):
    b=request.json or {}
    C.mark_paid(sid, b.get("amount"), b.get("paid_date"))
    return jsonify({"ok":True})

@app.get("/api/dues")
def upcoming_dues():
    return jsonify(C.get_upcoming_dues(int(request.args.get("days","30"))))

@app.get("/api/cards/banks")
def list_banks():
    return jsonify(C.known_banks())

@app.get("/api/cards/stats")
def cards_stats():
    return jsonify(C.get_stats())

@app.get("/api/spending/categories")
def spending_categories(): return jsonify(T.get_categories())

@app.get("/api/spending/categories/custom")
def spending_categories_custom(): return jsonify(T.custom_categories())

@app.post("/api/spending/categories")
def spending_category_add():
    b=request.json or {}
    try: return jsonify({"ok":True, **T.add_category(b.get("name",""), b.get("keywords",""))})
    except ValueError as e: return jsonify({"ok":False,"error":str(e)}),400

@app.delete("/api/spending/categories/<path:name>")
def spending_category_delete(name):
    try: return jsonify({"ok":True,"moved_to_other":T.delete_category(name)})
    except ValueError as e: return jsonify({"ok":False,"error":str(e)}),400

@app.get("/api/spending/by-category")
def spending_by_category():
    days=int(request.args.get("days","180"))
    return jsonify(T.spend_by_category(days))

@app.get("/api/spending/monthly")
def spending_monthly():
    months=int(request.args.get("months","6"))
    return jsonify(T.monthly_spend(months))

@app.get("/api/spending/transactions")
def spending_transactions():
    days=int(request.args.get("days","180"))
    return jsonify(T.get_spend_transactions(days))

@app.put("/api/transactions/<int:tid>")
def update_transaction(tid):
    b=request.json or {}
    count=0
    if "category" in b: count=T.update_category(tid, b["category"], b.get("subcategory",""))
    return jsonify({"ok":True,"updated":count})

@app.get("/api/spending/trips")
def spending_trips():
    import categorizer as CZ
    CZ.ensure_columns()
    with get_db() as conn:
        rows = conn.execute("""SELECT trip label, MIN(txn_date) start, MAX(txn_date) end, COUNT(*) charges, ROUND(SUM(amount)) total
                               FROM statement_transactions WHERE trip!='' AND amount>0 GROUP BY trip ORDER BY MIN(txn_date) DESC""").fetchall()
    return jsonify([dict(r) for r in rows])

@app.put("/api/transactions/<int:tid>/trip")
def update_transaction_trip(tid):
    """trip: a trip label to pin it to, '-' for no trip, or '' to go back to automatic."""
    import categorizer as CZ
    CZ.ensure_columns()
    t = (request.json or {}).get("trip", "")
    with get_db() as conn:
        conn.execute("UPDATE statement_transactions SET trip_manual=? WHERE id=?", (None if t == "" else str(t)[:80], tid))
    return jsonify({"ok": True, **{k: v for k, v in CZ.recategorize().items() if k != "trips"}})

@app.post("/api/spending/recategorize")
def spending_recategorize():
    import categorizer as CZ
    return jsonify({"ok":True,**CZ.recategorize()})

@app.get("/api/spending/ai")
def spending_ai_state():
    import categorizer as CZ
    return jsonify({"enabled": CZ.ai_enabled(), "waiting": len(CZ.unknown_merchants(500)), "model": CZ.CHEAP})

@app.put("/api/spending/ai")
def spending_ai_set():
    import categorizer as CZ
    CZ.set_ai_enabled(bool((request.get_json(force=True) or {}).get("enabled")))
    return jsonify({"enabled": CZ.ai_enabled()})

@app.post("/api/spending/ai-classify")
def spending_ai_classify():
    import categorizer as CZ
    return jsonify(CZ.ai_classify())

@app.get("/api/spending/taxonomy")
def spending_taxonomy():
    import categorizer as CZ
    custom=[c["name"] for c in T.custom_categories()]
    return jsonify({"taxonomy":{**{c:s for c,s in CZ.TAXONOMY.items()},**{c:[""] for c in custom if c not in CZ.TAXONOMY}}})


# ─── market ───────────────────────────────────────────────────────────────────
@app.get("/api/market/search")
def market_search():
    q=request.args.get("q","")
    if len(q)<1: return jsonify([])
    return jsonify(P.search_symbols(q))

@app.get("/api/market/quote/<symbol>")
def market_quote(symbol):
    q=P.quote(symbol)
    if not q: return jsonify({"error":"not found"}),404
    return jsonify(q)

@app.get("/api/market/history/<symbol>")
def market_history(symbol):
    rng=request.args.get("range","3mo")
    h=P.history(symbol, rng)
    if not h: return jsonify({"error":"not found"}),404
    return jsonify(h)

@app.get("/api/watchlist")
def get_watchlist():
    watch=M.get_watchlist()
    for w in watch:
        q=P.quote(w["symbol"])
        w["price"]=q["price"] if q else None
        w["currency"]=q["currency"] if q else ""
    return jsonify(watch)

@app.post("/api/watchlist")
def add_watchlist():
    b=request.json or {}
    if not b.get("symbol"): return jsonify({"error":"symbol required"}),400
    M.add_watch(b["symbol"], b.get("name",""))
    return jsonify({"ok":True}),201

@app.delete("/api/watchlist/<symbol>")
def remove_watchlist(symbol):
    M.remove_watch(symbol); return jsonify({"ok":True})


# ─── AI assistant ───────────────────────────────────────────────────────────────
@app.get("/api/assistant/status")
def assistant_status():
    return jsonify({"available":AI.available()})

@app.get("/api/assistant/history")
def assistant_history():
    return jsonify(AI.get_history(100))

@app.post("/api/assistant/chat")
def assistant_chat():
    b=request.json or {}
    msg=(b.get("message") or "").strip()
    if not msg: return jsonify({"ok":False,"error":"empty message"}),400
    nw=summary().get_json()
    bg=get_budget().get_json()
    result=AI.chat(msg, net_worth=nw, budget=bg)
    return jsonify(result), (200 if result.get("ok") else 502)

@app.post("/api/assistant/clear")
def assistant_clear():
    AI.clear_history(); return jsonify({"ok":True})

@app.get("/api/insights")
def get_insights():
    bg=get_budget().get_json()
    return jsonify(AI.generate_insights(budget=bg))

@app.get("/api/basics")
def basics_api():
    return _safe(lambda: {**PL.basics(), "goals": PL.money_plan()["goals"]})

@app.post("/api/insights/<key>/dismiss")
def dismiss_insight(key):
    AI.dismiss_insight(key); return jsonify({"ok":True})


# ─── AI Analysis (LLM-generated, cached per scope) ─────────────────────────────
AI_INSIGHTS_TTL_HOURS = 24
AI_INSIGHTS_SCOPES = ("dashboard", "portfolio", "cards", "budget", "market")

def _ai_context(scope):
    if scope == "dashboard":
        s=summary().get_json(); budget=get_budget().get_json()
        readiness=AN.financial_readiness(); wc=AN.what_changed()
        anomalies=AN.category_anomalies(); dues=C.get_upcoming_dues(14)
        stats=C.get_stats()
        # give the LLM the raw underlying numbers, not the derived 0-100 "readiness"
        # sub-scores (higher score = healthier, which is the inverse of raw utilization%
        # and previously got misread as "100/100 utilization" meaning fully maxed out)
        lines=[f"Net worth: {CCY()} {s['total_base']:,.0f} (safe {s['safe_pct']}%, growth {s['growth_pct']}%)",
               f"Overall financial readiness score: {readiness['score']}/100 ({readiness['verdict']}) — higher is healthier",
               f"Card balances outstanding: {CCY()} {stats['total_outstanding']:,.0f}"]
        for a in (s.get("allocation_drift") or []):
            lines.append(f"Allocation — {a['label']}: actual {a['actual_pct']}% vs target {a['target_pct']}% (drift {a['drift']:+.1f}pp)")
        B=PL.basics()
        lines.append(f"Monthly picture: typical take-home {CCY()} {B['net_typical']:,}, average incl. annual bonus {CCY()} {B['net_avg']:,}; real spending {CCY()} {B['spend']:,} "
                     f"(cards {B['card_avg']:,} + paid outside cards {B['off_card']:,}); invested/saved {CCY()} {B['saved']:,}. "
                     f"Left after investing: {CCY()} {B['flow_typical']:+,} in a typical month, {CCY()} {B['flow_avg']:+,} on average with the bonus. "
                     f"Cash runway {B['runway_months']} months of real spending. Savings rate {B['savings_rate_gross_pct']}% of gross pay. "
                     f"(The typed-in budget claims a surplus of {CCY()} {budget['surplus']:,.0f}; that is a plan, not a measurement. Do not praise it.)")
        if wc: lines.append(f"Biggest month-over-month mover: {wc['summary']}")
        for a in anomalies[:3]:
            lines.append(f"Category swing: {a['category']} {a['direction']} {abs(a['pct_change'])}%, {CCY()} {a['value']:,.0f} in cycle {a['month']} vs {CCY()} {a['prev_value']:,.0f} in {a['prev_month']}")
        if dues: lines.append(f"{len(dues)} upcoming/overdue card payment(s) in next 14 days")
        return "\n".join(lines)
    if scope == "portfolio":
        with get_db() as conn: rows=conn.execute("SELECT * FROM holdings WHERE archived=0").fetchall()
        fx=latest_fx(); lines=["Holdings:"]
        for r in rows:
            d=dict(r); v=value_base(d,fx); pnl=""
            if d["cost_basis"]:
                nv=native_value(d)
                pnl_pct=round((nv-d["cost_basis"])/d["cost_basis"]*100,1)
                pnl=f", PnL {pnl_pct:+.1f}%"
            cfd = " [LEVERAGED CFD: P&L is on margin, not a normal return]" if "cfd" in (d["name"]+d["symbol"]).lower() else ""
            lines.append(f"  - {d['name']} ({d['asset_class']}, {d['geography']}, {d['currency']}): {CCY()} {v:,.0f}{pnl}{cfd}")
        return "\n".join(lines)
    if scope == "cards":
        stats=C.get_stats(); dues=C.get_upcoming_dues(30)
        cy=AN.spend_cycles(); done=[m for m in cy["months"] if m["complete"]][-3:]
        lines=[f"Cards: {stats['card_count']}, outstanding {CCY()} {stats['total_outstanding']:,.0f}",
               "Spending is measured per statement cycle (each card closes on a different day). Only complete cycles are listed; never describe a 'last 7 days' trend."]
        for m in done:
            lines.append(f"  - Cycle {m['month']}: {CCY()} {m['total']:,.0f} total ({', '.join(f'{k} {v:,.0f}' for k, v in sorted(m['by_card'].items()))})")
        if done:
            for cat, v in sorted(done[-1]["cats"].items(), key=lambda kv: -kv[1])[:8]:
                lines.append(f"  - Latest cycle {done[-1]['month']}, {cat}: {CCY()} {v:,.0f}")
        if cy["months"] and not cy["months"][-1]["complete"]:
            lines.append(f"  - Cycle {cy['months'][-1]['month']} is incomplete (missing: {', '.join(cy['months'][-1]['missing'])})")
        for d in dues[:6]: lines.append(f"  - Due: {d['card_name']} {CCY()} {d['total_due']:,.2f} on {d['due_date']} ({d['urgency']})")
        return "\n".join(lines)
    if scope == "budget":
        budget=get_budget().get_json(); act=BG.actuals(); inc=AN.income_basis()
        lines=[f"Planned budget (per month): income {CCY()} {budget['income']:,.0f}, spending {CCY()} {budget['expenses']:,.0f}, "
               f"savings {CCY()} {budget['savings']:,.0f}, investing {CCY()} {budget['invest']:,.0f}, planned left over {CCY()} {budget['surplus']:,.0f}"]
        for item in budget["items"]:
            extra=f", linked to {', '.join(item['categories'])}" if item["categories"] else (", paid outside the cards" if item.get("off_card") else "")
            real=act["items"].get(item["id"])
            lines.append(f"  - {item['label']} ({item['type']}): {CCY()} {item['monthly']:,.0f}/month{extra}" + (f"; actual {CCY()} {real:,.0f}/month" if real is not None else "") + ("; irregular, judged over 12 months" if item.get("lump") else ""))
        if inc["source"]=="payslip":
            lines.append(f"Payslips: typical month nets {CCY()} {inc['typical_net']:,.0f}; average incl. annual bonus spread over 12 months {CCY()} {inc['avg_net']:,.0f}; "
                         f"gross {inc['gross']:,.0f}. Payroll deductions (already out of net pay, not extra spending): about {CCY()} {act['payroll']['monthly']:,.0f}/month.")
        if act["cycles"]:
            lines.append(f"Actual card spending, average of complete statement cycles {', '.join(act['cycles'])}: {CCY()} {act['card_total']:,.0f}/month")
            for cat,v in sorted(act["by_category"].items(), key=lambda kv:-kv[1])[:8]: lines.append(f"  - {cat}: {CCY()} {v:,.0f}/month")
            if act["unbudgeted"]: lines.append("Categories with no budget line: "+", ".join(f"{u['category']} {u['monthly']:,.0f}" for u in act['unbudgeted'][:6]))
            if act["realistic_surplus"] is not None: lines.append(f"Realistic left over at real spending: {CCY()} {act['realistic_surplus']:,.0f}/month")
            else: lines.append("Some spending lines are not linked to card categories yet, so planned vs actual cannot be compared reliably.")
        lines.append("Advice rules: compare plan with actual; do not praise a surplus that real spending contradicts; payroll deductions are not extra expenses.")
        return "\n".join(lines)
    if scope == "market":
        watch=M.get_watchlist()
        if not watch: return ""
        lines=["Watchlist:"]
        for w in watch:
            q=P.quote(w["symbol"])
            if q: lines.append(f"  - {w['symbol']} ({w.get('name','')}): {q.get('price')} {q.get('currency','')}")
        return "\n".join(lines)
    return ""

def _ai_cols():
    with get_db() as conn:
        if "ctx_hash" not in {r["name"] for r in conn.execute("PRAGMA table_info(ai_insights_cache)").fetchall()}:
            conn.execute("ALTER TABLE ai_insights_cache ADD COLUMN ctx_hash TEXT DEFAULT ''")

def _ai_cache_get(scope):
    _ai_cols()
    with get_db() as conn:
        row=conn.execute("SELECT content,generated_at,ctx_hash FROM ai_insights_cache WHERE scope=?",(scope,)).fetchone()
    return dict(row) if row else None

def _ai_cache_set(scope, insights, ctx_hash=""):
    # explicit "Z" suffix: without it the browser's `new Date(iso)` reads a bare UTC timestamp as local time (otherwise the time is off by the viewer's UTC offset)
    _ai_cols()
    with get_db() as conn:
        conn.execute("INSERT OR REPLACE INTO ai_insights_cache(scope,content,generated_at,ctx_hash) VALUES (?,?,?,?)",
                     (scope, json.dumps(insights), datetime.utcnow().isoformat()+"Z", ctx_hash))

import hashlib
AI_MIN_REGEN_HOURS = 6      # even when the data changed, never rewrite an analysis more often than this

@app.get("/api/ai-insights/<scope>")
def get_ai_insights(scope):
    """The analysis is only rewritten when the numbers it was written from have changed (and not more than every 6 hours).
    Opening a page never costs an AI call when nothing is new, however long since the last one."""
    import aiusage
    if scope not in AI_INSIGHTS_SCOPES: return jsonify({"error":"invalid scope"}),400
    cached=_ai_cache_get(scope)
    ctx=_ai_context(scope); h=hashlib.sha1(ctx.encode()).hexdigest()[:16]
    def reply(c,stale,**kw):
        return jsonify({"insights":json.loads(c["content"]) if c else [],"generated_at":c["generated_at"] if c else None,"stale":stale,"available":AI.available(),**kw})
    if cached:
        age_h=(datetime.utcnow()-datetime.fromisoformat(cached["generated_at"].rstrip("Z"))).total_seconds()/3600
        if cached.get("ctx_hash")==h or age_h<AI_MIN_REGEN_HOURS:
            aiusage.hit("insights:"+scope)
            return reply(cached,False)
    if not AI.available(): return reply(cached,True,available=False)
    result=AI.generate_ai_insights(scope, ctx)
    if not result.get("ok"): return reply(cached,True,error=result.get("error"))
    _ai_cache_set(scope, result["insights"], h)
    return jsonify({"insights":result["insights"],"generated_at":datetime.utcnow().isoformat()+"Z","stale":False,"available":True})

@app.post("/api/ai-insights/<scope>/refresh")
def refresh_ai_insights(scope):
    if scope not in AI_INSIGHTS_SCOPES: return jsonify({"error":"invalid scope"}),400
    if not AI.available(): return jsonify({"error":"AI analysis requires an OpenAI API key."}),400
    result=AI.generate_ai_insights(scope, _ai_context(scope))
    if not result.get("ok"): return jsonify({"error":result.get("error","failed")}),502
    _ai_cache_set(scope, result["insights"], hashlib.sha1(_ai_context(scope).encode()).hexdigest()[:16])
    return jsonify({"insights":result["insights"],"generated_at":datetime.utcnow().isoformat()+"Z","stale":False,"available":True})

@app.get("/api/readiness")
def readiness():
    return jsonify(AN.financial_readiness())

@app.get("/api/patterns")
def patterns():
    return jsonify(AN.find_patterns())

@app.get("/api/spend-cycles")
def spend_cycles_api(): return jsonify(AN.spend_cycles())

@app.get("/api/spend-load")
def spend_load():
    return jsonify(AN.spend_load())

@app.get("/api/what-changed")
def what_changed():
    return jsonify(AN.what_changed())

@app.get("/api/anomalies")
def anomalies():
    return jsonify(AN.category_anomalies())

@app.get("/api/category-trends")
def category_trends():
    return jsonify(AN.category_trends())

@app.get("/api/subscription-trend")
def subscription_trend():
    return jsonify(AN.subscription_creep())

@app.get("/api/action-plan")
def action_plan():
    s = summary().get_json()
    return jsonify(AN.daily_plan(allocation_drift=s.get("allocation_drift")))

@app.get("/api/heatmap")
def heatmap():
    return jsonify(AN.daily_spend_heatmap(int(request.args.get("weeks","12"))))

@app.get("/api/correlation-scatter")
def correlation_scatter():
    return jsonify(AN.correlation_scatter())

@app.get("/api/category-mix")
def category_mix():
    return jsonify(AN.category_mix())

@app.get("/api/data-freshness")
def data_freshness():
    today = date.today()
    def _row(label, last_date, threshold_days):
        days = (today - date.fromisoformat(last_date)).days if last_date else None
        return {"label": label, "last_date": last_date, "days_ago": days,
                "stale": days is None or days > threshold_days}
    sources = []
    for c in C.get_all_cards():
        stmts = C.get_statements(c["id"], 1)
        sources.append(_row(f"{c['name']} statement", stmts[0]["statement_date"] if stmts else None, 35))
    bs_cfg = BS.get_config()
    if bs_cfg.get("current_holding_id") or bs_cfg.get("savings_holding_id"):
        last = bs_cfg.get("last_sync")
        sources.append(_row("Bank account statement sync", last[:10] if last else None, 35))
    email_status = ES.get_status()
    if email_status.get("status") in ("connected", "expired"):
        last = email_status.get("last_sync")
        sources.append(_row("Email sync (card statements)" + (" - reconnect needed" if email_status["status"] == "expired" else ""),
                            last[:10] if last else None, 35))
    with get_db() as conn:
        row = conn.execute("SELECT MAX(snap_date) d FROM snapshots").fetchone()
    sources.append(_row("Net worth snapshot", row["d"], 14))
    return jsonify(sources)

@app.get("/api/history/by-class")
def history_by_class():
    with get_db() as conn:
        rows = conn.execute(
            """SELECT s.snap_date, h.asset_class, ROUND(SUM(s.value_base),2) total
               FROM snapshots s JOIN holdings h ON s.holding_id=h.id
               GROUP BY s.snap_date, h.asset_class ORDER BY s.snap_date""").fetchall()
    dates = sorted({r["snap_date"] for r in rows})
    classes = sorted({r["asset_class"] for r in rows})
    by_date = {}
    for r in rows:
        by_date.setdefault(r["snap_date"], {})[r["asset_class"]] = r["total"]
    series = [{"asset_class": c, "values": [by_date.get(d, {}).get(c, 0) for d in dates]} for c in classes]
    return jsonify({"dates": dates, "series": series})

@app.get("/api/fx-exposure")
def fx_exposure():
    fx = latest_fx()
    with get_db() as conn: rows = conn.execute("SELECT * FROM holdings WHERE archived=0").fetchall()
    by_ccy = {}; total = 0.0
    for r in rows:
        v = value_base(r, fx); total += v
        by_ccy[r["currency"]] = by_ccy.get(r["currency"], 0) + v
    exposures = []
    for ccy, v in sorted(by_ccy.items(), key=lambda x: -abs(x[1])):
        if ccy == CCY(): continue
        exposures.append({
            "currency": ccy, "exposure_base": round(v, 2),
            "pct_of_total": round(v/total*100, 1) if total else 0,
            "rate_to_base": fx.get(ccy), "sensitivity_1pct_base": round(v*0.01, 2),
        })
    return jsonify({
        "total_base": round(total, 2),
        "base_exposure_base": round(by_ccy.get(CCY(), 0), 2),
        "base_pct": round(by_ccy.get(CCY(), 0)/total*100, 1) if total else 0,
        "exposures": exposures,
    })


# ─── goals ──────────────────────────────────────────────────────────────────
@app.get("/api/goals")
def list_goals():
    with get_db() as conn:
        rows = [dict(r) for r in conn.execute("SELECT * FROM goals ORDER BY target_date").fetchall()]
        hist = [dict(r) for r in conn.execute(
            "SELECT snap_date,ROUND(SUM(value_base),2) total FROM snapshots GROUP BY snap_date ORDER BY snap_date").fetchall()]
    fx = latest_fx()
    with get_db() as conn: hrows = conn.execute("SELECT * FROM holdings WHERE archived=0").fetchall()
    current_total = sum(value_base(r, fx) for r in hrows)
    today = date.today()
    for g in rows:
        target_date = date.fromisoformat(g["target_date"])
        days_remaining = (target_date - today).days
        months_remaining = max(days_remaining/30.44, 0.01)
        g["current_total"] = round(current_total, 2)
        g["pct_complete"] = round(min(100, max(0, current_total/g["target_amount"]*100)), 1) if g["target_amount"] else 0
        g["days_remaining"] = days_remaining
        g["required_monthly"] = round((g["target_amount"]-current_total)/months_remaining, 2) if days_remaining > 0 else None
        g["projection_available"] = False
        if len(hist) >= 2:
            first, last = hist[0], hist[-1]
            days_span = (date.fromisoformat(last["snap_date"])-date.fromisoformat(first["snap_date"])).days
            if days_span >= 30:  # a shorter window extrapolates noise into a confident-looking verdict
                rate_per_day = (last["total"]-first["total"])/days_span
                projected = last["total"] + rate_per_day*max(days_remaining, 0)
                g["projection_available"] = True
                g["projected_total"] = round(projected, 2)
                g["on_track"] = projected >= g["target_amount"]
    return jsonify(rows)

@app.post("/api/goals")
def add_goal():
    b = request.json or {}
    if not b.get("name") or not b.get("target_amount") or not b.get("target_date"):
        return jsonify({"error":"name, target_amount and target_date required"}),400
    now = datetime.utcnow().isoformat()
    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO goals(name,target_amount,target_date,created_at,updated_at) VALUES (?,?,?,?,?)",
            (b["name"], float(b["target_amount"]), b["target_date"], now, now))
    return jsonify({"id":cur.lastrowid}),201

@app.put("/api/goals/<int:gid>")
def update_goal(gid):
    b = request.json or {}
    cols = [c for c in ("name","target_amount","target_date") if c in b]
    if cols:
        with get_db() as conn:
            conn.execute(f"UPDATE goals SET {','.join(c+'=?' for c in cols)}, updated_at=? WHERE id=?",
                         [b[c] for c in cols]+[datetime.utcnow().isoformat(), gid])
    return jsonify({"ok":True})

@app.delete("/api/goals/<int:gid>")
def delete_goal(gid):
    with get_db() as conn: conn.execute("DELETE FROM goals WHERE id=?",(gid,))
    return jsonify({"ok":True})


# ─── email sync ───────────────────────────────────────────────────────────────
@app.get("/api/email/status")
def email_status(): return jsonify(ES.get_status())

@app.get("/api/email/connect")
def email_connect():
    url,err=ES.get_auth_url()
    if err: return jsonify({"error":err}),400
    return redirect(url)

@app.get("/api/email/callback")
def email_callback():
    code=request.args.get("code"); state=request.args.get("state")
    if not code: return redirect("/?email=error")
    ok,err=ES.exchange_code(code,state)
    return redirect("/?email=connected" if ok else "/?email=error")

@app.post("/api/email/sync")
def email_sync():
    days=int(request.json.get("days_back",90) if request.json else 90)
    result=ES.sync_emails(days, backfill=bool((request.json or {}).get("backfill")))
    return jsonify(result)

@app.put("/api/email/imap")
def email_imap_save():
    b=request.json or {}
    if not b.get("user") or not b.get("password"): return jsonify({"ok":False,"error":"Enter your Gmail address and the App Password"}),400
    try: ES.save_imap(b["user"], b["password"])
    except ES.GmailAuthError as e: return jsonify({"ok":False,"error":str(e)}),400
    except Exception as e: return jsonify({"ok":False,"error":f"Could not reach Gmail: {e}"}),502
    return jsonify({"ok":True})

@app.delete("/api/email/imap")
def email_imap_remove():
    ES.remove_imap(); return jsonify({"ok":True})

@app.post("/api/email/disconnect")
def email_disconnect():
    ES.disconnect(); return jsonify({"ok":True})


# ─── CSV import ───────────────────────────────────────────────────────────────
@app.post("/api/import/statement")
def import_csv():
    """Import a bank CSV statement to update a holding's balance."""
    f = request.files.get("file")
    holding_id = request.form.get("holding_id")
    if not f or not holding_id: return jsonify({"error":"file and holding_id required"}),400
    content=f.read().decode("utf-8","ignore")
    reader=csv.reader(io.StringIO(content))
    rows=list(reader)
    # try to find the closing/final balance
    balance=None
    for row in reversed(rows):
        for cell in row:
            cell=cell.strip().replace(",","")
            try:
                val=float(cell)
                if val>0: balance=val; break
            except: pass
        if balance: break
    if balance:
        with get_db() as conn:
            conn.execute("UPDATE holdings SET manual_value=?,updated_at=? WHERE id=?",
                         (balance,datetime.utcnow().isoformat(),int(holding_id)))
        return jsonify({"ok":True,"balance":balance,"rows":len(rows)})
    return jsonify({"ok":False,"error":"Could not detect balance in CSV","rows":len(rows)})


# ─── budget ───────────────────────────────────────────────────────────────────
@app.get("/api/budget")
def get_budget():
    return jsonify(AN.get_budget_summary())

@app.get("/api/budget/actuals")
def get_budget_actuals():
    return jsonify(BG.actuals())

@app.get("/api/budget/report-card")
def get_report_card(): return _safe(BG.report_card)

@app.get("/api/budget/checkup")
def get_budget_checkup():
    return jsonify(BG.checkup())

@app.put("/api/budget/<int:bid>")
def update_budget_item(bid):
    b=request.json or {}
    cur=next((i for i in BG.list_items() if i["id"]==bid), None)
    if not cur: return jsonify({"ok":False,"error":"That line no longer exists."}),404
    merged={"label":cur["label"],"type":cur["type"],"amount":cur["amount"],"frequency":cur["frequency"],
            "source":cur["source"],"categories":cur["categories"],"off_card":cur["off_card"], **b}
    try: BG.save_item(merged, bid)
    except ValueError as e: return jsonify({"ok":False,"error":str(e)}),400
    return jsonify({"ok":True})

@app.post("/api/budget")
def add_budget_item():
    try: new_id=BG.save_item(request.json or {})
    except ValueError as e: return jsonify({"ok":False,"error":str(e)}),400
    return jsonify({"ok":True,"id":new_id}),201

@app.delete("/api/budget/<int:bid>")
def delete_budget_item(bid):
    BG.delete_item(bid)
    return jsonify({"ok":True})


# ─── settings ─────────────────────────────────────────────────────────────────
# Settings the page may read or write. The settings table also holds the password hash, session tokens and every API key, so nothing outside
# this list is sent to the browser or accepted from it (a write to pw_hash or session_tokens would be a way into the whole app).
PUBLIC_SETTINGS={"pay_day","date_order","home_country","timezone","payslip_spend_words","my_name","family_names","app_title","base_currency","monthly_income","theme","auto_sync_enabled","auto_sync_interval_hours","auto_sync_last_run","auto_sync_v2","bill_reminders_enabled",
                 "ai_categorise","bank_stmt_current_holding_id","bank_stmt_savings_holding_id","bank_stmt_last_sync","bank_stmt_last_current","bank_stmt_last_savings",
                 "ibkr_last_sync","ibkr_last_error","etoro_last_sync","etoro_last_error","payslip_last_scan","payslip_last_error","gmail_imap_user"}
WRITABLE_SETTINGS={"pay_day","date_order","home_country","timezone","payslip_spend_words","base_currency","my_name","family_names","app_title","monthly_income","theme","auto_sync_enabled","auto_sync_interval_hours","bill_reminders_enabled","ai_categorise"}

def _check_prefs(b):
    """Validates the preference settings; returns an error message or None."""
    if str(b.get("pay_day","")).strip():
        try:
            if not 1<=int(b["pay_day"])<=28: raise ValueError
        except (TypeError, ValueError): return "Payday must be a day of the month from 1 to 28."
    if b.get("date_order","dmy") not in ("dmy","mdy"): return "Date order must be day first or month first."
    tz=str(b.get("timezone","")).strip()
    if tz:
        try:
            from zoneinfo import ZoneInfo; ZoneInfo(tz)
        except Exception: return "Unknown time zone. Use a name like Europe/Berlin or America/Chicago."
    return None

@app.get("/api/countries")
def countries():
    import categorizer as CZ
    return jsonify(sorted(CZ.ISO2))

@app.get("/api/settings")
def get_settings():
    with get_db() as conn: rows=conn.execute("SELECT key,value FROM settings").fetchall()
    return jsonify({r["key"]:r["value"] for r in rows if r["key"] in PUBLIC_SETTINGS})

@app.put("/api/settings")
def put_settings():
    b=request.json or {}
    bad=[k for k in b if k not in WRITABLE_SETTINGS]
    if bad: return jsonify({"ok":False,"error":"Not a changeable setting: "+", ".join(bad)}),400
    err=_check_prefs(b)
    if err: return jsonify({"ok":False,"error":err}),400
    ccy=str(b.get("base_currency","")).strip().upper() if "base_currency" in b else None
    if ccy is not None and not (len(ccy)==3 and ccy.isalpha()): return jsonify({"ok":False,"error":"Use a three-letter currency code such as USD, EUR, GBP or JPY."}),400
    changed = ccy is not None and ccy!=CCY()
    with get_db() as conn:
        for k,v in b.items(): conn.execute("INSERT OR REPLACE INTO settings VALUES (?,?)",(k,ccy if k=="base_currency" else str(v)[:500]))
        if changed:                                    # saved exchange rates were relative to the old currency
            conn.execute("DELETE FROM fx_rates"); conn.execute("INSERT INTO fx_rates VALUES (?,?,?)",(ccy,1.0,date.today().isoformat()))
    if changed:
        with get_db() as conn: curs={r["currency"] for r in conn.execute("SELECT DISTINCT currency FROM holdings WHERE archived=0").fetchall()}
        for c in curs-{ccy}: _ensure_fx(c)
    return jsonify({"ok":True,"currency_changed":changed})


# ─── monthly report export ─────────────────────────────────────────────────────
_report_cycle=''
def _report_data():
    s=summary().get_json()
    done=[m for m in AN.spend_cycles()["months"] if m["complete"]]
    by_cat=[{"category":k,"total":v} for k,v in sorted(done[-1]["cats"].items(), key=lambda kv:-kv[1])] if done else []
    global _report_cycle
    _report_cycle=done[-1]["month"] if done else "none yet"
    budget=get_budget().get_json()
    return s,by_cat,budget

@app.get("/api/reports/monthly.csv")
def report_csv():
    s,by_cat,budget=_report_data()
    buf=io.StringIO(); w=csv.writer(buf)
    w.writerow(["Wealth Tracker — Monthly Report",date.today().isoformat()])
    w.writerow([])
    w.writerow(["Net Worth Summary"])
    w.writerow([f"Total ({CCY()})",s["total_base"]])
    w.writerow([f"Safe ({CCY()})",s["safe_base"],f"{s['safe_pct']}%"])
    w.writerow([f"Growth ({CCY()})",s["growth_base"],f"{s['growth_pct']}%"])
    w.writerow([])
    w.writerow(["By Asset Class"]); w.writerow(["Class",f"Value ({CCY()})","% of Total"])
    for c in s["by_class"]: w.writerow([c["label"],c["value"],f"{c['pct']}%"])
    w.writerow([])
    w.writerow([f"Spend by Category (latest complete statement cycle: {_report_cycle})"]); w.writerow(["Category",f"Total ({CCY()})"])
    for c in by_cat: w.writerow([c["category"],c["total"]])
    w.writerow([])
    w.writerow(["Budget"])
    inc=AN.income_basis()
    if inc["source"]=="payslip":
        w.writerow([f"Gross pay (payslip {inc['period']})",inc["gross"]]); w.writerow(["Net pay",inc["net"]]); w.writerow(["Payroll deductions",inc["deductions"]])
    w.writerow(["Budget income",budget["income"]]); w.writerow(["Expenses",budget["expenses"]])
    w.writerow(["Savings",budget["savings"]]); w.writerow(["Invest",budget["invest"]])
    w.writerow(["Surplus",budget["surplus"]])
    resp=make_response(buf.getvalue())
    resp.headers["Content-Type"]="text/csv"
    resp.headers["Content-Disposition"]=f"attachment; filename=wealth-report-{date.today().isoformat()}.csv"
    return resp

@app.get("/api/reports/monthly.pdf")
def report_pdf():
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.lib import colors
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet
    s,by_cat,budget=_report_data()
    styles=getSampleStyleSheet()
    story=[Paragraph("Wealth Tracker — Monthly Report",styles["Title"]),
           Paragraph(date.today().isoformat(),styles["Normal"]),Spacer(1,12)]

    def section(title,rows,header=None):
        story.append(Paragraph(title,styles["Heading2"]))
        data=([header] if header else [])+rows
        t=Table(data,hAlign="LEFT")
        t.setStyle(TableStyle([
            ("FONTSIZE",(0,0),(-1,-1),9),("BOTTOMPADDING",(0,0),(-1,-1),4),
            ("GRID",(0,0),(-1,-1),0.4,colors.HexColor("#cccccc")),
            ("BACKGROUND",(0,0),(-1,0),colors.HexColor("#eeeeee")) if header else ("BACKGROUND",(0,0),(-1,-1),colors.white),
        ]))
        story.append(t); story.append(Spacer(1,14))

    section("Net Worth Summary",[
        [f"Total ({CCY()})",f"{s['total_base']:,.2f}"],
        [f"Safe ({CCY()})",f"{s['safe_base']:,.2f}",f"{s['safe_pct']}%"],
        [f"Growth ({CCY()})",f"{s['growth_base']:,.2f}",f"{s['growth_pct']}%"],
    ])
    section("By Asset Class",[[c["label"],f"{c['value']:,.2f}",f"{c['pct']}%"] for c in s["by_class"]],
            header=["Class",f"Value ({CCY()})","% of Total"])
    section(f"Spend by Category (latest complete statement cycle: {_report_cycle})",[[c["category"],f"{c['total']:,.2f}"] for c in by_cat],
            header=["Category",f"Total ({CCY()})"])
    inc=AN.income_basis()
    section("Budget",([[f"Gross pay (payslip {inc['period']})",f"{inc['gross']:,.2f}"],["Net pay",f"{inc['net']:,.2f}"]] if inc["source"]=="payslip" else [])+[
        ["Budget income",f"{budget['income']:,.2f}"],["Expenses",f"{budget['expenses']:,.2f}"],
        ["Savings",f"{budget['savings']:,.2f}"],["Invest",f"{budget['invest']:,.2f}"],
        ["Surplus",f"{budget['surplus']:,.2f}"],
    ])

    buf=io.BytesIO()
    SimpleDocTemplate(buf,pagesize=A4,topMargin=20*mm,bottomMargin=20*mm).build(story)
    buf.seek(0)
    resp=make_response(buf.read())
    resp.headers["Content-Type"]="application/pdf"
    resp.headers["Content-Disposition"]=f"attachment; filename=wealth-report-{date.today().isoformat()}.pdf"
    return resp


# ─── backup / restore ──────────────────────────────────────────────────────────
from database import DB_PATH

@app.get("/api/backup")
def download_backup():
    """A copy of the data for safekeeping, without sign-in material (password hash, sessions, Face ID keys). Secrets inside it are sealed to this Windows account."""
    if SEC.is_remote(): return jsonify({"error":"Do this on your PC."}),403
    import sqlite3, tempfile, shutil
    tmp=os.path.join(tempfile.mkdtemp(),"backup.db")
    src=sqlite3.connect(DB_PATH); dst=sqlite3.connect(tmp); src.backup(dst); src.close()
    for t in ("sessions","auth_events","passkeys","enroll_tokens","push_subs","notif_log"): dst.execute(f"DROP TABLE IF EXISTS {t}")
    dst.execute("DELETE FROM settings WHERE key IN ('pw_hash','pw_salt','session_tokens','oauth_state','passkey_user','owner_login','vapid_private','vapid_public')")
    dst.commit(); dst.execute("VACUUM"); dst.close()
    data=open(tmp,"rb").read(); shutil.rmtree(os.path.dirname(tmp),ignore_errors=True)
    return Response(data,mimetype="application/octet-stream",headers={"Content-Disposition":f"attachment; filename=portfolio-backup-{date.today().isoformat()}.db"})


# ─── planning: money plan, insights, card value ───────────────────────────────
import planner as PL

def _safe(fn, *a, **k):
    try:
        return jsonify(fn(*a, **k))
    except ValueError as e:                       # a message written for the user
        return jsonify({"error": str(e)}), 400
    except Exception:
        app.logger.exception("error in %s", request.path)
        return jsonify({"error": "Something went wrong."}), 500

@app.get("/api/plan")
def plan_core():
    return _safe(lambda: {"plan": PL.money_plan(), "forecast": PL.forecast(), "settings": PL.settings(), "review": AG.review_state()})

@app.get("/api/plan/weather")
def plan_weather(): return _safe(PL.market_weather)

@app.get("/api/plan/projection")
def plan_projection():
    t = request.args.get("target", type=float)
    return _safe(PL.projection, min(30, request.args.get("years", 10, type=int)), t)

@app.get("/api/plan/simulate")
def plan_simulate():
    a = request.args
    S = PL.settings()
    eq = min(100, max(0, a.get("equity", 70, type=float))) / 100
    r = eq * S["assume"]["equity"][0] + (1 - eq) * S["savings_rate"]
    return _safe(PL.simulate, a.get("start", 0, type=float), a.get("monthly", 0, type=float), min(40, a.get("years", 5, type=float)), r,
                 eq * S["assume"]["equity"][1], a.get("target", type=float))

@app.get("/api/plan/backtest")
def plan_backtest():
    return _safe(PL.dca_backtest, (request.args.get("symbol") or IN.core()).strip()[:12], request.args.get("months", 12, type=int))

@app.put("/api/plan/settings")
def plan_settings_put():
    return _safe(PL.save_settings, request.get_json(force=True) or {})

import intel as IN

@app.get("/api/market/briefing")
def market_briefing(): return _safe(IN.briefing)

@app.get("/api/market/timing")
def market_timing():
    a=request.args
    return _safe(lambda: {**IN.buy_timing((a.get("symbol") or IN.core()).strip()[:12], a.get("lag",1,type=int), 9, a.get("usd",1000,type=float)),
                          "next": IN.next_dates(a.get("lag",1,type=int), 3), "hours": IN.trading_hours()})

import signals as SG

@app.get("/api/market/signals")
def market_signals():
    def go():
        from concurrent.futures import ThreadPoolExecutor
        w=M.get_watchlist()
        def one(x):
            c=SG.cached(x["symbol"])
            return {"symbol":x["symbol"],"name":x.get("name") or x["symbol"],"quant":(c or {}).get("quant") or SG.quant(x["symbol"]),"ai":(c or {}).get("ai"),"generated_at":(c or {}).get("generated_at"),"stale":(c or {}).get("stale")}
        with ThreadPoolExecutor(6) as ex: return {"items":list(ex.map(one,w))}
    return _safe(go)

@app.get("/api/market/signal")
def market_signal():
    a=request.args
    return _safe(SG.verdict,(a.get("symbol") or "").strip(),a.get("refresh")=="1")

@app.get("/api/market/discover")
def market_discover():
    c=SG._cache_get("discover",24)
    if request.args.get("refresh")!="1": return jsonify(c or {"picks":None})
    return _safe(SG.discover,True)

import wealth as WL
import bank_tx as BT

@app.get("/api/invest/habit")
def invest_habit(): return _safe(BT.habit)

@app.post("/api/bank/sync")
def bank_sync(): return _safe(lambda: BT.sync(int(request.args.get("months",14))))

@app.get("/api/bank/sources")
def bank_sources_get(): return jsonify({"sources":BT.list_sources(),"plugin":[x["name"] for x in BT.sources()][:len(BT.sources())-len(BT.list_sources())]})

@app.put("/api/bank/sources")
def bank_sources_put(): return jsonify({"ok":True,"sources":BT.save_sources((request.json or {}).get("sources"))})

@app.post("/api/bank/import-csv")
def bank_import_csv():
    b=request.json or {}
    try: return jsonify(BT.import_csv(b.get("text",""), b.get("bank","CSV import"), b.get("mapping"), b.get("spend_is_negative")))
    except ValueError as e: return jsonify({"ok":False,"error":str(e)}),400

@app.post("/api/cards/<int:cid>/import-csv")
def card_import_csv(cid):
    """A CSV export from the card's issuer becomes one statement (dated by its last transaction) with its transactions categorised."""
    b=request.json or {}
    try: return jsonify(C.import_csv(cid, b.get("text",""), b.get("mapping"), b.get("spend_is_negative")))
    except ValueError as e: return jsonify({"ok":False,"error":str(e)}),400

@app.get("/api/bank/summary")
def bank_summary(): return _safe(lambda: BT.summary(request.args.get("months",12,type=int)))

@app.get("/api/bank/transactions")
def bank_transactions():
    k=request.args.get("kind"); m=request.args.get("months",12,type=int)
    return _safe(lambda: {"rows":BT.rows(m,[k] if k else None)[:int(request.args.get("limit",300))],"kinds":sorted(BT.ALL_KINDS)})

@app.put("/api/bank/tx/<int:tid>")
def bank_tx_kind(tid):
    k=(request.json or {}).get("kind","")
    if k not in BT.ALL_KINDS: return jsonify({"error":"unknown kind"}),400
    with get_db() as conn: conn.execute("UPDATE bank_transactions SET user_kind=?, kind=? WHERE id=?",(k,k,tid))
    return jsonify({"ok":True})


@app.get("/api/wealth/needs-wants")
def wealth_needs_wants(): return _safe(WL.needs_wants)

@app.get("/api/wealth/merchants")
def wealth_merchants(): return _safe(WL.merchants)

@app.put("/api/wealth/need")
def wealth_need():
    b=request.json or {}
    try: WL.set_need(b.get("key",""), b.get("need",""))
    except ValueError as e: return jsonify({"error":str(e)}),400
    return jsonify({"ok":True})

@app.get("/api/wealth/leaks")
def wealth_leaks(): return _safe(WL.leaks)

@app.get("/api/ai/usage")
def ai_usage():
    import aiusage
    return jsonify(aiusage.summary(request.args.get("days",30,type=int)))

@app.get("/api/market/ideas")
def market_ideas(): return _safe(IN.ideas)

@app.get("/api/plan/requirements")
def plan_requirements_get():
    return _safe(PL.requirements)

@app.put("/api/plan/requirements")
def plan_requirements_put():
    try:
        return jsonify(PL.save_requirements(request.get_json(force=True) or {}))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

@app.get("/api/plan/insights")
def plan_insights_get(): return _safe(lambda: {"insights": PL.insights()})

@app.post("/api/plan/insights/<iid>/dismiss")
def plan_insights_dismiss(iid):
    PL.dismiss(iid)
    return jsonify({"ok": True})

@app.get("/api/cards/fx")
def cards_fx(): return _safe(PL.fx_report, request.args.get("period", "m12"))

@app.get("/api/cards/usage")
def cards_usage(): return _safe(PL.card_usage, request.args.get("period", "m12"))

@app.get("/api/cards/recurring")
def cards_recurring(): return _safe(lambda: {"items": PL.recurring()})

@app.get("/api/cards/rewards")
def cards_rewards():
    import rewards as RW
    p = request.args.get("period", "m12")
    return _safe(lambda: {"report": RW.report(p), "routing": RW.routing(p)})

_reparse = {"running": False, "report": None}

@app.post("/api/cards/reparse")
def cards_reparse():
    if _reparse["running"]:
        return jsonify({**_reparse})
    def work():
        try:
            import email_sync as _ES
            _reparse["report"] = _ES.reparse_statements()
        except Exception as e:
            _reparse["report"] = {"ok": False, "error": str(e)}
        _reparse["running"] = False
    _reparse["running"] = True; _reparse["report"] = None
    threading.Thread(target=work, daemon=True).start()
    return jsonify({**_reparse})

@app.get("/api/cards/reparse")
def cards_reparse_status(): return jsonify({**_reparse})

@app.put("/api/cards/<int:cid>/info")
def cards_info(cid):
    b = request.get_json(force=True) or {}
    info = {}
    if isinstance(b.get("rules"), list):
        info["rules"] = [r for r in b["rules"] if isinstance(r, dict) and r.get("label")][:8]
    for k in ("annual_fee", "fx_fee_pct"):
        if b.get(k) not in (None, ""):
            try: info[k] = max(0.0, float(b[k]))
            except ValueError: pass
    if "benefits" in b: info["benefits"] = str(b["benefits"])[:300]
    S = PL.settings()
    PL.save_settings({"card_info": {**S["card_info"], str(cid): {**S["card_info"].get(str(cid), {}), **info}}})
    return jsonify({"ok": True})

@app.post("/api/plan/review")
def plan_review_start():
    if not AG.review_state().get("running"):
        threading.Thread(target=AG.run_review, daemon=True).start()
    return jsonify({**AG.review_state(), "running": True})

@app.get("/api/plan/review")
def plan_review_get(): return jsonify(AG.review_state())

# ─── automated background sync (opt-in via Settings) ──────────────────────────
def _daily_snapshot():
    """Net-worth history should not depend on you opening the app: once a day the loop refreshes rates and prices, then saves the snapshot."""
    today=date.today().isoformat()
    with get_db() as conn:
        if conn.execute("SELECT 1 FROM snapshots WHERE snap_date=? LIMIT 1",(today,)).fetchone(): return
        curs=[r["currency"] for r in conn.execute("SELECT DISTINCT currency FROM holdings WHERE archived=0").fetchall()]
        ids=[r["id"] for r in conn.execute("SELECT id FROM holdings WHERE archived=0 AND price_mode IN ('ticker','nav') AND symbol!=''").fetchall()]
    for c in curs:
        if c!=CCY():
            r=P.fx_to_base(c)
            if r:
                with get_db() as conn: conn.execute("INSERT OR REPLACE INTO fx_rates VALUES (?,?,?)",(c,r,today))
    for hid in ids: _refresh_one(hid)
    take_snapshot()

def _auto_sync_loop():
    while True:
        try: _daily_snapshot()
        except Exception as e: print("daily snapshot failed:",e)
        try:
            if IB.due():
                try: _ibkr_sync()
                except Exception as e: print("auto-sync: IBKR sync failed:",e)
        except Exception as e:
            print("auto-sync loop error (ibkr):",e)
        try:
            if EO.due():
                try: EO.sync(take_snapshot)
                except Exception as e: print("auto-sync: eToro sync failed:",e)
        except Exception as e:
            print("auto-sync loop error (etoro):",e)
        try:
            if setting("auto_sync_enabled","1")=="1":
                interval_h=float(setting("auto_sync_interval_hours","12") or 12)
                last=setting("auto_sync_last_run","")
                due=True
                if last:
                    try: due=(datetime.utcnow()-datetime.fromisoformat(last)).total_seconds()>=interval_h*3600
                    except ValueError: due=True
                if due:
                    _run_mail_jobs()
                    with get_db() as conn:
                        conn.execute("INSERT OR REPLACE INTO settings VALUES ('auto_sync_last_run',?)",
                                     (datetime.utcnow().isoformat(),))
        except Exception as e:
            print("auto-sync loop error:",e)
        try: NT.price_check()
        except Exception as e: print("price alert check failed:", e)
        try: NT.market_check()
        except Exception as e: print("market alert check failed:", e)
        try:
            if AG.review_due():
                AG.run_review()
        except Exception as e:
            print("weekly review error:", e)
        _time.sleep(1800)  # re-check every 30 minutes; actual sync only runs once per configured interval


@app.get("/api/notify/key")
def notify_key(): return jsonify({"key":NT.public_key(),"phones":NT.count()})

@app.post("/api/notify/subscribe")
def notify_subscribe():
    if not SEC.is_remote(): return jsonify({"ok":False,"error":"Turn alerts on from your iPhone."}),400
    b=request.json or {}
    try: NT.subscribe(b.get("subscription"), b.get("name",""))
    except ValueError as e: return jsonify({"ok":False,"error":str(e)}),400
    return jsonify({"ok":True})

@app.post("/api/notify/unsubscribe")
def notify_unsubscribe():
    NT.unsubscribe((request.json or {}).get("endpoint","")); return jsonify({"ok":True})

@app.post("/api/notify/test")
def notify_test():
    return jsonify({"ok":True,"sent":NT.send("test:"+str(_time.time()),"Alerts are on","You will only hear from Aurum for new statements and rare market moments.")})

AG.G.update(globals())     # the agent calls these app functions directly

# ─── launch ───────────────────────────────────────────────────────────────────
if __name__=="__main__":
    import serve; serve.main()
