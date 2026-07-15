import sqlite3

from summary_service.backup import create_backup


def test_online_backup_is_consistent(tmp_path) -> None:
    source = tmp_path / "source.db"
    destination = tmp_path / "backups" / "snapshot.db"
    connection = sqlite3.connect(source)
    connection.execute("CREATE TABLE records (value TEXT NOT NULL)")
    connection.execute("INSERT INTO records VALUES ('kept')")
    connection.commit()

    create_backup(source, destination)

    backup = sqlite3.connect(destination)
    assert backup.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert backup.execute("SELECT value FROM records").fetchone()[0] == "kept"
