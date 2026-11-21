# Production upgrade and data protection

## Before deploying

1. Put the site in maintenance mode or stop writes if the host allows it.
2. Create and integrity-check a SQLite backup:

   ```powershell
   python manage.py backup_database
   ```

   Set `VIARMS_BACKUP_DIR` to a private, persistent directory outside the release directory for production. The default is the project `backups/` folder, which is ignored by Git.
3. Back up `MEDIA_ROOT` separately. SQLite does not contain uploaded product images, payment proofs, or other media files.
4. Copy both backups to storage outside the application host. Keep dated copies and periodically restore one to a staging environment.
5. Review the migration plan before applying migrations:

   ```powershell
   python manage.py migrate --plan
   ```

   Migration `0024` adds a constraint that product cost cannot exceed selling price. Check existing production records first; correct any invalid products before migrating:

   ```powershell
   python manage.py shell -c "from django.db.models import F; from rms.models import Product; print(Product.objects.filter(cost_price__gt=F('selling_price')).count())"
   ```

## Deploy

After the backup and preflight checks, deploy the code and run:

```powershell
python manage.py migrate
python manage.py check
```

For a public HTTPS deployment, set `DJANGO_DEBUG=False`, configure `DJANGO_ALLOWED_HOSTS` for the real domain, and set `DJANGO_SECURE_SSL_REDIRECT=True`, `DJANGO_SESSION_COOKIE_SECURE=True`, and `DJANGO_CSRF_COOKIE_SECURE=True`. Set `DJANGO_SECURE_HSTS_SECONDS` only after confirming HTTPS works for the whole site and all required subdomains. These settings are environment-driven so local HTTP development can stay usable.

Expired trials and subscriptions now suspend access without automatically deleting tenant accounts or business records. Permanent tenant deletion remains an explicit administrator action and deletes related records; make a backup first.

## Restore

Stop the application before restoring. Preserve the current database and media directory, restore the selected SQLite backup to the configured database path, restore its matching media backup, and verify the restored copy in staging before reopening the production site. Do not restore a database and media snapshot from different backup times unless you have checked for missing uploads.

The backup command currently supports the SQLite database configured by this project. If the deployment moves to PostgreSQL or another database, use that provider's native snapshot and restore tools instead.
