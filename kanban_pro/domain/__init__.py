"""Canonical kanban domain model (Pydantic v2).

The ONLY types that cross the port boundary. Keep the core minimal — backend-specific
fields belong in each entity's `ext` mapping, not here (see SPEC.md, decision 1).

Card placement is a set of {board_id, column_id, position} entries (`placements[]`),
not a single column_id — a card may live on several boards at once (SPEC decision 4).
Single-board backends + the native store use one placement.

TODO: define Board, Column (with a category enum), Card (with placements[], start_date?,
due_date?, checklists[]), Checklist (nested on Card: {id, title, items:[{id,text,done,
order}]}), Label, Comment, User (minimal: id + display_name + ext), and a Relation edge
(RelationKind lives in ports/). Card.assignees[] and Comment.author reference User ids.
Subtasks = child cards via PARENT/CHILD relations (not checklists).
"""
