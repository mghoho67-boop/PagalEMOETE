"""
Flask API bot (OB55) -- bot.py ko login/packets use garchha.
Routes:  /5?uid=   /6?uid=   /3?uid=          (squad invite)
         /emote?uid1=..&uid6=&emote_id=        (bot pahile nai squad ma bhayeko bela)
         /join?tc=<team_code>&uid1=..&emote_id= (team code ma join -> emote -> leave)
Run:     python api_bot.py        (ckr.txt ma uid/password)
Optional: API_KEY env set garyo bhane ?key=<API_KEY> chaincha.
"""
import os
import asyncio
import threading
from flask import Flask, request, jsonify

import bot  # bot.py (OB55 login + packet builders)

app = Flask(__name__)
LOOP = None
API_KEY = os.environ.get("API_KEY", "")
SQUAD_STAY_SECONDS = 8   # invite pachi squad ma yati second basera leave garchha
STATE = {"key": None, "iv": None, "region": None, "uid": None, "client_version": "1.132.1"}
_lock = None

# ── login huda key/iv/region capture garne (bot.py lai chhoena) ──
_orig_run_account = bot.run_account


async def _patched_run_account(account_data):
    STATE["key"] = account_data["aes_ak"]
    STATE["iv"] = account_data["iv_i"]
    STATE["region"] = account_data.get("region") or "SG"
    STATE["uid"] = int(account_data["account_id"])
    STATE["client_version"] = account_data.get("client_version") or "1.132.1"
    await _orig_run_account(account_data)

bot.run_account = _patched_run_account


# ── helpers ──
def _get_lock():
    global _lock
    if _lock is None:
        _lock = asyncio.Lock()
    return _lock


def _writer():
    w = bot._online_writer_ref[0]
    return w if (w is not None and not w.is_closing()) else None


async def _send(pkt):
    w = _writer()
    if w is None:
        raise RuntimeError("Bot online socket ready chhaina")
    w.write(pkt)
    await w.drain()


def _ready():
    return STATE["key"] is not None and _writer() is not None


def _resolve_emote(val):
    val = str(val).strip()
    if val.isdigit() and int(val) >= 1000:
        return int(val)                 # seedhai emote id (909000001)
    return bot._find_emote(val)         # 1-384 number ya name (ak, dab...)


def build_join_by_code_packet(code, key, iv, region, client_version):
    # xC4.GenJoinSquadsPacket ko structure (TESTED NAHUNEKO -- OB55 ma milchha ki nai herna parchha)
    fields = {
        1: 4,
        2: {
            4: bytes.fromhex("01090a0b121920"),
            5: str(code),
            6: 6,
            8: 1,
            9: {2: 800, 6: 11, 8: str(client_version), 9: 5, 10: 1},
        },
    }
    return bot._wrap_packet(bot._serialize(fields), bot._squad_prefix(5, region), key, iv)


# ── actions ──
async def do_invite(size, uid):
    async with _get_lock():
        k, i, r = STATE["key"], STATE["iv"], STATE["region"]
        await _send(bot.build_open_squad_packet(size, k, i, r, STATE["client_version"]))
        await asyncio.sleep(0.3)
        await _send(bot.build_invite_squad_packet(1, uid, k, i, r))
        await asyncio.sleep(0.2)
        await _send(bot.build_invite_squad_packet(2, uid, k, i, r))
        await asyncio.sleep(SQUAD_STAY_SECONDS)
        await _send(bot.build_leave_squad_packet(STATE["uid"], k, i, r))


async def do_emote(uids, emote_id):
    k, i, r = STATE["key"], STATE["iv"], STATE["region"]
    for u in uids:
        await _send(bot.build_emote_packet(STATE["uid"], int(u), emote_id, k, i, r))
        await asyncio.sleep(0.05)


async def do_join_emote(code, uids, emote_id):
    async with _get_lock():
        k, i, r = STATE["key"], STATE["iv"], STATE["region"]
        await _send(build_join_by_code_packet(code, k, i, r, STATE["client_version"]))
        await asyncio.sleep(0.6)
        await do_emote(uids, emote_id)
        await asyncio.sleep(0.3)
        await _send(bot.build_leave_squad_packet(STATE["uid"], k, i, r))


def _run(coro, name):
    fut = asyncio.run_coroutine_threadsafe(coro, LOOP)

    def _done(f):
        try:
            f.result()
            print(f"[API] {name} done")
        except Exception as e:
            print(f"[API] {name} failed: {e}")
    fut.add_done_callback(_done)


def _auth_fail():
    if API_KEY and request.args.get("key") != API_KEY:
        return jsonify({"status": "error", "message": "bad key"}), 401
    if not _ready():
        return jsonify({"status": "error", "message": "bot not connected yet"}), 503
    return None


def _uids():
    out = []
    for n in range(1, 7):
        v = request.args.get(f"uid{n}")
        if v:
            if not v.isdigit():
                return None
            out.append(int(v))
    return out


# ── routes ──
def _invite_route(size):
    bad = _auth_fail()
    if bad:
        return bad
    u = request.args.get("uid", "")
    if not u.isdigit():
        return jsonify({"status": "error", "message": "uid missing/invalid"}), 400
    _run(do_invite(size, int(u)), f"invite{size} {u}")
    return jsonify({"status": "success", "target_uid": int(u), "message": f"{size}-Player Invite Sent!"})


@app.route("/3")
def r3(): return _invite_route(3)


@app.route("/5")
def r5(): return _invite_route(5)


@app.route("/6")
def r6(): return _invite_route(6)


@app.route("/emote")
def r_emote():
    bad = _auth_fail()
    if bad:
        return bad
    uids = _uids()
    eid = _resolve_emote(request.args.get("emote_id", ""))
    if not uids or not eid:
        return jsonify({"status": "error", "message": "uid1..uid6 ra valid emote_id chahincha"}), 400
    _run(do_emote(uids, eid), f"emote {eid}")
    return jsonify({"status": "success", "uids": uids, "emote_id": eid})


@app.route("/join")
def r_join():
    bad = _auth_fail()
    if bad:
        return bad
    code = request.args.get("tc")
    uids = _uids()
    eid = _resolve_emote(request.args.get("emote_id", ""))
    if not code or not uids or not eid:
        return jsonify({"status": "error", "message": "tc, uid1.. ra emote_id chahincha"}), 400
    _run(do_join_emote(code, uids, eid), f"join {code}")
    return jsonify({"status": "success", "team_code": code, "uids": uids, "emote_id": eid})


def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)


def main():
    global LOOP
    creds = bot.read_credentials() or {}
    token, uid, pw = creds.get("access_token", ""), creds.get("uid", ""), creds.get("password", "")
    if not token and not (uid.isdigit() and pw):
        print("ckr.txt ma uid+password ya access_token rakhnus")
        return
    LOOP = asyncio.new_event_loop()
    asyncio.set_event_loop(LOOP)
    threading.Thread(target=run_flask, daemon=True).start()
    bot.shutdown_requested = False

    async def runner():
        if token:
            await bot.run_token_account(token)
        else:
            await bot.run_forever(lambda: bot.run_guest_account(uid, pw))

    try:
        LOOP.run_until_complete(runner())
    except KeyboardInterrupt:
        bot.shutdown_requested = True


if __name__ == "__main__":
    main()
