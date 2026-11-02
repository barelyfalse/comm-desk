# Comm Desk — Features

Comm Desk is a single-page tool for tracking support cases and when each one needs its next customer communication. The original `Comm Desk.html` is browser-only: it keeps all data in `localStorage` under the key `casedesk.v1`. The server version (`server/`, see [README.md](README.md)) stores the data in SQLite on the VM, adds password sign-in, and shows a save-status indicator in the top bar.

## Cases (home)

- **Ledger** of open cases, sorted by next comm date (soonest and overdue first). Each row shows the case number, name, tags, notes, comm phase, last comm, next comm and auto-close.
- **Due states** are color-coded: overdue (red), today (orange), tomorrow (yellow), later and no date. A legend counts overdue, due-today and due-tomorrow cases.
- **Inline editing**: you can change the phase, last comm and next comm dates directly in the row. The list re-sorts after you leave the field.
- **Search** across number, name, tags, notes and phase.
- **Click to copy** the case number.
- **Closed cases** stay listed for 15 days so they can be reopened. After that they can be deleted.
- **Closed history** (`#history`) keeps a permanent record of every closure (number, title, date, and whether the case was reopened or deleted), even after the case itself is deleted.

## Case workspace

- **Case panel**: number, name, comm phase (`Waiting`, `Update`, `Follow Up`, `Closing soon`, `Closing`, or a custom phase), last comm, next comm, optional auto-close date, and a 1–5 complexity rating.
- **Notes**: free text.
- **Log**: timestamped entries you add yourself, plus automatic entries for events such as case created, follow-up due or sent, auto-close set or cleared, closing email sent, and closed or reopened.
- **TODO**: a checklist for this case. Click an item's box to change its state: `[ ]` to do → `[-]` partly done (light blue) → `[d]` done (gray, crossed out) → `[x]` canceled (dark red, crossed out). Shift-click goes back one state. **Clear done & canceled** removes the finished items.
- **Tags**, with suggestions taken from tags already used on other cases.
- **Links**: the default links plus links for this case only. URLs can contain placeholders. A link that is missing a value shows which variable it needs.
- **Variables**: the values for this case. Any `{placeholder}` used by the case's commands or links appears here automatically and is flagged *needed* until it has a value.
- **Commands**: the case's commands rendered with its values filled in. Each one has a **Copy** button. Missing values are highlighted and link to the variable that needs filling.
- **Close / Reopen / Delete** (delete asks for confirmation).

## Communication rules (automated)

- **48-hour follow-up**: when a `Waiting` case reaches 2 business days since its last comm, it moves to `Follow Up` automatically. The first follow-up is required. Later ones are optional. **Follow up sent** counts it, sets the last comm to today and returns the case to `Waiting`.
- **Auto-close**: setting an auto-close date switches the phase to `Closing soon`, with next comm on the business day before (the closing email). Once the closing email is sent, the phase becomes `Closing` and next comm moves to the auto-close date. A new last comm dated before that window clears the auto-close. The date is ignored when the phase is no longer a closing phase.
- Weekends are skipped in all business-day calculations.

## Commands library (`#commands`)

- Command templates with placeholders such as `admin-cli ... --account={accountId}`.
- A live preview and a list of the placeholders, each labeled with where its value comes from.
- **Add to every case by default**: puts the command on every case. It can still be removed from a single case.
- Deleting a command also removes it from every case.

## Links library (`#links`)

- Default links that appear on every case, with placeholders such as `https://…/case/{caseId}`. They can be hidden on a single case.

## Variables (`#variables`)

- **Global bank**: one value shared by every case, such as your username or team. It is read-only on cases.
- **Default variables**: every case gets its own copy, either empty or with a starting value.
- **Resolution order** for `{name}`: built-ins (`caseId`, `caseName`), then global, then the case's own value, then the default value.

## Data

- **Back up**: copy all data as JSON.
- **Restore**: paste JSON to replace all data. In the server version, the replaced data is kept as a snapshot.
- **Erase everything** is not in the web page in the server version. It's a server command (`main.py erase`).
- On first load, sample cases and commands are created. **Remove examples** clears them.

## UI

- Light and dark themes that follow the system setting.
- **Hints** toggle (the **?** button in the top bar) shows or hides the explanatory hints. The choice is saved in this browser, so each device keeps its own.
- Responsive layout. On phones the ledger becomes cards and a bottom tab bar replaces the top navigation.
- Hash-based routing: `#home`, `#case-<id>`, `#commands`, `#command-<id>`, `#links`, `#variables`, `#history`.
