# Quick start

> **⚠️ Experimental, educational software — not financial advice.** cherrypick is a prototype for
> learning about and researching options strategies. Its live-trading paths place **real,
> irreversible orders** at your own risk; options trading involves substantial risk of loss and is
> not suitable for all investors. Paper results are simulated and do not represent actual trading.
> Provided "as is", without warranty. **Read [DISCLAIMER.md](DISCLAIMER.md) before use.**

This gets cherrypick running on your computer in **paper mode**: it watches the real market and
records the trades its strategies *would* make, without sending any orders. It takes about 30
minutes, most of it waiting for downloads. You do not need to know how to program.

## What you need

- A computer running **Windows 10 or 11**, or a **Mac**. (Linux works too; see [INSTALL.md](INSTALL.md).)
- A **tastytrade** brokerage account. cherrypick uses it only to read market prices.
- About 2 GB of free disk space (several GB more if you add the optional earnings data in step 4).

## 1. Install two free tools: Python and Node.js

**Windows.** Click Start, type `Terminal`, and open it. Paste these two lines, pressing Enter after
each, and accept any prompts:

```
winget install -e --id Python.Python.3.13
winget install -e --id OpenJS.NodeJS.LTS
```

Then **close the Terminal window** (the new tools only show up in a new window).

**Mac.** Open **Terminal** (in Applications → Utilities). If you do not have Homebrew yet, install
it from [brew.sh](https://brew.sh) first, then run:

```
brew install python@3.13 node
```

## 2. Download cherrypick

On the cherrypick GitHub page, click the green **Code** button, then **Download ZIP**. Unzip it
somewhere easy to find, such as your **Documents** folder. (If you use git, `git clone` works too.)

## 3. Get your tastytrade API keys

cherrypick logs in to tastytrade with two keys you create on their website. You type them into
the installer once; they are kept in your computer's secure password store (Windows Credential
Manager or the macOS Keychain), never in a file.

1. Sign in at [my.tastytrade.com](https://my.tastytrade.com).
2. Go to **Manage → My Profile → API**, then **OAuth Applications**, and click **+ New OAuth client**.
   Give it any name, such as "cherrypick".
3. Copy the **Client Secret** it shows you and keep it somewhere safe for the next few minutes.
   **It is shown only once.**
4. In the same place, create a **New Personal OAuth Grant**. For paper mode, tick only the **read**
   scope: cherrypick never sends orders in paper mode, and a read-only key cannot place one by
   accident. Copy the **Refresh Token** it gives you.

tastytrade's own guide is at [developer.tastytrade.com](https://developer.tastytrade.com/docs/get-started/).

## 4. Run the installer

**Windows.** Open the cherrypick folder and **double-click `install.cmd`**. If Windows asks whether
to run it, choose **More info → Run anyway**.

**Mac.** In Terminal, go to the folder and run the installer (drag the folder onto the Terminal
window after typing `cd ` to fill in its path):

```
cd ~/Documents/cherrypick
./install.sh
```

The installer asks you a few things along the way:

| It asks | What to do |
|---|---|
| Type YES to accept the disclaimer | Read [DISCLAIMER.md](DISCLAIMER.md), then type `YES`. |
| Set up Dolt now? | **Optional.** Dolt downloads free market history that two extra features use: the **earnings** strategy and the **technicals** report. It is several GB and can take an hour. Answer **n** to skip it; those two features are then switched off and hidden, and you can add them later by running the installer again. |
| Connect now? | Answer **y**. At `client_secret`, paste the **Client Secret** from step 3; at `refresh_token`, paste the **Refresh Token**. Nothing is shown as you paste; that is normal. Press Enter after each. |

At the end it starts cherrypick and opens the **console** in your web browser.

## 5. Use the console

The console is a web page at **<http://127.0.0.1:5070>** that only your own computer can open.
Bookmark it. cherrypick keeps running in the background, including after a restart, so the page is
there whenever you want it.

- During market hours (9:30–16:00 US Eastern), the strategies take paper trades and the pages fill
  in. Outside those hours there is little to see; that is expected.
- The **Overview** page is the summary; each strategy has its own page in the left menu.
- The **Config** page turns strategies on and off. Strategies marked **experimental** are off by
  default.
- A small **read-only** badge at the bottom means your key has the read scope only. That is right
  for paper mode.

## Stopping cherrypick

Double-click **`uninstall.cmd`** (Windows) or run **`./uninstall.sh`** (Mac). It stops everything
and removes the background task. Your paper-trading history and settings are kept, so running the
installer again picks up where you left off.

## If something goes wrong

- **"Python 3.11 or newer is required" / "Node.js … is required"**: step 1 did not finish, or the
  Terminal window was not reopened afterwards. Repeat step 1, close Terminal, and run the installer
  again.
- **The console page does not load**: wait a minute and refresh. If it still does not load, open a
  Terminal in the cherrypick folder and run `.venv\Scripts\python packages\orchestrator\run.py doctor`
  (Windows) or `.venv/bin/python packages/orchestrator/run.py doctor` (Mac). It lists what is wrong
  in plain words.
- **No trades appear**: paper trades only happen during market hours on trading days, and only
  when a strategy's entry rules are met. Some days are quiet.

Running the installer again is always safe. It never overwrites your settings.

**Live trading** — sending real orders — is off, and stays off unless you deliberately turn it on
for a specific strategy. That is a separate, advanced step covered in each strategy's own guide.
Read [DISCLAIMER.md](DISCLAIMER.md) again before you consider it.
