-- migrations/0001_init.sql
-- Runs when PRAGMA user_version is 0.
-- After successful execution the caller sets PRAGMA user_version = 1.
-- (spec.md Section 4, SD-13)

CREATE TABLE workers (
  worker_id    TEXT PRIMARY KEY,
  display_name TEXT NOT NULL,
  language     TEXT NOT NULL CHECK (language IN ('en','hi','kn'))
);

CREATE TABLE sessions (
  session_id TEXT PRIMARY KEY,
  worker_id  TEXT NOT NULL REFERENCES workers(worker_id),
  started_at TEXT NOT NULL,
  ended_at   TEXT
);

CREATE TABLE transcript_turns (
  turn_id     TEXT PRIMARY KEY,
  session_id  TEXT NOT NULL REFERENCES sessions(session_id),
  speaker     TEXT NOT NULL CHECK (speaker IN ('worker','agent')),
  text        TEXT NOT NULL,
  audio_path  TEXT,
  created_at  TEXT NOT NULL
);
CREATE INDEX idx_turns_created ON transcript_turns(created_at);

CREATE TABLE visits (
  visit_id          TEXT PRIMARY KEY,
  session_id        TEXT NOT NULL REFERENCES sessions(session_id),
  worker_id         TEXT NOT NULL REFERENCES workers(worker_id),
  beneficiary_id    TEXT NOT NULL CHECK (length(beneficiary_id) BETWEEN 6 AND 16),
  beneficiary_name  TEXT NOT NULL,
  visit_type        TEXT NOT NULL CHECK (visit_type IN ('pregnancy_anc','newborn_postnatal','immunisation')),
  weight_kg         REAL NOT NULL CHECK (weight_kg BETWEEN 0.5 AND 150.0),
  vaccinations_due  TEXT NOT NULL DEFAULT '[]',
  next_visit_date   TEXT NOT NULL,
  notes             TEXT,
  danger_sign_flag  INTEGER NOT NULL DEFAULT 0 CHECK (danger_sign_flag IN (0,1)),
  danger_sign_codes TEXT NOT NULL DEFAULT '[]',
  form_version      INTEGER NOT NULL,
  created_at        TEXT NOT NULL,
  confirmed_at      TEXT NOT NULL
);
CREATE INDEX idx_visits_worker ON visits(worker_id, confirmed_at);

CREATE TABLE follow_ups (
  follow_up_id TEXT PRIMARY KEY,
  visit_id     TEXT NOT NULL REFERENCES visits(visit_id),
  type         TEXT NOT NULL CHECK (type IN ('phc_referral','next_visit','vaccination_due')),
  description  TEXT NOT NULL,
  due_date     TEXT,
  status       TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open'))
);

CREATE TABLE danger_events (
  event_id    TEXT PRIMARY KEY,
  session_id  TEXT NOT NULL REFERENCES sessions(session_id),
  visit_id    TEXT REFERENCES visits(visit_id),
  turn_id     TEXT REFERENCES transcript_turns(turn_id) ON DELETE SET NULL,
  code        TEXT NOT NULL CHECK (code IN ('DS-01','DS-02')),
  detected_by TEXT NOT NULL CHECK (detected_by IN ('lexicon','llm','both')),
  created_at  TEXT NOT NULL
);
