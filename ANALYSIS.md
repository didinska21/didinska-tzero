# Tzero (testnet.tzer0.fun) — Analysis

Date: 2026-11-10 · Analyst: Potato (REA + direct verification)
Target: Tzero Season 0 Pre-flight Points — launchpad on Zero (LayerZero L1),
currently live on **Base Sepolia** until Zero's own testnet opens.

> ⚠️ Domain: the correct domain is **testnet.tzer0.fun** (with a zero).
> `tzero.fun` is a parked domain. Only ever sign on testnet.tzer0.fun.

## 1. What it is

Bonding-curve launchpad (pump.fun model) for Zero. Every mission token:
fixed supply 1,000,000,000, no owner/mint/pause/blacklist/tax functions in
the token contract. Tokens sell on the curve ("Fuel"); a token that fills
its curve "lifts off" to TzeroSwap (a Uniswap V2 port) and its LP is sent
to the burn address `0x0000…dEaD` — locked forever.

Season 0 distributes **5% of the $T0 supply** pro-rata to eligible
wallets (max 0.1% of supply per wallet).

## 2. Season 0 points — official rules (docs/points, corrected)

The X thread's figures were partly wrong; the site's own docs + live API
below are authoritative.

| Category | Rule | Cap |
|---|---|---|
| Fuel | 1 pt per 0.01 ETH **net** buy (buys − sells), counted 60 min after the buy, position held ≥ 60 min. Sells earn nothing. | 5,000 / wallet |
| Crew | +50% of a mission's Fuel points if that mission lifts off **after** you bought it (one bonus per mission) | 3,750 / wallet |
| Liftoff | 2,500 pts per own mission that lifts off, **only** if ≥ 5 *other* wallets backed it with ≥ 0.01 ETH each, held 60 min. Solo launches earn nothing. | max 2 missions/day count |
| Staking | 2 pts per 0.01 ETH of rewards your $T0 stake earns per 60-min window | — |
| Signal | 250 pts/week — **not yet active** (`/api/points/stats`: `signal.open=false`) | — |

- **Eligibility:** points on ≥ 2 different days **and** from ≥ 2 different
  categories, otherwise the wallet does not count.
- **Anti-sybil (project's own words):** wallets caught running bots,
  farms or Sybil setups may earn nothing and be blocked from mainnet.
  Rules can change; Season 0 can stop; points are not a promise.
- Leaderboard snapshot 2026-11-10 ~14:47 WIB: 4,052 ranked wallets,
  #1 at 17,551 pts — of which **9,500 Liftoff**. Liftoff dominates the
  top; Fuel is capped and slow by design.

## 3. REA static analysis of the web app (bundle JS)

Method: REA `analyze-javascript-application` over the app's 28 Next.js
chunks (1.24 MB), downloaded from testnet.tzer0.fun.

- Run 1 was cancelled by a 280 s timeout at file 23/28; REA still emitted
  a partial observation — **Evidence ID
  `ev_0842d9d54f52e95e0c4ffc0246831819f5b97d6867601f252edb4b53ec024a3c`**,
  artifact digest `dde445fdc493ceaec28f865fc0123493b379782ea31eec6489182544e42f9025`.
- Partial statistics: 28 relevant files, 22 parsed, 6 skipped,
  299,371 AST nodes visited, **491 findings, 0 parse failures,
  0 integrity contradictions**.
- Route inventory (`jsri:c3f0f9a97cd01205500da782b46d98d8dc5445283290d1f70f29629ef9c95438`):
  19 routes. App routes match the observed UI exactly: `/`, `/creator`,
  `/docs`, `/launch`, `/points`, `/t0`. The remaining `/docs/contract/*`
  and `/docs/actions/*` routes are string constants from the bundled
  viem library's documentation — library noise, not app pages.
- Limitations (REA's own): semantic-graph budget exceeded on several
  large chunks (static/module facts retained); 2 sources marked
  interrupted by the cancellation. A full re-run with a 1,500 s budget
  is in flight; this file will be updated with its Evidence ID.
- Static analysis observes code, not execution (REA limitation): all
  load-bearing claims below were therefore re-verified live (API +
  on-chain) before being used in the bot.

## 4. Contracts (Base Sepolia, chain 84532, Sourcify-verified)

| Contract | Address |
|---|---|
| Launchpad | `0xbce48917C41BB45Ad15f859E4Bf0e4EEa74bAd5c` |
| Mission Factory | `0xf9CE1F9D20d5d42515b27fE2A5380B89A0Be927E` |
| TzeroSwap Router | `0x21BbD201c69d0091707d0367D3AD6875cEc150e1` |
| Fee Splitter | `0x1C69a90c69fdb2F2E459fCA23cA6B22ACD707B3b` |
| $T0 Staking | `0x221De22612E65093Fc43164dae84fa6Bd79E6239` |
| $T0 (Mission 000) | `0x15522727F10Ea666Ebb4fD4c23B394580F10FED4` |
| WETH (Base Sepolia) | `0x4200000000000000000000000000000000000006` |

Full ABIs in `abis/`. Key surface:
- Launchpad: `buy(token, minTokensOut)` payable, `sell(...)`,
  `create(name, symbol, uri)` payable, `quoteBuy` → **(tokensOut, fee,
  uint256)** (3 outputs — the bot's first draft assumed 1; fixed),
  `quoteSell` → 2 outputs, `progressBps(token)`, `curve(token)`.
- Router: `swapExactNativeForTokens` / `swapExactTokensForNative`
  (post-liftoff trading), quotes both ways.
- Staking: `stake`, `unstake`, `claim`, `pending`, `staked`,
  `notifyReward` payable.
- Splitter: `claimCreatorFees`, `creatorFees`.

On-chain constants read via RPC (2026-11-10):
`CREATION_FEE` = **0.0001 ETH** · `VIRTUAL_NATIVE` = 0.1 ETH ·
`VIRTUAL_TOKEN` = 1,073,000,000 · `CURVE_SUPPLY` = 793,100,000 ·
`LP_SUPPLY` = 206,900,000 (sum = 1 B ✓) · `TRADE_FEE_BPS` = 100 (1%) ·
`GRADUATION_FEE_BPS` = 300 (3%) · `tokenCount` = 5,340.

> ⚠️ **Discrepancy:** the /launch page states a 0.01 ETH creation fee;
> the contract reads 0.0001 ETH. The bot reads the fee live from the
> contract and never hardcodes it.

Fees (docs): 1% per curve trade; at liftoff the fee split is Creator
50% / Fee pool 25% / $T0 stakers 20% / burn 5%. Staking rewards are
paid in ETH, no lockup.

## 5. Public API (discovered in bundle, probed live, all GET read-only)

`/api/stats` · `/api/home` (stats + `king` + **`closest`** — missions
nearest liftoff; the bot's candidate source) · `/api/feed` ·
`/api/missions` (list; `sort` param appears ignored — do not rely on
it) · `/api/missions/{id}` (+ `/trades`, `/candles?interval=`) ·
`/api/search?q=` · `/api/metadata?token=` · `/api/points/stats` ·
`/api/points/leaderboard` · `/api/points/{address}` (per-wallet points,
eligibility, per-action attribution incl. `too-few-buyers` liftoff
rejections) · `/api/points/signal` · `/api/rpc`.

Quirk: `/api/home`'s `closest` sometimes reports `holders: null` — the
bot only filters on holders when it is a real number.

## 6. Worked example — $7MARS (the mission in the X post)

`0xfe990e374fc0b2250549c115f2e8eef00db52c4f` — already **graduated**
(on-chain `progressBps` = 10,000; `quoteBuy` = 0). Post-liftoff buys go
through the TzeroSwap Router, not the Launchpad, and earn no Fuel/Crew.
As a Season 0 target it is already spent.

## 7. Faucets

Base Sepolia ETH: Superchain Faucet (GitHub/onchain-identity sign-in),
Coinbase Faucet (Coinbase sign-in), Alchemy Faucet (needs 0.001 ETH on
mainnet). All require *human* identity sign-in — a headless bot cannot
honestly claim them. The bot checks balance and warns instead.

## 8. Bot design decisions (see ../bot/)

- **One dedicated wallet**, human-scale rhythm (≤ 3 buys/day, ~15-min
  cycles with jitter) — a participation helper, **not** a multi-wallet
  farm. The project's anti-bot rule is quoted in the bot's README;
  running it remains Bos's decision.
- Backs only *other people's* missions at ≥ 90% progress (Crew chance),
  ≥ 0.01 ETH, `progressBps` + `quoteBuy` re-verified on-chain per buy,
  min-out slippage protection. Never sells (sells cut net Fuel).
- Stakes $T0 + claims rewards; optional own-mission launch (fee read
  live). `DRY_RUN=true` by default; refuses any chain ≠ 84532.
- Tested 2026-11-10: syntax OK; dry-run cycle OK (chain guard, faucet
  warning, points logging); simulated-funds cycle picked ROCBE12 at
  91.19%, decoded its quote (10,268,038 tokens / 0.01 ETH) and built a
  correct `buy` transaction without sending anything.
