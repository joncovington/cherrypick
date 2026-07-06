"""Tastytrade CLI for EarningsFlyAgent.

Not yet implemented. Intended commands (see CLAUDE.md's Tool Reference):
  get_quote --symbol X
  get_option_chain --symbol X --expiration DATE --include_greeks
  get_account_info
  execute_trade --order '<JSON>' [--live]
"""

import argparse
import json
import sys


def cmd_get_quote(args) -> dict:
    raise NotImplementedError


def cmd_get_option_chain(args) -> dict:
    raise NotImplementedError


def cmd_get_account_info(args) -> dict:
    raise NotImplementedError


def cmd_execute_trade(args) -> dict:
    raise NotImplementedError


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    p_quote = sub.add_parser("get_quote")
    p_quote.add_argument("--symbol", required=True)

    p_chain = sub.add_parser("get_option_chain")
    p_chain.add_argument("--symbol", required=True)
    p_chain.add_argument("--expiration")
    p_chain.add_argument("--include_greeks", action="store_true")

    sub.add_parser("get_account_info")

    p_exec = sub.add_parser("execute_trade")
    p_exec.add_argument("--order", required=True)
    p_exec.add_argument("--live", action="store_true")

    args = parser.parse_args()
    dispatch = {
        "get_quote": cmd_get_quote,
        "get_option_chain": cmd_get_option_chain,
        "get_account_info": cmd_get_account_info,
        "execute_trade": cmd_execute_trade,
    }
    result = dispatch[args.command](args)
    json.dump(result, sys.stdout)


if __name__ == "__main__":
    main()
