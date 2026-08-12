"""Shared failures for the SQLite lifecycle adapter."""


class SqliteLifecycleError(RuntimeError):
    """Base error for SQLite connection and file lifecycle failures."""


class SqliteLifecycleStateError(SqliteLifecycleError):
    """Raised when an operation is incompatible with lifecycle state."""


class SqliteLifecycleBusyError(SqliteLifecycleError):
    """Raised when another lifecycle prevents exclusive file replacement."""


class UnsupportedSqliteVersionError(SqliteLifecycleError):
    """Raised when the linked SQLite runtime is below the required floor."""


class SqliteDatabaseValidationError(SqliteLifecycleError):
    """Raised when integrity or foreign-key validation rejects a database."""
