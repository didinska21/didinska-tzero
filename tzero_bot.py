#!/usr/bin/env python3
"""
Tzero Season 0 helper bot — Base Sepolia testnet (chain 84532).

What it does (ONE wallet, legitimate actions only):
  1. Watches the missions closest to liftoff (via the site's own /api/home).
  2. Backs OTHER people's near-liftoff missions with >= 0.01 ETH on the
     bonding curve (Fuel points after a 60-minute hold; Crew bonus if the
     mission lifts off after your buy).
  3. Never sells inside the hold window — in fact it never sells at all
     (sells reduce net Fuel). Selling is intentionally not implemented.
  4. Stakes any $T0 balance in the $T0 staking contract and claims ETH
     rewards (Staking points accrue from earned rewards).
  5. Optionally launches your own mission (Liftoff points need >= 5 OTHER
     wallets backing it with >= 0.01 ETH each, held 60 min — the bot does
     NOT fake that with other wallets; it just launches and reports).
  6. Logs your points from /api/points/<address> every cycle.

Safety rails:
  - DRY_RUN=true by default: plans and logs every transaction, sends none.
  - Refuses to run live on any chain other than 84532.
  - Daily buy cap and per-cycle jitter; state persisted in state.json.
  - The private key is read from the environment / .env only. Never log it.

Project rules warning: Tzero states that wallets caught running bots,
farms or Sybil setups may earn nothing and be blocked from mainnet.
This script automates a single wallet's ordinary participation; using it
is your decision and your risk. See README.md.
"""

import json
import logging
import os
import random
import sys
import threading
import time
from datetime import datetime, timezone

import requests
from dotenv import load_dotenv
from web3 import Web3
from web3.exceptions import ContractLogicError

# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------

load_dotenv()

def env_bool(name: str, default: bool) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")

def env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except (TypeError, ValueError):
        return default

def env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default

PRIVATE_KEY        = os.getenv("PRIVATE_KEY", "").strip()
RPC_URL            = os.getenv("RPC_URL", "https://sepolia.base.org").strip()
API_BASE           = os.getenv("API_BASE", "https://testnet.tzer0.fun").strip()
DRY_RUN            = env_bool("DRY_RUN", True)

BUY_AMOUNT_ETH     = env_float("BUY_AMOUNT_ETH", 0.01)        # Fuel counts per 0.01 ETH net buy
MIN_PROGRESS_BPS   = env_int("MIN_PROGRESS_BPS", 9000)        # only back missions >= 90% to liftoff
MIN_HOLDERS        = env_int("MIN_HOLDERS", 3)                # skip near-empty missions
MAX_BUYS_PER_DAY   = env_int("MAX_BUYS_PER_DAY", 3)
SLIPPAGE_BPS       = env_int("SLIPPAGE_BPS", 1000)            # 10% min-out protection on curve buys
MIN_BALANCE_ETH    = env_float("MIN_BALANCE_ETH", 0.005)      # below this: warn to top up (faucet is manual)
GAS_RESERVE_ETH    = env_float("GAS_RESERVE_ETH", 0.002)      # never spend the last gas money

STAKE_T0           = env_bool("STAKE_T0", True)
CLAIM_REWARDS      = env_bool("CLAIM_REWARDS", True)

LAUNCH_ENABLED     = env_bool("LAUNCH_ENABLED", False)        # own mission for Liftoff points
LAUNCH_NAME        = os.getenv("LAUNCH_NAME", "").strip()
LAUNCH_SYMBOL      = os.getenv("LAUNCH_SYMBOL", "").strip()
LAUNCH_URI         = os.getenv("LAUNCH_URI", "").strip()      # metadata URI; "" is accepted by create()
MAX_LAUNCH_PER_DAY = 2                                        # points count at most 2/day (project rule)

LOOP_INTERVAL_SEC  = env_int("LOOP_INTERVAL_SEC", 900)        # 15 min base cycle
JITTER_SEC         = env_int("JITTER_SEC", 180)               # +/- jitter so the rhythm is not metronomic
STATE_FILE         = os.getenv("STATE_FILE", "state.json")

EXPECTED_CHAIN_ID  = 84532  # Base Sepolia

# --------------------------------------------------------------------------
# Contracts (verified on Sourcify, chain 84532 — see ../rea-evidence/abis/)
# --------------------------------------------------------------------------

LAUNCHPAD = "0xbce48917C41BB45Ad15f859E4Bf0e4EEa74bAd5c"
STAKING   = "0x221De22612E65093Fc43164dae84fa6Bd79E6239"
T0_TOKEN  = "0x15522727F10Ea666Ebb4fD4c23B394580F10FED4"

LAUNCHPAD_ABI = [
    {"name": "buy", "type": "function", "stateMutability": "payable",
     "inputs": [{"name": "token", "type": "address"}, {"name": "minTokensOut", "type": "uint256"}],
     "outputs": [{"name": "tokensOut", "type": "uint256"}]},
    {"name": "create", "type": "function", "stateMutability": "payable",
     "inputs": [{"name": "name", "type": "string"}, {"name": "symbol", "type": "string"},
                {"name": "uri", "type": "string"}],
     "outputs": [{"name": "token", "type": "address"}]},
    {"name": "quoteBuy", "type": "function", "stateMutability": "view",
     "inputs": [{"name": "token", "type": "address"}, {"name": "nativeIn", "type": "uint256"}],
     "outputs": [{"name": "tokensOut", "type": "uint256"},
                 {"name": "fee", "type": "uint256"},
                 {"name": "", "type": "uint256"}]},
    {"name": "progressBps", "type": "function", "stateMutability": "view",
     "inputs": [{"name": "token", "type": "address"}],
     "outputs": [{"name": "", "type": "uint256"}]},
    {"name": "CREATION_FEE", "type": "function", "stateMutability": "view",
     "inputs": [], "outputs": [{"name": "", "type": "uint256"}]},
]

ERC20_ABI = [
    {"name": "balanceOf", "type": "function", "stateMutability": "view",
     "inputs": [{"name": "account", "type": "address"}], "outputs": [{"name": "", "type": "uint256"}]},
    {"name": "allowance", "type": "function", "stateMutability": "view",
     "inputs": [{"name": "owner", "type": "address"}, {"name": "spender", "type": "address"}],
     "outputs": [{"name": "", "type": "uint256"}]},
    {"name": "approve", "type": "function", "stateMutability": "nonpayable",
     "inputs": [{"name": "spender", "type": "address"}, {"name": "amount", "type": "uint256"}],
     "outputs": [{"name": "", "type": "bool"}]},
]

STAKING_ABI = [
    {"name": "stake", "type": "function", "stateMutability": "nonpayable",
     "inputs": [{"name": "amount", "type": "uint256"}], "outputs": []},
    {"name": "claim", "type": "function", "stateMutability": "nonpayable",
     "inputs": [], "outputs": [{"name": "", "type": "uint256"}]},
    {"name": "pending", "type": "function", "stateMutability": "view",
     "inputs": [{"name": "user", "type": "address"}], "outputs": [{"name": "", "type": "uint256"}]},
    {"name": "staked", "type": "function", "stateMutability": "view",
     "inputs": [{"name": "user", "type": "address"}], "outputs": [{"name": "", "type": "uint256"}]},
]

# --------------------------------------------------------------------------
# Terminal UI (stdlib only — no extra dependencies)
#
# Fancy mode (banner, colors, spinners, live countdown, status panel) is on
# automatically when stdout is a terminal. Force it with UI=fancy, or turn
# it off with UI=plain (plain mode = clean log lines, best for systemd).
# --------------------------------------------------------------------------

_ui_mode = os.getenv("UI", "auto").strip().lower()
FANCY = (_ui_mode == "fancy") or (_ui_mode == "auto" and sys.stdout.isatty())

RESET = "\033[0m"; BOLD = "\033[1m"; DIM = "\033[2m"
RED = "\033[31m"; GREEN = "\033[32m"; YELLOW = "\033[33m"
BLUE = "\033[34m"; MAGENTA = "\033[35m"; CYAN = "\033[36m"

def paint(text: str, *codes: str) -> str:
    if not FANCY:
        return text
    return "".join(codes) + text + RESET

def fmt_num(x) -> str:
    if x is None:
        return "—"
    try:
        f = float(x)
    except (TypeError, ValueError):
        return str(x)
    s = f"{f:,.2f}"
    return s.rstrip("0").rstrip(".") if "." in s else s

def progress_bar(pct: float, width: int = 18) -> str:
    filled = int(round(max(0.0, min(100.0, pct)) / 100 * width))
    return "█" * filled + "░" * (width - filled)

class Spinner:
    """Braille spinner on stderr (same stream as the logs) while work runs."""
    FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

    def __init__(self, label: str):
        self.label = label
        self._stop = threading.Event()
        self._thread = None

    def _spin(self) -> None:
        i = 0
        while not self._stop.is_set():
            frame = self.FRAMES[i % len(self.FRAMES)]
            sys.stderr.write(f"\r{paint(frame, CYAN, BOLD)} {self.label}… ")
            sys.stderr.flush()
            i += 1
            self._stop.wait(0.08)
        sys.stderr.write("\r" + " " * (len(self.label) + 8) + "\r")
        sys.stderr.flush()

    def __enter__(self):
        if FANCY:
            self._thread = threading.Thread(target=self._spin, daemon=True)
            self._thread.start()
        return self

    def __exit__(self, *exc):
        if self._thread:
            self._stop.set()
            self._thread.join(timeout=1)
        return False

def live_countdown(seconds: int) -> None:
    """Animated wait between cycles: spinner + countdown + draining bar."""
    if not FANCY:
        time.sleep(seconds)
        return
    words = ["watching the curve", "holding positions", "scanning for liftoffs",
             "guarding the wallet", "waiting for the next block party"]
    start = time.monotonic()
    i = 0
    try:
        while True:
            elapsed = time.monotonic() - start
            remaining = seconds - elapsed
            if remaining <= 0:
                break
            mm, ss = divmod(int(remaining) + 1, 60)
            pct = elapsed / seconds * 100
            frame = Spinner.FRAMES[i % len(Spinner.FRAMES)]
            word = words[(i // 40) % len(words)]
            line = (f"\r{paint(frame, CYAN, BOLD)} {paint('next cycle in', DIM)} "
                    f"{paint(f'{mm:02d}:{ss:02d}', BOLD)}  "
                    f"{paint(progress_bar(pct, 24), CYAN)} {pct:4.1f}%  "
                    f"{paint('· ' + word, DIM)}")
            sys.stdout.write(line + "   ")
            sys.stdout.flush()
            i += 1
            time.sleep(0.1)
    finally:
        sys.stdout.write("\r" + " " * 110 + "\r")
        sys.stdout.flush()

import re as _re
_ANSI_RE = _re.compile(r"\x1b\[[0-9;]*m")
_WIDE = set("🥔🎯🏆🛒💰📊🔄●✅⏭🚀")

def _vislen(s: str) -> int:
    w = 0
    for ch in _ANSI_RE.sub("", s):
        w += 2 if ord(ch) >= 0x1F000 or ch in _WIDE else 1
    return w

def _box(rows: list, width: int = 62) -> None:
    print(paint("╭" + "─" * width + "╮", CYAN), flush=True)
    for r in rows:
        pad = max(0, width - _vislen(r))
        print(paint("│", CYAN) + r + " " * pad + paint("│", CYAN), flush=True)
    print(paint("╰" + "─" * width + "╯", CYAN), flush=True)

def print_banner(bot) -> None:
    if not FANCY:
        return
    mode = paint("● LIVE — real transactions", GREEN, BOLD) if not DRY_RUN \
        else paint("● DRY_RUN — simulation only", YELLOW, BOLD)
    short = f"{bot.address[:6]}…{bot.address[-4:]}"
    _box([
        "",
        paint("  🥔  TZERO BOT", BOLD, CYAN) + paint("  ·  Season 0 Points Runner", DIM),
        paint("  ────────────────────────────────────────────────", DIM),
        f"  Wallet   {paint(short, BOLD)}",
        "  Chain    Base Sepolia (84532)",
        f"  Mode     {mode}",
        f"  Cycle    ~{LOOP_INTERVAL_SEC}s ± {JITTER_SEC}s · buys ≤ {MAX_BUYS_PER_DAY}/day · stake $T0 {'on' if STAKE_T0 else 'off'}",
        "",
    ])

def print_panel(bot, summary: dict) -> None:
    if not FANCY or not summary:
        return
    pts = summary.get("points") or {}
    bal = summary.get("bal")
    total = fmt_num(pts.get("total")) if pts else "—"
    parts = []
    if pts:
        parts = [f"Fuel {fmt_num(pts.get('fuel'))}", f"Crew {fmt_num(pts.get('crew'))}",
                 f"Liftoff {fmt_num(pts.get('liftoff'))}", f"Staking {fmt_num(pts.get('staking'))}"]
    rank = pts.get("rank")
    elig = pts.get("eligible")
    _box([
        paint(f"  📊 STATUS · cycle #{bot.cycle_no} · {datetime.now():%H:%M:%S}", BOLD),
        f"  💰 Balance   {paint(f'{bal:.6f} ETH' if bal is not None else '—', BOLD)}",
        f"  🎯 Points    {paint(total, BOLD, CYAN)}" + (paint("  (" + " · ".join(parts) + ")", DIM) if parts else ""),
        f"  🏆 Rank      {('#' + str(rank)) if rank else '—'}  ·  " +
        (paint("✅ eligible", GREEN) if elig else paint("not eligible yet", YELLOW)),
        f"  🛒 Buys      {day_count(bot.state, 'buys')}/{MAX_BUYS_PER_DAY} today  ·  "
        f"{paint(str(len(bot.state['buys'])) + ' missions backed in total', DIM)}",
    ])

# --------------------------------------------------------------------------
# Logging / state
# --------------------------------------------------------------------------

class _ColorFmt(logging.Formatter):
    LEVEL_COLORS = {logging.DEBUG: DIM, logging.INFO: CYAN,
                    logging.WARNING: YELLOW, logging.ERROR: RED,
                    logging.CRITICAL: RED + BOLD}

    def format(self, record):
        if FANCY:
            orig = record.levelname
            record.levelname = paint(f"{orig:<7}", self.LEVEL_COLORS.get(record.levelno, ""))
            try:
                out = super().format(record)
            finally:
                record.levelname = orig
            return out.replace("[DRY_RUN]", paint("[DRY_RUN]", MAGENTA, BOLD))
        return super().format(record)

_handler = logging.StreamHandler()
_handler.setFormatter(_ColorFmt(
    fmt="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S%z"))
logging.basicConfig(level=logging.INFO, handlers=[_handler])
log = logging.getLogger("tzero-bot")

def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            log.warning("state file unreadable, starting fresh")
    return {"buys": [], "launches": [], "days": {}}

def save_state(state: dict) -> None:
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=1)
    os.replace(tmp, STATE_FILE)

def today_key() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")

def day_count(state: dict, kind: str) -> int:
    return state["days"].get(today_key(), {}).get(kind, 0)

def bump_day(state: dict, kind: str) -> None:
    d = state["days"].setdefault(today_key(), {})
    d[kind] = d.get(kind, 0) + 1

# --------------------------------------------------------------------------
# API helpers (read-only; the site’s own public endpoints)
# --------------------------------------------------------------------------

def api_get(path: str):
    r = requests.get(API_BASE + path, timeout=30,
                     headers={"User-Agent": "tzero-helper/1.0"})
    r.raise_for_status()
    return r.json()

def fetch_closest() -> list:
    try:
        home = api_get("/api/home")
        return home.get("closest") or []
    except Exception as e:  # noqa: BLE001
        log.warning("fetch /api/home failed: %s", e)
        return []

def fetch_points(address: str) -> dict | None:
    try:
        return api_get(f"/api/points/{address.lower()}")
    except Exception as e:  # noqa: BLE001
        log.warning("fetch points failed: %s", e)
        return None

# --------------------------------------------------------------------------
# Chain helpers
# --------------------------------------------------------------------------

class Bot:
    def __init__(self):
        if not PRIVATE_KEY:
            log.error("PRIVATE_KEY is empty. Copy .env.example to .env and fill it in.")
            sys.exit(1)
        self.w3 = Web3(Web3.HTTPProvider(RPC_URL, request_kwargs={"timeout": 30}))
        if not self.w3.is_connected():
            log.error("cannot reach RPC %s", RPC_URL)
            sys.exit(1)
        chain_id = self.w3.eth.chain_id
        if chain_id != EXPECTED_CHAIN_ID:
            log.error("wrong chain: got %s, expected %s (Base Sepolia). Refusing to run.",
                      chain_id, EXPECTED_CHAIN_ID)
            sys.exit(1)
        self.acct = self.w3.eth.account.from_key(PRIVATE_KEY)
        self.address = self.acct.address
        self.launchpad = self.w3.eth.contract(
            address=Web3.to_checksum_address(LAUNCHPAD), abi=LAUNCHPAD_ABI)
        self.staking = self.w3.eth.contract(
            address=Web3.to_checksum_address(STAKING), abi=STAKING_ABI)
        self.t0 = self.w3.eth.contract(
            address=Web3.to_checksum_address(T0_TOKEN), abi=ERC20_ABI)
        self.state = load_state()
        self.cycle_no = 0
        log.info("wallet %s | chain %s | DRY_RUN=%s", self.address, chain_id, DRY_RUN)

    # -- tx plumbing -------------------------------------------------------
    def send(self, fn, value_wei: int = 0, label: str = "tx") -> str | None:
        """Build, (optionally) sign+send a contract call. Returns tx hash or None."""
        tx = fn.build_transaction({
            "from": self.address,
            "nonce": self.w3.eth.get_transaction_count(self.address, "pending"),
            "value": value_wei,
            "chainId": EXPECTED_CHAIN_ID,
            "gas": 400_000,  # placeholder so build_transaction doesn't auto-estimate
        })
        # Refine with a real estimate when possible; add a 20% buffer.
        try:
            gas = self.w3.eth.estimate_gas(tx)
            tx["gas"] = int(gas * 1.2)
        except Exception as e:  # noqa: BLE001
            log.warning("%s: gas estimate failed (%s); keeping 400k", label, e)
        if DRY_RUN:
            log.info("[DRY_RUN] would send %s: to=%s value=%.6f ETH gas=%s data=%s...",
                     label, tx.get("to"), value_wei / 1e18, tx["gas"],
                     (tx.get("data") or "")[:20])
            return None
        signed = self.acct.sign_transaction(tx)
        with Spinner(f"broadcasting {label} & waiting for confirmation"):
            h = self.w3.eth.send_raw_transaction(signed.raw_transaction)
            txh = h.hex()
            receipt = self.w3.eth.wait_for_transaction_receipt(h, timeout=180)
        log.info("%s sent: %s", label, txh)
        if receipt.status != 1:
            log.error("%s REVERTED on-chain: %s", label, txh)
            return None
        log.info("%s confirmed in block %s", label, receipt.blockNumber)
        return txh

    # -- strategy steps ----------------------------------------------------
    def balance_eth(self) -> float:
        return self.w3.eth.get_balance(self.address) / 1e18

    def pick_candidate(self, closest: list) -> dict | None:
        bought = {b["token"].lower() for b in self.state["buys"]}
        for m in closest:
            if m.get("graduated"):
                continue
            if (m.get("progressBps") or 0) < MIN_PROGRESS_BPS:
                continue
            holders = m.get("holders")
            if holders is not None and holders < MIN_HOLDERS:
                continue  # API sometimes reports holders=null; only filter on a real number
            if m["creator"].lower() == self.address.lower():
                continue  # never back your own mission: earns no Fuel/Crew
            if m["token"].lower() in bought:
                continue
            # Re-verify progress on-chain; the API can lag.
            try:
                bps = self.launchpad.functions.progressBps(
                    Web3.to_checksum_address(m["token"])).call()
            except ContractLogicError:
                continue
            if bps < MIN_PROGRESS_BPS or bps >= 10_000:
                continue
            return m
        return None

    def do_buy(self, m: dict) -> None:
        token = Web3.to_checksum_address(m["token"])
        amount_wei = self.w3.to_wei(BUY_AMOUNT_ETH, "ether")
        quote = self.launchpad.functions.quoteBuy(token, amount_wei).call()[0]
        if quote <= 0:
            log.info("quoteBuy returned 0 for %s (graduated?) — skipping", m["symbol"])
            return
        min_out = quote * (10_000 - SLIPPAGE_BPS) // 10_000
        pct = (m.get("progressBps") or 0) / 100
        log.info("backing %s (%s) %s %.2f%% with %.4f ETH, quote %s tokens",
                 m["name"], m["symbol"], progress_bar(pct), pct,
                 BUY_AMOUNT_ETH, quote / 1e18)
        txh = self.send(
            self.launchpad.functions.buy(token, min_out),
            value_wei=amount_wei, label=f"buy {m['symbol']}")
        if DRY_RUN:
            self.state["buys"].append({
                "token": m["token"], "symbol": m["symbol"],
                "amountEth": BUY_AMOUNT_ETH, "ts": int(time.time()),
                "txHash": None, "dryRun": True})
            bump_day(self.state, "buys")
            save_state(self.state)
        elif txh:
            self.state["buys"].append({
                "token": m["token"], "symbol": m["symbol"],
                "amountEth": BUY_AMOUNT_ETH, "ts": int(time.time()),
                "txHash": txh, "dryRun": False})
            bump_day(self.state, "buys")
            save_state(self.state)

    def do_stake_and_claim(self) -> None:
        if STAKE_T0:
            bal = self.t0.functions.balanceOf(self.address).call()
            if bal > 0:
                allow = self.t0.functions.allowance(
                    self.address, Web3.to_checksum_address(STAKING)).call()
                if allow < bal:
                    log.info("approving staking contract for %s T0", bal / 1e18)
                    self.send(self.t0.functions.approve(
                        Web3.to_checksum_address(STAKING), bal), label="approve T0")
                log.info("staking %s T0", bal / 1e18)
                self.send(self.staking.functions.stake(bal), label="stake T0")
        if CLAIM_REWARDS:
            try:
                pending = self.staking.functions.pending(self.address).call()
            except ContractLogicError:
                pending = 0
            if pending > 0:
                log.info("claiming %s ETH staking rewards", pending / 1e18)
                self.send(self.staking.functions.claim(), label="claim rewards")

    def do_launch(self) -> None:
        if not (LAUNCH_ENABLED and LAUNCH_NAME and LAUNCH_SYMBOL):
            return
        if day_count(self.state, "launches") >= MAX_LAUNCH_PER_DAY:
            return
        fee = self.launchpad.functions.CREATION_FEE().call()
        log.info("launching own mission %s (%s), creation fee read live: %s ETH. "
                 "NOTE: Liftoff points need >=5 OTHER wallets x >=0.01 ETH held 60 min; "
                 "announce the mission so real people can back it.",
                 LAUNCH_NAME, LAUNCH_SYMBOL, fee / 1e18)
        txh = self.send(
            self.launchpad.functions.create(LAUNCH_NAME, LAUNCH_SYMBOL, LAUNCH_URI),
            value_wei=fee, label=f"create {LAUNCH_SYMBOL}")
        if DRY_RUN or txh:
            self.state["launches"].append({
                "name": LAUNCH_NAME, "symbol": LAUNCH_SYMBOL,
                "ts": int(time.time()), "txHash": txh, "dryRun": DRY_RUN})
            bump_day(self.state, "launches")
            save_state(self.state)

    # -- main loop -----------------------------------------------------------
    def cycle(self) -> dict:
        self.cycle_no += 1
        if FANCY:
            print(paint(f"\n─── 🔄 cycle #{self.cycle_no} · {datetime.now():%H:%M:%S} "
                        f"────────────────────────────────", DIM))
        bal = self.balance_eth()
        log.info("balance %.6f ETH | buys today %s/%s",
                 bal, day_count(self.state, "buys"), MAX_BUYS_PER_DAY)
        if bal < MIN_BALANCE_ETH:
            log.warning("balance below %.4f ETH — top up from a Base Sepolia faucet "
                        "(manual sign-in required, the bot cannot claim for you) "
                        "and keep at least %.4f ETH as gas reserve.",
                        MIN_BALANCE_ETH, GAS_RESERVE_ETH)

        if (bal - GAS_RESERVE_ETH) >= BUY_AMOUNT_ETH and \
                day_count(self.state, "buys") < MAX_BUYS_PER_DAY:
            with Spinner("scanning missions closest to liftoff"):
                candidate = self.pick_candidate(fetch_closest())
            if candidate:
                self.do_buy(candidate)
            else:
                log.info("no mission at >= %s bps with >= %s holders right now",
                         MIN_PROGRESS_BPS, MIN_HOLDERS)

        self.do_stake_and_claim()
        self.do_launch()

        pts = fetch_points(self.address)
        if pts and not FANCY:
            log.info("points: total=%s fuel=%s crew=%s liftoff=%s staking=%s "
                     "eligible=%s rank=%s",
                     pts.get("total"), pts.get("fuel"), pts.get("crew"),
                     pts.get("liftoff"), pts.get("staking"),
                     pts.get("eligible"), pts.get("rank"))
        return {"bal": bal, "points": pts}

    def run(self) -> None:
        print_banner(self)
        log.info("starting loop: every ~%ss (+/- %ss jitter)", LOOP_INTERVAL_SEC, JITTER_SEC)
        while True:
            summary = {}
            try:
                summary = self.cycle() or {}
            except Exception as e:  # noqa: BLE001 — keep the loop alive on a VPS
                log.exception("cycle failed: %s", e)
            print_panel(self, summary)
            live_countdown(max(60, LOOP_INTERVAL_SEC + random.randint(-JITTER_SEC, JITTER_SEC)))


if __name__ == "__main__":
    try:
        Bot().run()
    except KeyboardInterrupt:
        print("\n👋 bot stopped. state.json is saved — safe to resume anytime.")
        sys.exit(0)
