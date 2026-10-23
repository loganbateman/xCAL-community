# Team setup sections and part types

Open Settings > Workspace > Edit setup fields & Parts pickers.

The preview shows the original fields and team parameters for the selected
section. Adding a parameter updates the preview immediately. Select a custom
field to load it into the editor. Changes appear in the preview and remain in
the dialog draft when switching fields or sections. **Save changes** saves the
current editor along with the rest of the draft. Cancel discards the draft.

Drag a box onto the left or right side of another box to insert it before or
after that box. The yellow insertion marker shows the target. Reordering stays
within the selected section and saves with the team definitions; the setup
sheet uses the same order. Field IDs, run values and part references stay intact.

Use **New field** to add a box. Use the trash can to remove a custom box from
the sheet after confirmation. Removed fields remain restorable, preserving
saved run values and part links. Original fields allow name and display-unit
changes but retain their storage keys, value types and presence on the sheet.

Choose an existing setup section for each parameter. Users cannot create new
sections. Previously saved custom sections remain readable to preserve data.

Use **New part type** to describe a new inventory category and its attributes.
Each attribute has a name, value type (text, number, whole number or date), and
optional units. Existing built-in categories cannot be replaced. Every part
also retains the common inventory fields such as name, manufacturer and notes.

Select **Parts picker** and the new type to connect a setup parameter to that
inventory category. Save team parameters to persist and share the definitions.
Changes in this editor remain a draft until saved; Cancel discards them.

The part creation form also offers **New part type**. Types created there are
saved immediately with the team definitions, even if you later cancel creating
the individual part. Select the new type to use its attributes. In a setup
picker, **New part** creates an item of the picker's selected type.

These definitions use the existing local-first team document and Drive sync.
Use the updated Community beta on every team device to understand custom types
and sections. This change does not convert existing parts or their revisions.
