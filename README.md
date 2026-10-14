# Assignment Tracker Bot

Coursework lives in four different systems that don't talk to each other, so
deadlines end up in four places and none of them is the one you check.

This pulls assignments from **Blackboard, Canvas, Gradescope and Pearson
MyLab/Mastering** into a single Google Sheet, sorted by due date. It runs
unattended on macOS, signs itself in through your university's single sign-on
without prompting, and notifies your Mac and your phone when work is due.

```
Blackboard ─┐
Canvas    ──┤
Gradescope ─┼──▶  bot (Python + Playwright)  ──▶  Google Sheet  ──▶  phone + Mac
Pearson   ──┘                                       (Apps Script)      (ntfy)
```

- **Two tabs.** A hidden `Bot Data` tab stores everything found; the tracker
  shows only what's due within the next week. Work further out arrives on its
  own as the deadline approaches.
- **Your edits stick.** Change a Status and the bot never overwrites that cell
  again. Mark something Done and it clears itself after 30 minutes. A missed
  deadline drops off a day later.
- **It won't touch your tabs.** The sheet script only writes to a tab it
  created, and if you deliberately point it at one of yours it copies that tab
  to a dated backup first.

---

## Requirements

| | |
| --- | --- |
| macOS | the scheduler uses launchd; the rest is portable |
| Python 3.9+ | `python3 --version` |
| A Google account | for the tracker sheet |
| Node.js | optional, only to run the sheet-script tests |

Everything else — including the browser it drives — is installed by the setup
script into the project folder. Nothing is installed system-wide.

---

## Setup

### 1. Install

```sh
git clone https://github.com/bakr1233/Assignment-Tracker--Bot.git
cd Assignment-Tracker--Bot
./setup.command
```

That creates a virtualenv, installs the dependencies, downloads the Chromium
build Playwright drives (~90 MB), and generates `config.json` with a random
shared secret. It prints the secret — you'll need it in step 3.

### 2. Fill in your logins

Each file in `credentials/` is plain `key=value` text. Fill in the ones you
use and leave the rest blank; a blank file is skipped with a note, not an
error.

| File | What goes in it |
| --- | --- |
| `blackboard.txt` | your university username and password |
| `canvas.txt` | your Canvas address, then **either** an API token **or** username and password |
| `gradescope.txt` | the email and password you log into Gradescope with |
| `pearson.txt` | username, password, and the URL of your **Assignments** page |

Notes that will save you time:

- **Canvas:** an API token is much better — no browser, no 2FA. Get one from
  Canvas → Account → Settings → *+ New Access Token*. Some universities
  disable this for students; if yours does, fill in username and password
  instead and the bot will sign in through a browser.
- **Blackboard:** also set `sources.blackboard.base_url` in `config.json` to
  your school's Blackboard address, e.g. `https://yourschool.blackboard.com`.
- **Gradescope:** if you sign in by clicking *School Credentials* rather than
  typing a password, leave this blank — the bot can't do SSO for Gradescope.
- **Pearson:** the URL must be the page that **lists your assignments**, not
  the course home. MyLab draws its table inside an embedded frame, and a
  course home page contains no assignments at all.

### 3. Set up the Google Sheet

1. Create (or open) the Google Sheet you want to use.
2. **Extensions → Apps Script.** It has to be opened from the sheet — that
   makes the script *container-bound*, which is what lets it write to that
   spreadsheet. A standalone script at script.google.com will not work.
3. Back in a terminal, run `./copy_receiver.command`. This puts
   `sheet_receiver.gs` on your clipboard with your shared secret filled in.
4. In Apps Script: select all the existing code, paste over it, and **Save**.
5. **Deploy → New deployment** → the ⚙️ gear next to "Select type" → **Web
   app**.
6. Set **Execute as: Me** and **Who has access: Anyone**, then **Deploy**.
7. Authorize when asked: *Advanced → Go to (project) → Allow*. The warning
   appears because it's your own script, not a published app.
8. Copy the **Web app URL** — it ends in `/exec`.

> **Why "Anyone"?** The bot posts to that URL from your machine without a
> Google login, so the deployment has to be reachable. That's why every
> request carries the shared secret from `config.json`; without it the script
> refuses. Treat the URL as a secret too.

Then point the bot at it:

```sh
./.venv/bin/python -m bot.main --set-sheet-url "https://script.google.com/.../exec"
./.venv/bin/python -m bot.main --test-sheet
```

`--test-sheet` should answer with the name of your spreadsheet.

### 4. First run

```sh
./run_visible.command
```

A browser window opens and signs in. **Approve the second factor on your
phone** when it arrives — once. Blackboard and Canvas share one browser
profile, so a single approval covers both, and the saved session is reused by
every unattended run afterwards.

You'll see what it found, and the sheet fills in.

### 5. Turn on the schedule

```sh
./install_schedule.sh
```

Runs **every hour, around the clock**, with no window. Hourly rather than once
a day because rows only move when the bot runs — "due within 7 days", "gone a
day after the deadline" and "gone 30 minutes after you tick it off" are only
true if something is running, including overnight when coursework often gets
finished. If the Mac is asleep, launchd runs the job once it wakes. Edit the
hour list in that script to run less often.

```sh
launchctl kickstart -k "gui/$UID/com.assignmentbot.tracker"   # run it now
tail -f logs/scheduled.log                                     # watch
./uninstall_schedule.sh                                        # stop
```

### 6. Phone notifications (optional)

Alerts go through [ntfy](https://ntfy.sh) — no account, nothing to buy.

1. Install **ntfy** from the App Store or Play Store.
2. In `config.json`, set `push.enabled` to `true` and `push.topic` to a long
   random string of your own.
3. In the app, subscribe to that same topic.
4. `./.venv/bin/python -m bot.main --test-push`

Optionally set `sheet_link` in `config.json` to your spreadsheet URL, and
tapping a notification will open it.

> **The topic name is the only thing protecting it.** ntfy.sh topics are
> readable by anyone who knows the name, so make yours long and random and
> don't post it anywhere. For something stronger, self-host ntfy or use its
> auth and point `push.server` at your own instance.

---

## What ends up in the tracker

A to-do list, not an archive. By default it leaves out:

- anything with no due date — usually exams you've already sat;
- anything past its deadline that you've already handed in;
- anything due more than `display_within_days` away (it waits in `Bot Data`);
- anything more than `overdue_grace_days` past due;
- anything marked Done more than `done_linger_minutes` ago.

Past work you *never* handed in stays, because that's the case worth seeing.
Nothing is ever deleted — `Bot Data` keeps every assignment it has seen, so
widening any of these brings rows straight back.

### Settings (`config.json`)

| Key | Default | Does |
| --- | --- | --- |
| `sheet_tab` | `Bot Assignments` | the tracker tab |
| `data_tab` | `Bot Data` | hidden store behind it |
| `adopt_existing_tab` | `false` | let the bot take over a tab you made (it backs it up first) |
| `display_within_days` | 7 | how far ahead the tracker shows |
| `overdue_grace_days` | 1 | how long a missed deadline stays |
| `done_linger_minutes` | 30 | how long something stays after you mark it Done |
| `notify_within_days` | 2 | notification covers work due inside this many days |
| `lookahead_days` / `lookback_days` | 60 / 7 | how far the sources are searched |
| `timezone` | `America/Chicago` | how due times are interpreted |
| `sources.<name>.enabled` | `true` | turn a source off |
| `sources.blackboard.base_url` | — | **required** for Blackboard |
| `sources.gradescope.terms` | `current` | `all` includes past terms |
| `push.enabled` / `push.topic` | `false` / — | phone notifications |

---

## How reliable each source is

| Source | How it works | Expect |
| --- | --- | --- |
| **Canvas** | official REST API, via a token or a browser session | solid |
| **Gradescope** | form login and HTML parsing, no browser | solid, and fast |
| **Blackboard** | browser sign-in, then Blackboard's internal JSON endpoints | good, but those endpoints aren't a published API and differ by version |
| **Pearson** | browser, then reads the MyLab assignment table | works, but its session is short-lived and sign-in can show a CAPTCHA |

**Pearson needs a hand now and then.** When its session lapses the sign-in may
serve an image CAPTCHA, which an unattended run has no honest way to answer.
The run skips Pearson, still writes the other three, and notifies you once.
Run `./run_visible.command`, solve it, and the session is reused until it
expires again.

If a source comes back empty or wrong:

```sh
./.venv/bin/python -m bot.main --only blackboard --visible --debug-dump
```

That saves the page HTML and a screenshot into `debug/`, which is what you
need to adjust the selectors for your school.

---

## Commands

```sh
./run_visible.command                                  # with a window, for sign-in
./.venv/bin/python -m bot.main                         # headless, what the schedule runs
./.venv/bin/python -m bot.main --dry-run               # collect, don't touch the sheet
./.venv/bin/python -m bot.main --only canvas gradescope
./.venv/bin/python -m bot.main --test-sheet            # check the sheet link
./.venv/bin/python -m bot.main --test-push             # check phone notifications
./.venv/bin/python -m bot.main --visible --debug-dump  # save pages into debug/
./.venv/bin/python -m unittest discover -s tests       # 92 Python tests
node tests/receiver_harness.js                         # 49 checks on the sheet script
node tests/cleanup_harness.js                          # 13 checks on the cleanup script
```

`receiver_harness.js` stubs the Google Sheets API and runs the real `doPost`
against a fake spreadsheet. **Run it before deploying a change to
`sheet_receiver.gs`** — it takes a second, and it has caught a bug that would
have overwritten a hand-made tab.

---

## Updating the sheet script

Run `./copy_receiver.command`, paste over everything in Apps Script, Save,
then **Deploy → Manage deployments → ✏️ → Version: New version → Deploy**.

Editing and saving alone changes nothing: a deployment serves the version it
was created from until you publish a new one.

Use that script rather than copying out of an editor. The file in the repo
carries a placeholder so the secret stays out of git, and because the
clipboard is always built from the current file you can't paste a stale copy.

---

## Troubleshooting

**"The deployed version has no doPost in it."** The deployment was created
before the code was saved. Save, then Deploy → Manage deployments → ✏️ →
Version: **New version** → Deploy.

**A Google sign-in page instead of JSON.** "Who has access" isn't *Anyone*.

**"The tab ... already contains something else."** Working as intended: that
tab holds something the bot didn't create. Point `sheet_tab` at a free name,
or set `adopt_existing_tab` to `true` to let it take over (it backs the tab up
first).

**Every row reports as "changed" every run.** The deployment is serving an
older version of the script.

**A source says "skipped".** Its credentials file is blank or incomplete; the
message names the missing field. That isn't an error.

**"Could not find the username box."** Your school's sign-on differs. Run with
`--visible --debug-dump` and look at `debug/` to see what page it landed on.

**Second factor asked for on every run.** The saved session isn't persisting.
Check that `browser_profiles/` is writable, and accept "remember this browser"
when the prompt offers it.

---

## Where things are kept

| | |
| --- | --- |
| `credentials/` | your logins, owner-readable only, never committed |
| `config.json` | sheet URL, shared secret, settings — never committed |
| `browser_profiles/` | saved sessions, including "remember this browser" |
| `logs/` | one file per day, plus `scheduled.log` |
| `state/` | what was found last run, and which failures were already reported |
| `debug/` | only written by `--debug-dump` or when a source finds nothing |

Your passwords sit in plain text in `credentials/`, which is what makes an
unattended run possible — there's nobody there to type a password. They're
only as protected as your macOS account. If you'd rather not store one, use a
Canvas API token and leave the browser-login fields blank.

`credentials/`, `config.json`, `state/`, `logs/`, `browser_profiles/` and
`debug/` are all in `.gitignore`.

---

## Limitations

- **macOS only** for scheduling and desktop notifications. The collection code
  is portable; you'd need to replace launchd and `osascript`.
- **Blackboard uses internal endpoints**, not a published API. They work
  across the versions tested but may differ at your school.
- **Pearson needs a periodic manual sign-in** because of the CAPTCHA.
- **Gradescope SSO isn't supported** — password login only.

## License

MIT. See [LICENSE](LICENSE).
