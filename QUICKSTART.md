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
into your **Documents** folder and rename the folder to just **`cherrypick`**, so the rest of this
guide's paths match:

- Windows: `C:\Users\<you>\Documents\cherrypick`
- Mac: `/Users/<you>/Documents/cherrypick`

(If you use git, `git clone` into the same place works too.) This folder is the **installation
folder**: the installer puts everything it needs inside it, and you come back to it to run commands.

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

### Open a terminal in the installation folder

Every command below runs **from inside the cherrypick folder**.

**Windows.** Open **File Explorer**, go to **Documents → cherrypick**, right-click an empty part of
the window and choose **Open in Terminal**. (No such option? Open Terminal from the Start menu,
type `cd $HOME\Documents\cherrypick` and press Enter.) The prompt should now end in
`\Documents\cherrypick>`.

**Mac.** Open **Terminal** and type:

```
cd ~/Documents/cherrypick
```

To check you are in the right place, type `dir` (Windows) or `ls` (Mac): you should see
`install.cmd`, `install.sh`, `QUICKSTART.md` and a `packages` folder.

### Start the installer

**Windows.** Either **double-click `install.cmd`** in File Explorer, or in the terminal type:

```
.\install.cmd
```

If Windows asks whether to run it, choose **More info → Run anyway**.

**Mac.** In the terminal:

```
./install.sh
```

(If it says "permission denied", run `bash install.sh` instead.)

The installer asks you a few things along the way:

| It asks | What to do |
|---|---|
| Type YES to accept the disclaimer | Read [DISCLAIMER.md](DISCLAIMER.md), then type `YES`. |
| Set up Dolt now? | **Optional.** Dolt downloads free market history that two extra features use: the **earnings** strategy and the **technicals** report. It is several GB and can take an hour. Answer **n** to skip it; those two features are then switched off and hidden, and you can add them later by running the installer again. |
| Connect now? | Answer **y**. At `client_secret`, paste the **Client Secret** from step 3; at `refresh_token`, paste the **Refresh Token**. Nothing is shown as you paste; that is normal. Press Enter after each. |

At the end it starts cherrypick and opens the **console** in your web browser.

## 5. After installing: viewing the console

> **⚠️ The console is not hardened. Keep it on this computer.** It has no login and was not built
> to face a network: anyone who can reach it sees your positions and results and can use its
> Config page, including the live-trading halt switch. It listens only on `127.0.0.1` (this
> computer) by design. **Never expose it to your local network or the internet**: no port
> forwarding, reverse proxy, tunnel (ngrok and the like) or remote-access sharing of the page.
> The settings editor (`run.py settings`, port 8804) is under the same rule.

The **console** is cherrypick's control room: a web page that only your own computer can open. The
installer opens it for you when it finishes. To open it any other time:

1. Open your web browser (Chrome, Edge, Safari or Firefox).
2. Go to **<http://127.0.0.1:5070>**. (`127.0.0.1` means "this computer"; nothing is on the internet.)
3. **Bookmark it**, so next time it is one click.

You do not need to start anything first. cherrypick keeps running in the background, and starts
again by itself after you restart the computer. If the page says it cannot connect, wait a minute
(it may still be starting) and refresh; if it still does not load, see **If something goes wrong**
below.

What you will see:

- During market hours (9:30–16:00 US Eastern, on trading days) the strategies take paper trades and
  the pages fill in. Outside those hours there is little to see; that is expected.
- **Overview** is the summary. Each strategy has its own page in the menu on the left (click **»**
  at the top left to open it).
- **Config** turns strategies on and off. Strategies marked **experimental** are off by default.
- A small **read-only** badge at the bottom means your tastytrade key has the read scope only. That
  is right for paper mode.

**Optional: the console in its own window.** If you would rather have cherrypick as a desktop app
than a browser tab, open a terminal in the installation folder and run:

```
cd packages/console/desktop
pnpm start
```

It is the same console in its own window. Closing the window does not stop cherrypick.

## Running cherrypick commands yourself (the virtual environment)

You do not need this for everyday use. It is for checking on cherrypick, or for following a guide
that says to run a command such as `run.py doctor`.

The installer created a private Python setup inside the installation folder, called a **virtual
environment** (the `.venv` folder), so cherrypick's parts never interfere with anything else on your
computer. Before running a cherrypick command, open a terminal in the installation folder (step 4)
and **activate** it:

| Where | Type this |
|---|---|
| Windows Terminal / PowerShell | `.venv\Scripts\Activate.ps1` |
| Windows Command Prompt | `.venv\Scripts\activate.bat` |
| Mac / Linux Terminal | `source .venv/bin/activate` |

The prompt now starts with `(.venv)`. From there, commands are simply `python ...`, for example:

```
python packages/orchestrator/run.py doctor            # a health check, in plain words
python packages/orchestrator/run.py status            # what is running
python packages/orchestrator/run.py restart console   # restart just the console
```

Type `deactivate` when you are done, or just close the window.

If Windows says "running scripts is disabled on this system" when you activate, run this once,
then activate again:

```
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

## Stopping cherrypick

Double-click **`uninstall.cmd`** (Windows) or run **`./uninstall.sh`** (Mac). It stops everything
and removes the background task. Your paper-trading history and settings are kept, so running the
installer again picks up where you left off.

## If something goes wrong

- **"Python 3.11 or newer is required" / "Node.js … is required"**: step 1 did not finish, or the
  Terminal window was not reopened afterwards. Repeat step 1, close Terminal, and run the installer
  again.
- **The console page does not load**: wait a minute and refresh. If it still does not load, open a
  terminal in the installation folder, activate the virtual environment (see above) and run
  `python packages/orchestrator/run.py doctor`. It lists what is wrong in plain words, and
  `python packages/orchestrator/run.py restart console` restarts just the console.
- **No trades appear**: paper trades only happen during market hours on trading days, and only
  when a strategy's entry rules are met. Some days are quiet.

Running the installer again is always safe. It never overwrites your settings.

**Live trading** — sending real orders — is off, and stays off unless you deliberately turn it on
for a specific strategy. That is a separate, advanced step covered in each strategy's own guide.
Read [DISCLAIMER.md](DISCLAIMER.md) again before you consider it.
