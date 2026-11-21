# Notification email delivery

Admin announcements and system events are saved in the in-app notification center. Emails are written to the database outbox in the same transaction, so an SMTP outage does not remove the in-app notification. Stock increases also create a durable stock movement record and queue an Excel report for the tenant admin. The stock report includes the added quantities and a categorized inventory snapshot with cost and retail values.

Stock-in workbooks contain only the stock additions in that operation. For a full snapshot, run `python manage.py email_inventory_backup` on demand or schedule it weekly; this queues one workbook per tenant admin for the regular email worker. Both workbooks are generated in memory; the app does not save report files to media or cloud file storage. The email provider may retain attachments in the recipient's mailbox.

After applying database migrations, run the email worker with:

```powershell
.\.venv\Scripts\python.exe manage.py send_notification_emails
```

Schedule that command to run every few minutes in the deployment environment. For a one-time retry of messages that exhausted their automatic retries, run:

```powershell
.\.venv\Scripts\python.exe manage.py send_notification_emails --retry-failed
```

The worker retries transient failures up to five attempts and reports sent, pending, and failed counts. Set `EMAIL_TIMEOUT` in `.env` to control how long each SMTP attempt can block (defaults to 10 seconds). The command must run in the same environment and use the same database and SMTP environment variables as the Django app.
