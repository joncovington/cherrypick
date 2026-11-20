"""The Builder's words: how each choice the form offers reads as a sentence, so the page lays its
controls inside the sentence and spells no rule of its own. Pure data.

Every choice (an entry kind, a trigger form, a signal and its firing rules, an indicator, a filter
gate, an exit, a strike rule and its match modes, an expiry rule, a size rule and its progression, a
limit, a strategy) has an entry here keyed as the engine keys it:

    label     its name in a list ("Take profit")
    group     the heading it is listed under, where the list is grouped
    about     one sentence on what it does, for a new user
    sentence  the line the form draws, with a `{key}` slot where each value's control sits
    more      {key: phrase}: the keys the sentence leaves out, each with its own phrase and slot,
              shown under "More options"
    values    {key: {value: words}}: how an enum's or a flag's value reads where its own spelling
              would not ("prior_close" reads "the previous close"; a flag's "true" and "false"); a
              key that may be null has "null" for that and "n" for a number, its slot inside, and a
              number may have words of its own ("1" reads "1 contract")
    start     {key: value}: what the form writes for a key the engine requires but `more` shows,
              visible and editable there (a signal's `seed`, which the presets write true too)

A slot names a key of that choice, dotted for a nested one (`{window.open}`). Three slots are shared
rather than keys: `{range}` is a filter's `min` and `max` together ("between 15 and 30", "at least
15", "at most 30"), `{symbol}` is the automation's underlying in a strategy's line, and `{signal}` in
a trigger's line is that signal's own sentence. A key absent from a saved automation reads as its
default where it has one (`fieldtypes`) and is left out of the line where it has none.

`tests/test_words.py` holds this to the engine's tables both ways: every choice the schema offers has
words, every slot names a key the choice takes, every key the choice takes is in its sentence or its
`more`, every required key is in the sentence, and every enum option with an underscore is worded.
"""

from __future__ import annotations

#: A choice's words.
Words = dict

RANGE = "range"
SYMBOL = "symbol"
SIGNAL = "signal"

# --------------------------------------------------------------------------- entry
ENTRY: dict[str, Words] = {
    "time": {
        "label": "At a time",
        "about": "Enters at the same minute each session the weekdays allow.",
        "sentence": "Enter at {at} ET",
        "more": {"window": "for up to {window} minutes after it"},
    },
    "orb": {
        "label": "Opening-range break",
        "about": "Enters when the price leaves the range of the session's first minutes.",
        "sentence": (
            "Enter when the price breaks {direction} the first {minutes} minutes' range, no later than "
            "{latest} ET"
        ),
        "more": {"single_shot": "{single_shot}"},
        "values": {
            "single_shot": {
                "true": "only on the minute of the break",
                "false": "on any minute after the break, until it enters",
            }
        },
    },
    "trigger": {
        "label": "On a trigger",
        "about": "Enters when a signal fires inside a window, or at a fixed time.",
        "sentence": "Enter {trigger}",
        "more": {
            "bar_minutes": "reading {bar_minutes}-minute bars",
            "volume": "weighing each bar by {volume}'s volume",
        },
    },
}

TRIGGER: dict[str, Words] = {
    "at": {
        "label": "At a fixed time",
        "about": "Enters at one minute, or the first minute it can within a few after.",
        "sentence": "at {at} ET, within {within} minutes",
    },
    "signal": {
        "label": "When a signal fires",
        "about": "Enters on the signal's first firing between two times.",
        "sentence": "when {signal}, between {window.open} and {window.close} ET, entering {within}",
        "more": {"daily": "{daily}", "developing": "{developing}"},
        "values": {
            "within": {
                "null": "any time before the window closes",
                "n": "within {within} minutes of the firing",
            },
            "daily": {"true": "on daily bars, from the session before", "false": "on intraday bars"},
            "developing": {
                "true": "on today's daily bar as it stands, read each minute",
                "false": "on completed daily bars only",
            },
        },
    },
}

#: The firing rules every signal takes (`core.signals`), each a phrase under "More options".
SIGNAL_RULES: dict[str, Words] = {
    "confirm": {"label": "Confirm", "sentence": "held for {confirm} bars in a row"},
    "reset": {"label": "Reset", "sentence": "re-arming only after it moves back {reset} past the level"},
    "first_of_day": {
        "label": "Once a day",
        "sentence": "{first_of_day}",
        "values": {"first_of_day": {"true": "at most once a session", "false": "as often as it fires"}},
    },
    "latest": {"label": "No later than", "sentence": "no later than {latest} ET"},
    "only_when": {"label": "Only when", "sentence": "only while {only_when} holds"},
}

_DIR_UD = {"up": "up", "down": "down"}
_SIDE_HL = {"high": "high", "low": "low"}
_SEED = {
    "true": "carrying its reading across sessions",
    "false": "starting afresh each session",
}

SIGNALS: dict[str, Words] = {
    "cross": {
        "label": "Crosses",
        "group": "Indicators",
        "about": "One indicator crossing above or below another, or a number.",
        "sentence": "{a} crosses {direction} {b}",
        "more": {"seed": "{seed}"},
        "values": {"seed": _SEED},
        "start": {"seed": True},
    },
    "range": {
        "label": "Enters or leaves a band",
        "group": "Indicators",
        "about": "An indicator moving into, or out of, a band between two numbers.",
        "sentence": "{a} {direction} the band {low} to {high}",
        "more": {"seed": "{seed}"},
        "values": {"seed": _SEED},
        "start": {"seed": True},
    },
    "trend": {
        "label": "Rising or falling",
        "group": "Indicators",
        "about": "An indicator higher (or lower) on each of its last few bars.",
        "sentence": "{a} is {direction} for {n} bars",
        "more": {"seed": "{seed}"},
        "values": {"seed": _SEED},
        "start": {"seed": True},
    },
    "consecutive": {
        "label": "Closes in a row",
        "group": "Price",
        "about": "A run of bars that each close up, or each close down.",
        "sentence": "the price closes {direction} {n} bars in a row",
        "more": {"seed": "{seed}"},
        "values": {"seed": _SEED, "direction": _DIR_UD},
        "start": {"seed": True},
    },
    "gap": {
        "label": "Opening gap",
        "group": "The open",
        "about": "The session opening away from the previous close.",
        "sentence": "the session gaps {direction} at least {pct}%",
        "more": {"at": "read at {at}"},
        "values": {"at": {"open": "the open", "first_bar_close": "the first bar's close"}},
    },
    "gap_fill": {
        "label": "Gap fills",
        "group": "The open",
        "about": "The price coming back across an opening gap toward the previous close.",
        "sentence": "an opening gap of at least {min_pct}% fills",
    },
    "change": {
        "label": "Moves from the open or the close",
        "group": "Price",
        "about": "The price up or down a set percent since the open or the previous close.",
        "sentence": "the price is {direction} {pct}% from {from}",
        "values": {"from": {"open": "the open", "prior_close": "the previous close"}},
    },
    "change_from": {
        "label": "Moves from a price",
        "group": "Price",
        "about": "The price a set percent past a fixed level.",
        "sentence": "the price is {direction} {pct}% from {price}",
    },
    "opening_range": {
        "label": "Breaks the opening range",
        "group": "The open",
        "about": "The price leaving the range of the session's first minutes.",
        "sentence": "the price breaks {side} the first {minutes} minutes' range",
    },
    "prior_session": {
        "label": "Breaks yesterday's high or low",
        "group": "Levels",
        "about": "The price passing the previous session's high or low.",
        "sentence": "the price breaks the previous session's {side}",
        "values": {"side": _SIDE_HL},
    },
    "new_extreme": {
        "label": "New high or low",
        "group": "Levels",
        "about": "The price making a new high or low over a lookback.",
        "sentence": "the price makes a new {side} over {lookback} bars",
        "values": {"side": _SIDE_HL},
    },
    "distance": {
        "label": "Near or far from a high or low",
        "group": "Levels",
        "about": "The price within, or beyond, a percent of a recent high or low.",
        "sentence": "the price is {direction} {pct}% of its {lookback}-bar {side}",
        "values": {"side": _SIDE_HL},
    },
    "level_break": {
        "label": "Breaks a swing level",
        "group": "Levels",
        "about": "The close beyond the latest confirmed swing high or swing low.",
        "sentence": "the close breaks the latest {side} swing, confirmed {k} bars either side",
    },
    "trendline_break": {
        "label": "Breaks a trendline",
        "group": "Levels",
        "about": "The close beyond the line through the latest two swing highs or lows.",
        "sentence": "the close breaks the {side} trendline, swings confirmed {k} bars either side",
    },
    "consolidation_break": {
        "label": "Breaks out of a tight range",
        "group": "Levels",
        "about": "A close outside a run of bars that stayed within a narrow band.",
        "sentence": "the price closes {side} {n} bars that spanned at most {max_pct}%",
    },
    "event": {
        "label": "Around a calendar event",
        "group": "Calendar",
        "about": "A set number of days before, on or after a scheduled event.",
        "sentence": "{days} {unit} {timing} {event}, at {at} ET",
        "values": {"unit": {"sessions": "sessions", "calendar": "calendar days"}},
    },
    "candle": {
        "label": "Candle pattern",
        "group": "Price",
        "about": "One bar's shape, such as a hammer or an engulfing bar.",
        "sentence": "a {pattern} bar closes",
        "values": {
            "pattern": {
                "doji": "doji",
                "hammer": "hammer",
                "shooting_star": "shooting star",
                "bullish_engulfing": "bullish engulfing",
                "bearish_engulfing": "bearish engulfing",
                "inside_bar": "inside",
                "outside_bar": "outside",
            }
        },
    },
}

#: The indicators a signal's `a` or `b` can be (`core.signals` sources), each as a noun phrase. A key
#: core holds to a few words has them all in `values`, so the form offers them as a choice; `anchor`
#: ("session", a time, or an event) is typed, and every other key is a whole number.
SOURCES: dict[str, Words] = {
    "close": {"label": "Price", "group": "Price", "sentence": "the close"},
    "sma": {"label": "Simple moving average", "group": "Averages", "sentence": "the {n}-bar simple average"},
    "ema": {
        "label": "Exponential moving average",
        "group": "Averages",
        "sentence": "the {n}-bar exponential average",
    },
    "vwap": {
        "label": "VWAP",
        "group": "Averages",
        "sentence": "the VWAP from {anchor}",
        "more": {"price": "of each bar's {price}", "weight": "{weight}"},
        "values": {
            "price": {"close": "close", "hlc3": "high, low and close", "ohlc4": "open, high, low and close"},
            "weight": {"volume": "weighted by volume", "equal": "every bar weighted the same"},
        },
        "start": {"price": "close", "weight": "volume"},
    },
    "vwap_band": {
        "label": "VWAP band",
        "group": "Averages",
        "sentence": "the VWAP's {side} band {k} deviations out, from {anchor}",
        "more": {"price": "of each bar's {price}", "weight": "{weight}"},
        "values": {
            "price": {"close": "close", "hlc3": "high, low and close", "ohlc4": "open, high, low and close"},
            "weight": {"volume": "weighted by volume", "equal": "every bar weighted the same"},
            "side": {"upper": "upper", "lower": "lower"},
        },
        "start": {"price": "close", "weight": "volume"},
    },
    "rsi": {"label": "RSI", "group": "Oscillators", "sentence": "RSI({n})"},
    "stochastic": {
        "label": "Stochastic",
        "group": "Oscillators",
        "sentence": "the stochastic's {output} ({k_period}, {k_smooth}, {d_smooth})",
        "values": {"output": {"k": "%K", "d": "%D"}},
    },
    "cci": {"label": "CCI", "group": "Oscillators", "sentence": "CCI({n})"},
    "macd": {
        "label": "MACD",
        "group": "Oscillators",
        "sentence": "the MACD {output} ({fast}, {slow}, {signal})",
        "values": {"output": {"line": "line", "signal": "signal line", "histogram": "histogram"}},
    },
    "adx": {"label": "ADX", "group": "Trend strength", "sentence": "ADX({n})"},
    "atr": {"label": "ATR", "group": "Trend strength", "sentence": "ATR({n})"},
    "relative_volume": {
        "label": "Relative volume",
        "group": "Volume",
        "sentence": "volume against the same minute over {n} sessions",
    },
    "volume_ratio": {
        "label": "Volume ratio",
        "group": "Volume",
        "sentence": "volume against the last {n} bars",
    },
    "prior_period": {
        "label": "Previous period's high or low",
        "group": "Levels",
        "sentence": "the previous {period}'s {side}",
        "values": {"side": _SIDE_HL, "period": {"session": "session", "week": "week", "month": "month"}},
    },
    "opening_range": {
        "label": "Opening range",
        "group": "Levels",
        "sentence": "the first {minutes} minutes' {level}",
        "values": {"level": {"high": "high", "low": "low", "mid": "midpoint"}},
    },
}

# --------------------------------------------------------------------------- filters (gates)
GATES: dict[str, Words] = {
    "change_pct": {
        "label": "Move today",
        "group": "Price move",
        "about": "The price's change since the previous close, in percent.",
        "sentence": "today's move is {range}%",
    },
    "sd_change": {
        "label": "Move today in standard deviations",
        "group": "Price move",
        "about": "Today's move against the day's expected move from VIX.",
        "sentence": "today's move is {range} standard deviations",
    },
    "gap_pct": {
        "label": "Opening gap",
        "group": "Price move",
        "about": "The open against the previous close, in percent.",
        "sentence": "the opening gap is {range}%",
    },
    "open_change_pct": {
        "label": "Move since the open",
        "group": "Price move",
        "about": "The price against today's open, in percent.",
        "sentence": "the move since the open is {range}%",
    },
    "above_ma": {
        "label": "Above a moving average",
        "group": "Trend",
        "about": "The price above its average of the daily closes before today.",
        "sentence": "the price is above its {n}-day {kind}",
        "values": {"kind": {"sma": "simple average", "ema": "exponential average"}},
    },
    "rsi": {
        "label": "RSI",
        "group": "Trend",
        "about": "The relative strength index on intraday bars, 0 to 100.",
        "sentence": "RSI({n}) is {range}",
        "more": {"bars": "on {bars}-minute bars"},
    },
    "stoch": {
        "label": "Stochastic",
        "group": "Trend",
        "about": "The stochastic's %K on intraday bars, 0 to 100.",
        "sentence": "the stochastic is {range}",
        "more": {
            "k": "over {k} bars",
            "k_smooth": "%K smoothed over {k_smooth}",
            "d_smooth": "%D smoothed over {d_smooth}",
            "bars": "on {bars}-minute bars",
        },
    },
    "vix_level": {
        "label": "VIX level",
        "group": "Volatility",
        "about": "VIX now, in index points.",
        "sentence": "VIX is {range}",
    },
    "vix_change_pct": {
        "label": "VIX change today",
        "group": "Volatility",
        "about": "VIX's change since its previous close, in percent.",
        "sentence": "VIX's change today is {range}%",
    },
    "vol_index_rank": {
        "label": "Implied vol rank",
        "group": "Volatility",
        "about": "The underlying's vol index ranked between its low and high over a lookback, 0 to 100.",
        "sentence": "the vol index's rank is {range}",
        "more": {"lookback": "over {lookback} sessions"},
    },
    "vol_index_percentile": {
        "label": "Implied vol percentile",
        "group": "Volatility",
        "about": "The share of past sessions the vol index closed below today's, 0 to 100.",
        "sentence": "the vol index's percentile is {range}",
        "more": {"lookback": "over {lookback} sessions"},
    },
    "vol_index_minus_rv": {
        "label": "Implied over realized vol",
        "group": "Volatility",
        "about": "The vol index less the underlying's realized vol, in vol points.",
        "sentence": "implied vol less realized is {range} points",
        "more": {"n": "realized over {n} sessions"},
    },
    "expected_move_pct": {
        "label": "Expected move",
        "group": "Volatility",
        "about": "The at-the-money straddle on the trade's expiry, in percent of the price.",
        "sentence": "the expected move is {range}%",
    },
    "gex_ratio": {
        "label": "Dealer gamma balance",
        "group": "Dealer positioning",
        "about": "Calls' against puts' gamma exposure over strikes near the price.",
        "sentence": "the gamma ratio over the {strikes} strikes {mode} the price is {range}",
        "values": {"mode": {"nearest": "nearest", "above": "above", "below": "below"}},
    },
    "gex_put_total": {
        "label": "Put gamma total",
        "group": "Dealer positioning",
        "about": "The summed put gamma exposure over strikes near the price, in dollars.",
        "sentence": "put gamma over the {strikes} strikes {mode} the price is {range}",
    },
    "gex_max_otm": {
        "label": "Largest gamma strike's distance",
        "group": "Dealer positioning",
        "about": "How far out of the money the largest-gamma strike near the price sits.",
        "sentence": (
            "the largest-gamma strike of the {strikes} nearest is at most {max}% out on the {side} side"
        ),
    },
    "no_position_today": {
        "label": "Another automation is idle",
        "group": "Other automations",
        "about": "Another automation has neither entered today nor holds anything open.",
        "sentence": "{arm} has not entered today and holds nothing",
    },
    "mid_price": {
        "label": "Price of the trade",
        "group": "The trade's price",
        "about": "The structure's price at the mid, per share.",
        "sentence": "the trade's mid is ${range}",
    },
    "bid_ask": {
        "label": "Bid-ask width",
        "group": "The trade's price",
        "about": "The structure's whole bid-ask width, per share.",
        "sentence": "the bid-ask is at most ${max} wide",
    },
    "reward_risk": {
        "label": "Reward to risk",
        "group": "The trade's price",
        "about": "The most it can make over the most it can lose, at the mid.",
        "sentence": "the reward is at least {min}% of the risk",
    },
}

#: How `{range}` reads with one or both bounds set.
RANGE_WORDS = {"both": "between {min} and {max}", "min": "at least {min}", "max": "at most {max}"}

# --------------------------------------------------------------------------- exits
EXITS: dict[str, Words] = {
    "profit_target_pct": {
        "label": "Take profit",
        "group": "Profit",
        "about": "Close once the trade has made this share of the most it can make.",
        "sentence": "Take profit at {profit_target_pct}% of the most it can make",
    },
    "profit_dollars": {
        "label": "Take profit in dollars",
        "group": "Profit",
        "about": "Close once the open P&L reaches a dollar amount.",
        "sentence": "Take profit at ${profit_dollars}",
    },
    "trailing_stop": {
        "label": "Trailing stop",
        "group": "Profit",
        "about": "Once the trade is up, close if it gives back a set amount from its best.",
        "sentence": "Trail by ${trailing_stop.dollars} once up ${trailing_stop.activate_dollars}",
        "more": {
            "trailing_stop.pct": "or trail by {trailing_stop.pct}% of the most it can make",
            "trailing_stop.activate_pct": "once up {trailing_stop.activate_pct}%",
        },
    },
    "stop_credit_multiple": {
        "label": "Stop loss, credit trades",
        "group": "Loss",
        "about": "Close a credit trade once its loss reaches a multiple of the credit.",
        "sentence": "Stop at a loss of {stop_credit_multiple}× the credit",
    },
    "stop_debit_pct": {
        "label": "Stop loss, debit trades",
        "group": "Loss",
        "about": "Close a debit trade once it has lost this share of the debit.",
        "sentence": "Stop at a loss of {stop_debit_pct}% of the debit",
    },
    "stop_dollars": {
        "label": "Stop loss in dollars",
        "group": "Loss",
        "about": "Close once the open loss reaches a dollar amount.",
        "sentence": "Stop at a loss of ${stop_dollars}",
    },
    "touch": {
        "label": "Price touches a level",
        "group": "Price",
        "about": "Close when the price moves a set distance from where it entered.",
        "sentence": "Close if the price moves {touch.points} points from the entry",
        "more": {"touch.pct": "or {touch.pct}% from the entry"},
    },
    "delta_limit": {
        "label": "Delta limit",
        "group": "Price",
        "about": "Close once the structure's delta per unit passes a size, either way.",
        "sentence": "Close once its delta passes {delta_limit} either way",
    },
    "signal": {
        "label": "On a signal",
        "group": "Signal",
        "about": "Close when a signal fires on the underlying, read as an entry's is.",
        "sentence": "Close {signal.trigger}",
        "more": {
            "signal.bar_minutes": "reading {signal.bar_minutes}-minute bars",
            "signal.volume": "weighing each bar by {signal.volume}'s volume",
        },
    },
    "close_at_dte": {
        "label": "Days left",
        "group": "Time",
        "about": "Close once this many calendar days are left to expiry.",
        "sentence": "Close with {close_at_dte} days left",
    },
    "close_before_expiry_min": {
        "label": "Before the expiry's close",
        "group": "Time",
        "about": "Close this many minutes before the near expiry's close.",
        "sentence": "Close {close_before_expiry_min} minutes before expiry",
    },
    "avoid_events": {
        "label": "Before an event",
        "group": "Events",
        "about": "Close the session before a listed event, a set time before the close.",
        "sentence": (
            "Close {avoid_events.before_close_min} minutes before the close ahead of {avoid_events.labels}"
        ),
    },
    "max_spread": {
        "label": "Wide quotes",
        "group": "Guards",
        "about": "Hold off any exit while the structure's quotes are wider than this.",
        "sentence": "Wait out quotes wider than ${max_spread} a share",
    },
}

#: The nested keys of an exit that is an object, for the slots above ({exit: {key: required}}).
EXIT_PARTS = {
    "trailing_stop": {"dollars": False, "activate_dollars": False, "pct": False, "activate_pct": False},
    "touch": {"points": False, "pct": False},
    "signal": {"trigger": True, "bar_minutes": False, "volume": False},
    "avoid_events": {"labels": True, "before_close_min": False},
}

# --------------------------------------------------------------------------- strikes
_SHIFT_MORE = {"points": "shifted {points} points", "pct": "shifted {pct}%"}

STRIKES: dict[str, Words] = {
    "delta": {
        "label": "Delta",
        "group": "By the option",
        "about": (
            "The strike whose delta is nearest a target: 0.16 is about a 1 in 6 chance of finishing in the "
            "money."
        ),
        "sentence": "at {target} delta",
        "more": {"at_most": "never above {at_most} delta"},
    },
    "premium": {
        "label": "Premium",
        "group": "By the option",
        "about": "The strike whose option is priced nearest a dollar amount a share.",
        "sentence": "priced near ${target} at the {price}",
    },
    "spot": {
        "label": "From the price",
        "group": "By the price",
        "about": "A strike a set distance from the underlying's price now.",
        "sentence": "from the price",
        "more": _SHIFT_MORE,
    },
    "prev_close": {
        "label": "From the previous close",
        "group": "By the price",
        "about": "A strike a set distance from the previous session's close.",
        "sentence": "from the previous close",
        "more": _SHIFT_MORE,
    },
    "open": {
        "label": "From the open",
        "group": "By the price",
        "about": "A strike a set distance from the session's open.",
        "sentence": "from the open",
        "more": _SHIFT_MORE,
    },
    "expected_move": {
        "label": "Expected moves",
        "group": "By the price",
        "about": "A strike a number of expected moves (the at-the-money straddle) from the price.",
        "sentence": "{times} expected moves from the price",
        "more": _SHIFT_MORE,
    },
    "std_dev": {
        "label": "Standard deviations",
        "group": "By the price",
        "about": "A strike a number of standard deviations from the price, read off the straddle.",
        "sentence": "{sd} standard deviations from the price",
        "more": _SHIFT_MORE,
    },
    "opening_range": {
        "label": "From the opening range",
        "group": "By the price",
        "about": "A strike at the opening range's high, low or midpoint, once the range has closed.",
        "sentence": "at the first {minutes} minutes' {level}",
        "more": _SHIFT_MORE,
        "values": {"level": {"high": "high", "low": "low", "mid": "midpoint"}},
    },
    "entry_level": {
        "label": "From the entry's price",
        "group": "By the price",
        "about": "A strike a set distance from the price when the entry fired.",
        "sentence": "from the price at entry",
        "more": _SHIFT_MORE,
    },
    "from_leg": {
        "label": "From another leg",
        "group": "From a leg",
        "about": "A strike a set distance from a leg placed before it, as a spread's wing is.",
        "sentence": "from {leg}",
        "more": _SHIFT_MORE,
    },
    "same_as": {
        "label": "Same as another leg",
        "group": "From a leg",
        "about": "The same strike as a leg placed before it.",
        "sentence": "at the same strike as {leg}",
    },
    "match_width": {
        "label": "Match another spread's width",
        "group": "From a leg",
        "about": "From one leg by the width between two others, so both sides match.",
        "sentence": "from {from} by the width of {width_of}",
    },
    "max_abs_gex": {
        "label": "Largest gamma strike",
        "group": "By dealer positioning",
        "about": "The strike with the largest gamma exposure near the price.",
        "sentence": "at the largest-gamma strike of the {strikes} nearest",
    },
    "gex_max": {
        "label": "Largest of a gamma measure",
        "group": "By dealer positioning",
        "about": "The strike that is largest on one gamma or open-interest measure near the price.",
        "sentence": "at the largest {measure} strike of the {strikes} nearest",
        "values": {
            "measure": {
                "net_positive": "net positive gamma",
                "net_negative": "net negative gamma",
                "call_gex": "call gamma",
                "put_gex": "put gamma",
                "call_oi": "call open interest",
                "put_oi": "put open interest",
            }
        },
    },
}

#: How a strike's `match` reads: for a rule aimed at a price, and one aimed at the option's value.
MATCHES = {
    "price": {
        "exact": "only that strike",
        "nearest": "the nearest listed strike",
        "above": "the first listed at or above",
        "below": "the first listed at or below",
    },
    "value": {
        "nearest": "the nearest",
        "above": "the smallest at or above",
        "below": "the largest at or below",
    },
}

# --------------------------------------------------------------------------- expiry
EXPIRY: dict[str, Words] = {
    "between": {
        "label": "Between",
        "about": "A listed expiry inside a range of calendar days out.",
        "sentence": "Expiring between {min} and {max} days out, the {prefer}",
        "more": {"monthly": "{monthly}"},
        "values": {"prefer": {"first": "nearest", "last": "farthest"}},
    },
    "at_least": {
        "label": "At least",
        "about": "The nearest listed expiry at least so many calendar days out.",
        "sentence": "Expiring at least {days} days out",
        "more": {"monthly": "{monthly}"},
    },
    "dte": {
        "label": "Exactly",
        "about": "The listed expiry a set number of calendar days out.",
        "sentence": "Expiring {days} days out",
        "more": {"tolerance": "or up to {tolerance} days off"},
    },
    "calendar": {
        "label": "Exactly, calendar days",
        "about": "The listed expiry a set number of calendar days out.",
        "sentence": "Expiring {days} calendar days out",
        "more": {"tolerance": "or up to {tolerance} days off"},
    },
    "market_days": {
        "label": "Exactly, trading days",
        "about": "The listed expiry a set number of trading days out (0 is today's).",
        "sentence": "Expiring {days} trading days out",
        "more": {"tolerance": "or up to {tolerance} days off"},
    },
    "after_near": {
        "label": "After the near expiry",
        "about": "The first listed expiry at least so many days after the near one.",
        "sentence": "The far leg expires at least {days} days after the near one",
        "more": {"monthly": "{monthly}"},
    },
}
_MONTHLY = {"true": "monthlies only", "false": "any listed expiry"}
for _k in ("between", "at_least", "after_near"):
    EXPIRY[_k].setdefault("values", {})["monthly"] = _MONTHLY

# --------------------------------------------------------------------------- size and limits
SIZES: dict[str, Words] = {
    "contracts": {
        "label": "Contracts",
        "about": "The same number of contracts every time.",
        "sentence": "Trade {contracts} each time",
        "values": {"contracts": {"1": "1 contract", "n": "{contracts} contracts"}},
    },
    "max_risk": {
        "label": "Risk budget",
        "about": "The most contracts whose most-it-can-lose fits a dollar amount.",
        "sentence": "Trade as many as fit ${max_risk} of risk",
        "more": {"minimum": "never fewer than {minimum}"},
    },
    "allocation_pct": {
        "label": "Share of an allocation",
        "about": "The most contracts whose risk fits a percent of the money set aside.",
        "sentence": "Trade as many as fit {allocation_pct}% of ${allocation}",
        "more": {
            "minimum": "never fewer than {minimum}",
            "max_dollars": "at most ${max_dollars} of risk",
            "max_contracts": "at most {max_contracts} contracts",
        },
    },
}

PROGRESSION: Words = {
    "label": "Step after a run",
    "about": "After each loss (or win) in a row, change the count, held between a floor and a ceiling.",
    "sentence": "after each {after} in a row, {step} contracts, between {floor} and {ceiling}",
    "values": {"after": {"loss": "loss", "win": "win"}},
}

LIMITS: dict[str, Words] = {
    "max_open": {"label": "Most open", "sentence": "at most {max_open} open at once"},
    "per_day": {"label": "Per day", "sentence": "at most {per_day} a day"},
    "every_days": {"label": "Spacing", "sentence": "at least {every_days} days between entries"},
    "one_per_expiry": {
        "label": "One per expiry",
        "sentence": "{one_per_expiry}",
        "values": {"one_per_expiry": {"true": "one position per expiry", "false": "any number per expiry"}},
    },
}

# --------------------------------------------------------------------------- strategies
#: A strategy's settings that its expiry line shows, not its own sentence.
STRATEGY_EXPIRY_SETTINGS = ("dte", "expiries", "far_days")

STRATEGY_VALUES = {
    "direction": {"short": "Sell", "long": "Buy"},
    "right": {"put": "put", "call": "call"},
    "place_by": {
        "delta": "delta",
        "pct": "% from the price",
        "sd": "standard deviations from the price",
        "points": "points from the price",
    },
    "expiries": {"all": "any listed expiry", "weekly": "weeklies", "monthly": "monthlies"},
}

STRATEGIES: dict[str, Words] = {
    "option": {
        "group": "One option",
        "sentence": "{direction} a {right} on {symbol} at {target} {place_by}",
    },
    "vertical": {
        "group": "Spreads",
        "sentence": (
            "{direction} a {right} spread on {symbol}, the short strike at {target} {place_by} and the "
            "other {width} points further out"
        ),
    },
    "ratio": {
        "group": "Spreads",
        "sentence": (
            "Buy a {right} on {symbol} at {target} {place_by} and sell {ratio} more {width} points further "
            "out"
        ),
    },
    "butterfly": {
        "group": "Spreads",
        "sentence": (
            "{direction} a {right} butterfly on {symbol}, the body at {target} {place_by} and wings {width} "
            "points wide"
        ),
    },
    "broken_wing": {
        "group": "Spreads",
        "sentence": (
            "{direction} a broken-wing {right} butterfly on {symbol}, the body at {target} {place_by}, "
            "wings {width} and {width2} points wide"
        ),
    },
    "straddle": {
        "group": "Both sides",
        "sentence": "{direction} a straddle on {symbol} at the strike nearest the price",
    },
    "strangle": {
        "group": "Both sides",
        "sentence": "{direction} a strangle on {symbol} with both strikes at {target} {place_by}",
    },
    "iron_condor": {
        "group": "Both sides",
        "sentence": (
            "{direction} an iron condor on {symbol} with short strikes at {target} {place_by} and wings "
            "{width} points wide"
        ),
    },
    "iron_fly": {
        "group": "Both sides",
        "sentence": "{direction} an iron fly on {symbol} at the price, wings {width} points wide",
    },
    "jade_lizard": {
        "group": "Both sides",
        "sentence": (
            "Sell a jade lizard on {symbol}: a put at {target} {place_by} and a call spread {width} points "
            "wide"
        ),
    },
    "calendar": {
        "group": "Two expiries",
        "sentence": "Buy a {right} calendar on {symbol} at {target} {place_by}",
    },
    "diagonal": {
        "group": "Two expiries",
        "sentence": (
            "Buy a {right} diagonal on {symbol} at {target} {place_by}, the far leg {width} points further "
            "out"
        ),
    },
    "covered": {"group": "Stock", "sentence": "Hold shares of {symbol} and sell a call against them"},
    "custom": {"group": "Custom", "sentence": "Any legs on {symbol}"},
}

#: The order the strategy list's groups are shown in.
STRATEGY_GROUPS = ("One option", "Spreads", "Both sides", "Two expiries", "Stock", "Custom")


def schema() -> dict:
    """Every choice's words, for `builderform.schema`."""
    return {
        "entry": ENTRY,
        "trigger": TRIGGER,
        "signal_rules": SIGNAL_RULES,
        "signals": SIGNALS,
        "sources": SOURCES,
        "gates": GATES,
        "range": RANGE_WORDS,
        "exits": EXITS,
        "exit_parts": EXIT_PARTS,
        "strikes": STRIKES,
        "matches": MATCHES,
        "expiry": EXPIRY,
        "sizes": SIZES,
        "progression": PROGRESSION,
        "limits": LIMITS,
        "strategies": STRATEGIES,
        "strategy_values": STRATEGY_VALUES,
        "strategy_expiry_settings": list(STRATEGY_EXPIRY_SETTINGS),
        "strategy_groups": list(STRATEGY_GROUPS),
    }
