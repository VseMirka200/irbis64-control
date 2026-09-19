"""Совместимая точка входа для отдельного подключения к базе данных."""

from irbis_control.ui.windows.database_connector import main

if __name__ == "__main__":
    raise SystemExit(main())
