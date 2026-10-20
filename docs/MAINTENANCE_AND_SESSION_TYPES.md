# Maintenance and session types

Open **Settings → Maintenance → Edit maintenance schedule** to adjust checklist
items. The default rule applies to all sessions. A basic check appears on each
dated setup; an interval appears when it is due for the selected kart.

Each item also has Practice, Qualifying, and Race controls:

- **Default** keeps the default rule.
- **Hidden** excludes the item from that session type without deleting history.
- A positive number sets an interval, with its own Sessions or Days selector.

For example, set Practice to 5 sessions and Race to 1 session to show a service
task every fifth practice and every race. Qualifying can have its own interval
or use the default. Type-specific session intervals count only that type, once
per kart and session; extra setup sheets and extra runs do not add counts.
Calendar-day intervals use elapsed days. Completing service for a task resets
its interval across all session types, since the work services the same kart.
Changing an interval retains prior service; renaming or changing the default
task type creates a new task identity.

Choose a session type in the New Session dialog. For an existing session, open
it and click the **Practice / Qualifying / Race session** button in its header.
Use **All session types** in Sessions to filter the list. Explicit types are
stored by the stable session folder ID, so they survive folder renames and moves.
Until explicitly classified, existing names containing Practice/Testing,
Qualifying, or Race provide a starting type; other names default to Practice.

Checklist ticks are saved with **Save Setup**, including time and editor, through
the app's existing setup save/offline workflow. xCAL's shared schedules and types
use its existing Supabase app documents. Community uses its own local catalog and
Drive journal, so schedule and type changes can be saved locally while offline.
Neither app changes the other's records or account configuration.
