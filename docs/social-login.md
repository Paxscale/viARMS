# Google and Apple sign-in setup

The login and registration pages show provider buttons only after credentials are configured. OAuth sign-in does not configure outbound SMTP. New social signups still collect business/KYC details and require administrator approval. A verified Google or Apple email matching an existing account signs in to that account; new users continue to the business/KYC signup form.

## 1. Install in the project virtual environment

From the repository root in PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python manage.py migrate
```

Copy `.env.example` to `.env`; `.env` is ignored by Git.

## 2. Google sign-in

1. In [Google Cloud Console](https://console.cloud.google.com/), create/select a project and configure its OAuth consent screen.
2. Create an OAuth client ID with application type **Web application**.
3. Add this authorized JavaScript origin: `http://127.0.0.1:8000`.
4. Add this authorized redirect URI: `http://127.0.0.1:8000/accounts/google/login/callback/`.
5. Put the client ID and secret in `.env`:

```dotenv
GOOGLE_OAUTH_CLIENT_ID=your-client-id
GOOGLE_OAUTH_CLIENT_SECRET=your-client-secret
```

For production, register your production HTTPS origin and callback URL too.

## 3. Apple sign-in for iCloud users

"Sign in with Apple" lets people use their Apple Account, including iCloud email or Apple's private relay address. Web sign-in requires an Apple Developer account, a primary App ID with Sign in with Apple enabled, a related **Services ID**, and a Sign in with Apple private key.

1. In the Apple Developer portal, enable Sign in with Apple on the primary App ID and create a related Services ID.
2. Configure the Services ID website/domain and return URL to your public HTTPS domain and `https://your-domain/accounts/apple/login/callback/`.
3. Create a Sign in with Apple key. Record its Key ID and Team ID and download its `.p8` key. Put the private key into `.env` as one line with `\n` between key lines:

```dotenv
APPLE_SERVICE_ID=your.web.service.id
APPLE_KEY_ID=your-key-id
APPLE_TEAM_ID=your-team-id
APPLE_PRIVATE_KEY="-----BEGIN PRIVATE KEY-----\n...key contents...\n-----END PRIVATE KEY-----"
```

Apple web sign-in needs a registered public domain and HTTPS callback, so it will not work against `127.0.0.1` alone. See [django-allauth Apple setup](https://docs.allauth.org/en/latest/socialaccount/providers/apple.html).

## 4. Finish configuration

Restart Django after editing `.env`. A provider button appears after that provider has all required credentials. New users finish the business signup form after first social sign-in; administrator approval still applies. Existing users with a matching verified provider email are signed in directly.

Keep `.env` and Apple's `.p8` private. Google and Apple are configured as trusted verified-email providers for matching existing accounts. Existing accounts are not automatically linked to a social account, so the provider email must continue to match the account email. Revoke/rotate any credential that has been exposed.
