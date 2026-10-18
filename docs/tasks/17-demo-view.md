# Demo view and presenter mode

Read DESIGN.md steps 12.3 and 14 and `docs/dashboard.md`. Add a fifth dashboard view,
**Demo**, that presents one completed optimization run as six ordered beats for a
60-second onstage walkthrough:

1. 0–8 s — captured snapshot inventory: file count, original bytes, exclusions and
   the first entries with their hashes.
2. 8–20 s — Factory's bounded proposals (settings and rationale), each candidate's
   measured archive bytes, and the selected result against the parent policy.
3. 20–30 s — the workflow topology with persisted node events, and where the
   experiment ran (Daytona sandbox or this computer).
4. 30–47 s — the independent-decoder receipt as a readable card: archive hash, a
   fresh decoder sandbox that received only the archive, engine identity, and the
   snapshot the backend compared byte for byte.
5. 47–55 s — both sandbox records with their persisted status.
6. 55–60 s — the archive download control and the offline restore command.

Use only data the existing API already returns (`/runs/{id}`, `/repositories`,
`/workflow`, SSE events). Do not change the API, archive format or engine. Show a
check mark only when the persisted receipt exists and names the run's snapshot; the
backend records `independent-decode` only after its exact comparison passes. Missing
evidence shows an explicit "not available for this run" state, never placeholder
values. A no-improvement run says the parent policy was retained.

The Demo view selects the newest optimization run (preferring Daytona) when the
selected run is not an optimization. It hides the setup controls. Presenter mode
(`?present=1`, a toggle button, Esc to leave) hides the sidebar, top bar and
page heading, enlarges the beats, and steps through them with ←/→ or PageUp/PageDown
from a sticky, keyboard-accessible stepper. Respect reduced motion and keep the
390-pixel layout free of horizontal scroll.

Extend the existing browser test to open the Demo view after the cloud run, check
all six beats, the receipt, both deletion states and the download link, and exercise
presenter navigation. Run the web build and the browser suite in mock mode; report
what ran and what remains untested.
