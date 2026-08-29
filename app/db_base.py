"""Dependency-light SQLAlchemy declarative base shared by DB setup and models."""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""
