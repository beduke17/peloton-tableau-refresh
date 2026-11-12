# Peloton → Tableau

Pull your own Peloton workout history into a Tableau Hyper extract, then drop it into a ready-made workbook (KPIs, trends, instructor spotlight).

**Live example:** [Peloton Ride Story on Tableau Public](https://public.tableau.com/app/profile/ben.dukleth/viz/PelotonRideStoryrefreshed/HistoricalTrends#1). The published version includes the Historical Trends dashboard only. The Instructor KPIs dashboard, which uses the Instructor Spotlight extension, is not on Tableau Public. Download the repo and build the full workbook locally to get it.

## Quick start

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

export PELOTON_TOKEN='eyJ...'     # see below; or leave unset and paste when prompted
python peloton_to_hyper.py        # writes peloton_data.hyper
python build_twbx.py              # writes Peloton Ride Story.twbx
```

### Windows (untested)

The scripts are plain Python and should work on Windows, but I've only tested them on macOS Terminal, not PowerShell or cmd. The commands differ:

```powershell
# PowerShell
py -3 -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt

$env:PYTHONUTF8 = "1"                  # avoids Unicode errors from ✓ and box characters
$env:PELOTON_TOKEN = "eyJ..."          # or leave unset and paste when prompted
python peloton_to_hyper.py
python build_twbx.py
```

For cmd, activate with `.venv\Scripts\activate.bat` and set variables with `set PYTHONUTF8=1` and `set PELOTON_TOKEN=eyJ...`. To clear a stale token, use `Remove-Item Env:PELOTON_TOKEN` (PowerShell) or `set PELOTON_TOKEN=` (cmd). Close the workbook in Tableau before refreshing, because Windows won't let the script replace an open `.hyper` file. If you hit a Windows-specific problem, please open an issue.

Open `Peloton Ride Story.twbx` in **Tableau Desktop build 26.1026 or newer**. Older versions strip the embedded Instructor Spotlight extension ("Embedded extension removed when workbook was downgraded"). Re-run both scripts any time to refresh.

## Getting your token

Chrome → members.onepeloton.com → DevTools → Network → Fetch/XHR → reload → click an `api.onepeloton.com` request → Request Headers → copy everything after `authorization: Bearer `. Full steps: `python peloton_to_hyper.py --token-help`.

- Tokens are short-lived. If you get a 401, copy a fresh one and run immediately.
- If a stale `PELOTON_TOKEN` is exported in your shell, the script uses it instead of prompting. `unset PELOTON_TOKEN` to be prompted.
- If requests have no `authorization` header, your session uses cookies, which this script doesn't support.

## Privacy

Your extract and workbook contain heart rate, calories and timestamped workouts. `.gitignore` excludes `.hyper`, `.twbx` and `.env`. Don't commit them, and don't publish the workbook to Tableau Public without trimming columns first.

## Notes

- This uses Peloton's unofficial web API, which may change or break, and use may be subject to Peloton's terms. Use it only for your own account.
- `workout_date` is UTC, so late-evening rides can land on the next day.
- `instructor_name` may be blank if the API doesn't nest the instructor object.
- The workbook's Instructor Spotlight extension sets the "Selected Instructor" parameter and reads names from the "Time by Instructor" sheet.
