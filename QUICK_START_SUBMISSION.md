# ASHADhwani – Submission Quick Start (Windows)

## 1. Open the project
Extract `ASHADhwani_Submission_Ready.zip` to a normal folder, for example:
`C:\\Users\\YourName\\Desktop\\ASHADhwani`

Open that folder in VS Code. The folder containing `pyproject.toml` is the project root.

## 2. Open the VS Code terminal
Use **Terminal → New Terminal** and make sure the terminal is in the project root.

## 3. Create and activate a virtual environment
```powershell
py -3.12 -m venv .venv
.venv\\Scripts\\Activate.ps1
```
If PowerShell blocks activation, use Command Prompt instead:
```bat
.venv\\Scripts\\activate.bat
```

## 4. Install dependencies
```powershell
python -m pip install --upgrade pip
python -m pip install -e .
```

## 5. Start the dashboard
```powershell
python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
```
Keep this terminal open. Open Chrome at **http://127.0.0.1:8000**.

## 6. Test the complete demo flow
1. Select **Anita (English)**.
2. Open **Voice Entry** and press **Start voice entry**.
3. Allow microphone access. If speech recognition is unavailable, use the **Demo shortcuts** or type a note in the note box and press **Send**.
4. Use **Full note** in Demo shortcuts to populate all required fields.
5. Review the six fields on the right.
6. Press **Confirm & save** only after checking the values.
7. Open **Dashboard**, **Beneficiaries**, **Records**, **Follow-ups**, and **Reports**. The saved record should appear in each relevant section.
8. Use **Done** or **Reschedule +2d** on a follow-up to test updates.

## 7. Languages
Use the worker selector at the top:
- Anita (English)
- Sunita (Hindi)
- Lakshmi (Kannada)

The selected worker determines the language of the server's fixed prompt templates.

## Important implementation notes
- Records and follow-ups are stored in `data/app.db` (SQLite) and remain after restarting the server.
- The browser voice input currently uses the browser's Web Speech recognition support where available. Text entry and Demo shortcuts use the same backend visit pipeline.
- Spoken prompts use browser speech synthesis. Live Gnani Prisma/Timbre streaming adapters and Gemini extraction are not configured as verified production integrations in this archive. The Gnani contract document itself marks key API details as unverified until the live spike is run.
- This is a synthetic-data hackathon demo. Do not enter real patient-identifiable or clinical data.
- Do not share `.env` or API keys. No API key is needed for the included text/demo workflow.

## Troubleshooting
- **`uvicorn` not found:** run `python -m pip install -e .` again and launch using the exact `python -m uvicorn ...` command above.
- **Port 8000 already in use:** stop the earlier server with `Ctrl+C`, or run `python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8001` and open `http://127.0.0.1:8001`.
- **Dashboard says connection lost:** confirm the backend terminal still shows the server running, then refresh Chrome.
- **Microphone blocked:** allow microphone access for `127.0.0.1`; use Demo shortcuts or text input if the browser does not support speech recognition.
