# Tzero Season 0 Bot — Base Sepolia Testnet

Automates **one** dedicated wallet's ordinary participation in
[Tzero](https://testnet.tzer0.fun) Season 0 (the launchpad on Zero,
currently running on Base Sepolia, chain ID 84532):

1. **Back near-liftoff missions** from the site's own `/api/home`
   "closest" list — ≥ 0.01 ETH on the bonding curve, progress ≥ 90%,
   ≥ 3 holders, never your own mission, on-chain `progressBps` and
   `quoteBuy` re-verified before every buy.
   → Fuel points (1 pt per 0.01 ETH net buy, counted after a 60-minute
   hold) and the +50% Crew bonus if the mission lifts off after you
   bought it.
2. **Never sells.** Sells reduce net Fuel, so selling is not implemented.
3. **Stakes $T0 and claims ETH rewards** (Staking points accrue from
   rewards earned).
4. **Optionally launches your own mission** (`LAUNCH_ENABLED=true`).
   Liftoff points (2,500/mission) additionally require ≥ 5 *other*
   wallets backing it with ≥ 0.01 ETH each held 60 min — the bot does
   not and cannot fake that; share the mission so real people back it.
5. **Logs your points** from `/api/points/<address>` every cycle.
   Eligibility needs points on ≥ 2 different days **and** from ≥ 2
   different categories, so run it across multiple days.

Eligibility, caps and rules are the project's; they can change or the
season can stop at any time. Points are not a promise of tokens.

## ⚠️ Read this first

- **Anti-bot rule:** Tzero states that anyone caught running bots,
  farms or Sybil setups may earn nothing and be blocked from mainnet.
  This bot is a single-wallet participation helper with a human-scale
  rhythm (≤ 3 buys/day, 15-minute cycles with jitter). Running it is
  your decision and your risk.
- **Testnet funds only.** Base Sepolia ETH has no value. Faucets
  (Superchain / Coinbase / Alchemy) require *your* GitHub/Coinbase
  sign-in, so the bot cannot claim for you — it warns when the balance
  runs low instead.
- **Use a dedicated wallet**, never your main wallet.
- **Creation-fee note:** the website UI states a 0.01 ETH creation fee,
  but the Launchpad contract's `CREATION_FEE()` reads **0.0001 ETH**
  on-chain (checked 2026-11-10). The bot always reads the fee live from
  the contract and never hardcodes it.

## Install on a VPS (Ubuntu/Debian)

```bash
sudo adduser --disabled-password tzero        # or reuse an existing user
sudo mkdir -p /opt/tzero-bot && sudo chown tzero:tzero /opt/tzero-bot
# copy tzero_bot.py, requirements.txt, .env.example into /opt/tzero-bot
cd /opt/tzero-bot
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
nano .env            # set PRIVATE_KEY; keep DRY_RUN=true for the first run
chmod 600 .env
```

### 1. Dry-run first (no transactions are sent)

```bash
.venv/bin/python tzero_bot.py
```

Watch the logs: it should print the wallet, balance, the `[DRY_RUN]
would send buy ...` lines and your points. Ctrl-C to stop.

### 2. Go live

Set `DRY_RUN=false` in `.env`, fund the wallet with Base Sepolia ETH
(Superchain Faucet gives 0.01 ETH/day), then run it as a service:

```bash
sudo cp tzero-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now tzero-bot
journalctl -u tzero-bot -f
```

## Configuration (`.env`)

| Key | Default | Meaning |
|---|---|---|
| `PRIVATE_KEY` | — | Dedicated testnet wallet key (env only, never logged) |
| `DRY_RUN` | `true` | Plan + log transactions, send none |
| `BUY_AMOUNT_ETH` | `0.01` | Per-backing amount (Fuel counts per 0.01 ETH) |
| `MIN_PROGRESS_BPS` | `9000` | Only back missions ≥ 90% to liftoff |
| `MIN_HOLDERS` | `3` | Skip near-empty missions |
| `MAX_BUYS_PER_DAY` | `3` | Daily buy cap |
| `SLIPPAGE_BPS` | `1000` | min-out protection vs the on-chain quote |
| `GAS_RESERVE_ETH` | `0.002` | Never spend the last gas money |
| `STAKE_T0` / `CLAIM_REWARDS` | `true` | Stake $T0 / claim ETH rewards |
| `LAUNCH_ENABLED` | `false` | Launch your own mission (≤ 2/day, project cap) |
| `LOOP_INTERVAL_SEC` | `900` | Cycle length, ± `JITTER_SEC` |

State (buys, launches, daily counters) persists in `state.json` next to
the script, so restarts don't double-buy the same mission.

## Contracts (Base Sepolia, verified on Sourcify)

| Contract | Address |
|---|---|
| Launchpad | `0xbce48917C41BB45Ad15f859E4Bf0e4EEa74bAd5c` |
| Mission Factory | `0xf9CE1F9D20d5d42515b27fE2A5380B89A0Be927E` |
| TzeroSwap Router | `0x21BbD201c69d0091707d0367D3AD6875cEc150e1` |
| Fee Splitter | `0x1C69a90c69fdb2F2E459fCA23cA6B22ACD707B3b` |
| $T0 Staking | `0x221De22612E65093Fc43164dae84fa6Bd79E6239` |
| $T0 token | `0x15522727F10Ea666Ebb4fD4c23B394580F10FED4` |

Full ABIs and the REA static-analysis evidence live in
`../rea-evidence/` (`ANALYSIS.md` there summarises the findings).
