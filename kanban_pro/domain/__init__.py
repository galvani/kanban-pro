"""Canonical kanban domain model (Pydantic v2).

The ONLY types that cross the port boundary. Keep the core minimal — backend-specific
fields belong in each entity's `ext` mapping, not here (see SPEC.md, decision 1).

TODO: define Board, Column, Card, Label, Comment.
"""
