# AoE2 Replay Analytics MVP — Manual Upload First

## 1. Product idea

Build a web app where an Age of Empires II: Definitive Edition player can manually upload an `.aoe2record` replay file and receive a clean analytics report about the match.

This avoids needing Steam account linking, a desktop watcher app, or live parsing in the first version. The goal is to prove that users care about the insights before investing in automation.

---

## 2. MVP goal

The MVP should answer one simple question:

> “Can a player upload a replay and get useful, understandable, actionable AoE2 insights within seconds?”

The first version does **not** need to solve every analytics problem. It only needs to produce a report that feels useful enough for a player to upload another replay.

---

## 3. User flow

1. User visits the website.
2. User clicks **Upload Replay**.
3. User selects an `.aoe2record` file from their PC.
4. App validates the file type and size.
5. App uploads the replay to the server.
6. Server stores the replay file.
7. Server parses replay metadata and timeline data.
8. Server generates a match report.
9. User sees the report in the browser.
10. User can optionally create an account to save replay history.

---

## 4. Where users find replay files

Most Windows users can find AoE2: DE replay files in a path similar to:

```txt
C:\Users\<WindowsUsername>\Games\Age of Empires 2 DE\<profile-id>\savegame
```

The app should include helper text like:

> “In AoE2: DE, go to Single Player → Load Game → Replays → Open Saved Games Folder. Then upload the `.aoe2record` file here.”

Accepted file type:

```txt
.aoe2record
```

---

## 5. What an `.aoe2record` file is

An `.aoe2record` file is not a video. It is a recorded-game file that stores game setup data and player command data. The game engine can replay those commands to recreate the match.

For analytics, this is useful because the file can expose structured match information such as:

- Players
- Civilizations
- Teams
- Map
- Game type
- Game duration
- Build order events
- Technology timings
- Unit creation timings
- Resign times
- Chat/events, depending on parser support
- Economy and military activity, depending on what can be derived

---

## 6. MVP feature scope

### Version 0.1 — Upload and basic replay summary

The first version should only include:

- Manual `.aoe2record` upload
- File validation
- Replay parsing
- Basic match summary
- Simple player comparison
- Basic timeline events
- Replay history for logged-in users, optional

### Do not build yet

Avoid these features in the first MVP:

- Steam account linking
- Automatic replay detection
- Desktop app
- Live game parsing
- AI coaching chat
- Team dashboards
- Public profile pages
- Paid subscriptions
- Complex ladder/stat integrations
- Full replay viewer
- Video rendering

---

## 7. Basic report sections

### A. Match overview

Show the user the basic details of the replay.

Example fields:

| Field | Example |
| --- | --- |
| Map | Arabia |
| Game type | 1v1 Random Map |
| Duration | 32:14 |
| Players | Player A vs Player B |
| Civilizations | Franks vs Ethiopians |
| Winner | Player A |
| Replay version | AoE2: DE version/build |

---

### B. Player summary

Show one row per player.

| Player | Civ | Team | Result | Feudal Time | Castle Time | Imperial Time |
| --- | --- | --- | --- | --- | --- | --- |
| Player A | Franks | 1 | Won | 10:21 | 19:45 | — |
| Player B | Ethiopians | 2 | Lost | 10:58 | 21:12 | — |

---

### C. Build order timeline

Show important early-game events in chronological order.

Example:

| Time | Player | Event |
| --- | --- | --- |
| 00:00 | Player A | Created villagers |
| 02:10 | Player A | Built lumber camp |
| 04:55 | Player A | Built mill |
| 10:21 | Player A | Advanced to Feudal Age |
| 12:40 | Player A | Built stable |
| 19:45 | Player A | Advanced to Castle Age |

This does not need to be perfect at first. Even a partial timeline can be useful.

---

### D. Key timings

Show important timing benchmarks.

Examples:

- Feudal age time
- Castle age time
- Imperial age time
- First military building
- First military unit
- First market
- First blacksmith
- First town center after Castle Age
- First castle
- First major attack, if detectable

---

### E. Actionable notes

This is where the app becomes valuable. Convert raw data into plain-English observations.

Example notes:

- “You reached Feudal Age 37 seconds later than your opponent.”
- “Your first military building came after your opponent’s, which may have delayed pressure.”
- “You reached Castle Age at 19:45, which is strong for a fast Castle approach.”
- “You did not add a second Town Center within 3 minutes of reaching Castle Age.”
- “Your opponent applied earlier military pressure.”

For the MVP, these notes can be rule-based instead of AI-generated.

---

## 8. Suggested technical architecture

### Frontend

Recommended stack:

- Next.js
- React
- Tailwind CSS
- Upload component
- Report page
- Optional auth later

Frontend pages:

```txt
/upload
/replays/[replayId]
/replays
```

Main frontend components:

```txt
ReplayUploadCard
ReplayUploadInstructions
ReplayProcessingState
ReplaySummaryHeader
PlayerComparisonTable
TimelineTable
KeyTimingCards
ActionableNotesPanel
```

---

### Backend

Recommended stack:

- Node.js / Next.js API route for upload handling
- Python worker for replay parsing, if using Python parser tools
- Database for parsed results
- Object storage for raw replay files

Possible backend flow:

```txt
POST /api/replays/upload
↓
Validate file
↓
Save raw file to storage
↓
Create replay record in database with status = processing
↓
Send file path to parser
↓
Parser extracts data
↓
Save parsed JSON to database
↓
Update replay record with status = complete
↓
Return replayId to frontend
```

---

## 9. Replay parser approach

The backend should parse `.aoe2record` files into JSON.

A likely first approach is to use an existing replay parser library rather than building your own parser from scratch.

Example parser workflow:

```txt
.aoe2record file
↓
Parser library
↓
Raw parsed data
↓
Normalized match JSON
↓
Derived analytics
↓
User-facing report
```

The app should separate **raw parsed data** from **derived insights**.

---

## 10. Normalized data model

The parser output should be transformed into your own app-friendly shape.

Example:

```json
{
  "match": {
    "id": "replay_123",
    "map": "Arabia",
    "durationSeconds": 1934,
    "gameType": "Random Map",
    "playedAt": null,
    "version": "aoe2de-build"
  },
  "players": [
    {
      "slot": 1,
      "name": "Player A",
      "civilization": "Franks",
      "team": 1,
      "result": "win",
      "feudalTimeSeconds": 621,
      "castleTimeSeconds": 1185,
      "imperialTimeSeconds": null
    }
  ],
  "events": [
    {
      "timeSeconds": 621,
      "playerSlot": 1,
      "type": "age_up",
      "label": "Advanced to Feudal Age"
    }
  ],
  "insights": [
    {
      "severity": "info",
      "category": "timing",
      "text": "You reached Feudal Age 37 seconds later than your opponent."
    }
  ]
}
```

---

## 11. Database tables

### `replays`

| Column | Type | Notes |
| --- | --- | --- |
| id | uuid | Primary key |
| user_id | uuid/null | Optional before login |
| original_filename | text | Uploaded file name |
| file_url | text | Raw replay storage path |
| status | text | uploaded, processing, complete, failed |
| map | text | Parsed map name |
| duration_seconds | int | Match length |
| game_type | text | Match type |
| parser_version | text | Useful for future reprocessing |
| created_at | timestamp | Upload time |

### `replay_players`

| Column | Type | Notes |
| --- | --- | --- |
| id | uuid | Primary key |
| replay_id | uuid | Foreign key |
| slot | int | Player slot |
| name | text | Player name |
| profile_id | text/null | If available |
| civilization | text | Civ name |
| team | int/null | Team number |
| result | text/null | win/loss/unknown |
| feudal_time_seconds | int/null | Age timing |
| castle_time_seconds | int/null | Age timing |
| imperial_time_seconds | int/null | Age timing |

### `replay_events`

| Column | Type | Notes |
| --- | --- | --- |
| id | uuid | Primary key |
| replay_id | uuid | Foreign key |
| time_seconds | int | Event time |
| player_slot | int/null | Related player |
| type | text | age_up, building, unit, tech, resign, etc. |
| label | text | Human-readable event text |
| raw_payload | jsonb | Optional raw event data |

### `replay_insights`

| Column | Type | Notes |
| --- | --- | --- |
| id | uuid | Primary key |
| replay_id | uuid | Foreign key |
| player_slot | int/null | Related player |
| category | text | timing, economy, military, strategy |
| severity | text | info, warning, good, critical |
| text | text | User-facing insight |

---

## 12. File upload validation

The upload endpoint should check:

- File extension is `.aoe2record`
- File size is below a safe limit
- File is not empty
- User has not exceeded upload quota
- Parser can read the file

Suggested MVP limits:

```txt
Max file size: 50 MB
Max uploads without account: 3 per browser/session
Max uploads with free account: 25 total
```

---

## 13. Processing states

The frontend should show clear processing states.

```txt
Idle
Uploading
Uploaded
Processing
Complete
Failed
```

Example messages:

- “Uploading replay…”
- “Reading match data…”
- “Building your report…”
- “Replay parsed successfully.”
- “We could not parse this replay. It may be from an unsupported game version.”

---

## 14. Rule-based insight engine

Start simple. Use deterministic rules before adding AI.

Example rules:

### Feudal timing comparison

```txt
If player_feudal_time > opponent_feudal_time + 30 seconds:
  Show: "You reached Feudal Age more than 30 seconds after your opponent."
```

### Castle timing comparison

```txt
If player_castle_time > opponent_castle_time + 60 seconds:
  Show: "Your opponent reached Castle Age at least 1 minute before you."
```

### Missing Castle Age expansion

```txt
If player reaches Castle Age and no second Town Center is detected within 180 seconds:
  Show: "You did not add a second Town Center within 3 minutes of reaching Castle Age."
```

### Earlier military pressure

```txt
If opponent creates first military unit more than 45 seconds before player:
  Show: "Your opponent created military significantly earlier."
```

### Strong timing praise

```txt
If player reaches Castle Age before 20:00:
  Show: "Your Castle Age timing was strong for many standard openings."
```

---

## 15. MVP report design

Suggested page layout:

```txt
Replay Report
├── Match Summary Card
├── Player Comparison Table
├── Key Timing Cards
├── Timeline
├── Actionable Notes
└── Upload Another Replay CTA
```

The report should feel useful even if parsing is incomplete. If a section is unavailable, show:

> “This replay did not include enough data for this section yet.”

Do not show broken or empty analytics cards.

---

## 16. Manual MVP landing page copy

### Hero headline

> Upload an AoE2 replay. Get a smarter match breakdown.

### Subheadline

> Start with a manual `.aoe2record` upload and see key timings, player comparisons, build order events, and simple coaching notes.

### CTA

> Upload Replay

### Trust text

> No Steam linking required for the MVP. Just upload a replay file from your saved games folder.

---

## 17. Upload page helper copy

```txt
Upload your AoE2: DE replay file

Choose a `.aoe2record` file from your saved games folder. We’ll parse the replay and generate a match report with key timings, player comparisons, and useful notes.

Need help finding it?
In AoE2: DE, go to Single Player → Load Game → Replays → Open Saved Games Folder.
```

---

## 18. What to measure

The MVP should measure whether users care enough to keep using it.

Track:

- Number of replay uploads
- Upload success rate
- Parser failure rate
- Average time to report
- Number of repeat uploads
- Number of users who create accounts after first report
- Most viewed report sections
- Most common parser errors
- Feedback thumbs up/down on insights

---

## 19. Monetization should wait

Do not charge immediately unless users clearly want the reports.

Possible pricing later:

### Free

- Manual uploads
- Basic match summary
- Limited replay history

### Pro

- Unlimited replay history
- Trend analysis over many games
- Build order leak detection
- Matchup/civ-specific insights
- Private profile
- Desktop auto-upload app

### Team / Clan

- Shared team dashboard
- Practice review
- Player comparisons
- Coach notes

---

## 20. Future upgrade path

### Phase 1 — Manual upload MVP

- Manual `.aoe2record` upload
- Basic parser
- Report page
- Rule-based insights

### Phase 2 — User accounts and replay history

- Save uploaded reports
- Compare games over time
- Player profile page
- Basic trends

### Phase 3 — Desktop auto-uploader

- Windows app watches the replay folder
- Detects new `.aoe2record` files
- Uploads automatically
- Connects local app to web account

### Phase 4 — Advanced analytics

- Build order detection
- Idle time estimates
- Army/economy balance
- Map control indicators
- Civ-specific recommendations
- Matchup-specific benchmarks

### Phase 5 — Live/near-live parsing research

- Investigate whether live file watching is possible
- Explore memory/state limitations carefully
- Avoid anything that violates game rules, anti-cheat policies, or fair-play expectations

---

## 21. Recommended first build ticket list

### Ticket 1 — Upload UI

Build a replay upload card that accepts `.aoe2record` files and shows upload progress.

Acceptance criteria:

- User can drag and drop a replay file
- User can browse files manually
- Invalid file types are rejected
- Upload progress is visible
- Error states are clear

---

### Ticket 2 — Upload API

Create an API endpoint that accepts replay uploads.

Acceptance criteria:

- Endpoint accepts multipart file upload
- Endpoint validates `.aoe2record` extension
- Endpoint stores file in object storage or local dev storage
- Endpoint creates a replay database record
- Endpoint returns `replayId`

---

### Ticket 3 — Parser worker

Create a backend worker that parses the uploaded replay.

Acceptance criteria:

- Worker receives a replay file path
- Worker extracts match metadata
- Worker extracts player metadata
- Worker extracts at least basic age-up timings if available
- Worker stores normalized JSON
- Worker marks replay status as complete or failed

---

### Ticket 4 — Replay report page

Create a report page for a parsed replay.

Acceptance criteria:

- Page loads by `replayId`
- Page shows match overview
- Page shows players
- Page shows key timings
- Page shows basic timeline events
- Page handles failed/incomplete parsing gracefully

---

### Ticket 5 — Insight rules

Add simple rule-based insights.

Acceptance criteria:

- Feudal timing comparison rule
- Castle timing comparison rule
- First military timing rule, if available
- Missing expansion rule, if available
- Insights display in plain English

---

## 22. MVP success criteria

The MVP is successful if:

- Users can upload replays without help
- At least 80% of supported replays parse successfully
- The report loads in a reasonable amount of time
- Users understand the insights
- Users upload more than one replay
- Users ask for replay history, trend tracking, or auto-upload

---

## 23. Core product principle

The MVP should not try to be a full replay analyzer immediately.

It should do this one thing well:

> Turn a manually uploaded AoE2 replay into a useful match report that helps the player understand what happened and what to improve next.
