# TODOs

## SMS-Man balance refresh

- **What:** Add an on-demand `get-balance` refresh control to the desktop header.
- **Why:** Users can verify available SMS-Man funds without leaving the app.
- **Pros:** Matches the reference’s trust signal while avoiding extra automatic API requests.
- **Cons:** Adds API-state, last-updated-time, error UI, and test coverage.
- **Context:** Display the balance only after an explicit refresh. A failed balance request must not block number issuance or look like an activation error.
- **Depends on / blocked by:** Implement `SmsManClient.get_balance`, a refresh-only UI state, and fake-session unit tests.
