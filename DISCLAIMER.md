# Disclaimer

**Read this before installing or running cherrypick. By using it you accept everything below.**

## Experimental software, for educational purposes only

cherrypick is an **experimental prototype**, written to learn about and research options strategies.
It is not a finished product, a trading service, or a recommendation of any strategy. Parts of it are
marked **EXPERIMENTAL** (calendars, PMCC-99, curve and the manual desk) and are less tested still.

It is provided **"as is", without warranty of any kind**, under the MIT License ([LICENSE](LICENSE)).
The authors are not liable for any loss or damage arising from its use.

## Not financial advice

Nothing in this software, its documentation, its reports or its AI-written narratives is investment,
financial, legal or tax advice, or a recommendation to buy or sell any security. The authors are not
registered investment advisers, broker-dealers or commodity trading advisors. Make your own decisions
and, where you need advice, get it from a qualified professional.

## Options trading carries substantial risk of loss

Options involve risk and are not suitable for all investors. You can lose money quickly, and some
positions can lose more than the premium involved. Same-day (0DTE) options move fast enough that a
position can go from a profit to its maximum loss in minutes. Before trading options, read the Options
Clearing Corporation's [*Characteristics and Risks of Standardized Options*](https://www.theocc.com/company-information/documents-and-archives/options-disclosure-document),
which your broker is required to give you.

## Live trading places real, irreversible orders

The live paths — MEIC, flies, BWB and earnings each have a live loop, and the manual desk places
orders by hand — send **real orders to a real brokerage account with real money**. They are **off by
default** and each needs its own deliberate switch. If you turn one on:

- **You are solely responsible for every order** it places, every position it holds, and every loss.
- **Software fails.** Bugs, stale or missing market data, a stalled data feed, network or broker
  outages, rejected or partial fills, early assignment and settlement surprises can all cause losses
  the software does not prevent. A data feed once stalled silently for 34 hours here and looked exactly
  like a quiet market.
- **The safety limits are not guarantees.** Buying-power caps, loss limits, the halt flag and the
  confirmation steps reduce accidents; they do not make trading safe. Anything running as your user
  account can change the configuration that sets them.
- **Watch it.** Never leave a live loop running unattended without a way to stop it.

## Paper and simulated results are not actual trading

Most of cherrypick runs in paper mode: simulated fills against market quotes, recorded in a local
ledger. In the words of CFTC Rule 4.41's standard disclosure: hypothetical or simulated performance
results have certain limitations. Unlike an actual performance record, simulated results do not
represent actual trading. Since the trades have not been executed, the results may have under- or
over-compensated for the impact, if any, of certain market factors, such as lack of liquidity.
Simulated trading programs in general are also subject to the fact that they are designed with the
benefit of hindsight. No representation is being made that any account will or is likely to achieve
profits or losses similar to those shown.

Paper fills here are modelled (at the mid, or with a modelled slippage concession), fees are modelled,
and early assignment is measured rather than modelled, so a paper result is an upper bound on what the
same trades would have done live.

## The console is not a secure web application

The console (`http://127.0.0.1:5070`) and the settings editor (`run.py settings`, port 8804) have no
login and are not hardened against attack. They listen only on `127.0.0.1`, so only this computer
can open them, and they must stay that way: **never expose either to your local network or the
internet** — no port forwarding, reverse proxy, tunnel or remote-access sharing. Anyone who can
reach the console can read your positions, account figures and results, and use its Config page,
including the live-trading halt switch.

## AI features

The optional advisor and narratives send data to an AI model (Claude Code) and act only on paper
books. Model output can be wrong, incomplete or confidently mistaken. It is never advice.

## Third parties

cherrypick is independent. It is **not affiliated with, endorsed by or supported by** tastytrade,
the Options Clearing Corporation, Anthropic, DoltHub or any data vendor. Trademarks belong to their
owners. You are responsible for complying with the terms of every service you connect it to,
including your broker's and any data provider's, and with the rules that apply to your trading
(for example pattern-day-trader rules and taxes).
