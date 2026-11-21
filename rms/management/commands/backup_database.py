import os
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connections


class Command(BaseCommand):
    help = 'Create a consistent SQLite database backup before an upgrade or migration.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--output-dir',
            help='Backup directory. Defaults to VIARMS_BACKUP_DIR or <project>/backups.',
        )

    def handle(self, *args, **options):
        connection = connections['default']
        if connection.vendor != 'sqlite':
            raise CommandError(
                'This command currently supports SQLite only. Use your database provider\'s '
                'snapshot or backup tool for the configured database.'
            )

        database_path = Path(connection.settings_dict['NAME'])
        if str(database_path) == ':memory:' or not database_path.is_file():
            raise CommandError(f'SQLite database file was not found: {database_path}')

        configured_dir = options.get('output_dir') or os.environ.get('VIARMS_BACKUP_DIR')
        backup_dir = Path(configured_dir).expanduser() if configured_dir else settings.BASE_DIR / 'backups'
        try:
            backup_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise CommandError(f'Could not create backup directory {backup_dir}: {exc}') from exc

        timestamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        backup_path = backup_dir / f'viarms-db-{timestamp}.sqlite3'
        temporary_path = backup_path.with_suffix('.sqlite3.tmp')

        try:
            with closing(sqlite3.connect(database_path)) as source, closing(sqlite3.connect(temporary_path)) as destination:
                source.backup(destination)
                integrity = destination.execute('PRAGMA integrity_check').fetchone()
                if not integrity or integrity[0] != 'ok':
                    raise CommandError(f'Backup integrity check failed: {integrity[0] if integrity else "no result"}')
            os.replace(temporary_path, backup_path)
        except Exception as exc:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass
            if isinstance(exc, CommandError):
                raise
            raise CommandError(f'Could not create database backup: {exc}') from exc

        size_mb = backup_path.stat().st_size / (1024 * 1024)
        self.stdout.write(self.style.SUCCESS(
            f'Backup created and integrity checked: {backup_path} ({size_mb:.2f} MiB)'
        ))
        self.stdout.write('Remember to back up MEDIA_ROOT separately; uploaded files are not stored in SQLite.')
