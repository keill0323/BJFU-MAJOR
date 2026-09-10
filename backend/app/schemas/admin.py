"""Minimal administrator inbox counts; no private review material."""
from pydantic import BaseModel


class RegistrationTodo(BaseModel):
    match_id: int
    match_name: str
    count: int
    roster_locked: bool


class AdminTodos(BaseModel):
    verification_count: int
    rank_application_count: int
    team_count: int
    registration_count: int
    total: int
    registration_matches: list[RegistrationTodo]
