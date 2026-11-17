# Rviyan — private Instagram dashboard

A private React dashboard for one owner managing multiple Instagram professional accounts. It turns top-level post comments into private DMs, then posts a public confirmation after Meta accepts the DM.

## Run with Docker Desktop

```sh
cp .env.example .env
```

Generate a random `DASHBOARD_SETUP_TOKEN` and enter it in your private `.env`:

```sh
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
docker compose up --build -d
```

Open http://localhost:8080. On first use, enter the setup code and choose a password of at least 12 characters. The setup code works only before the owner account is created. Password hashes, sessions, settings, synced posts, and delivery records persist in the `reply-data` volume. Sessions expire after 12 hours. Five failed attempts block sign-in for 15 minutes.

The default `MODE=demo` provides clearly labelled example posts and simulated messages. No Instagram credentials are needed for demo mode.

## Dashboard

- **Overview:** lifetime Instagram comment/reply counts, a recent seven-day activity chart, saved automation summary, and recent conversations. Simulations do not inflate live metrics.
- **Automations:** ordered keyword rules, private DM templates, public comment text, unmatched-comment behavior, and pause control. Whole words or phrases match ignoring case; `hi` does not match `shipping`.
- **Posts:** sync the latest 50 Instagram posts and select all or specific posts. Save changes to apply the scope.
- **Activity:** latest 100 events with search, source/status filters, and separate DM/public reply results.
- **Settings:** account connection, last sync, post count, signature configuration, pause control, and sign-out.
- **Test automation:** simulate a comment against the saved rules and selected post. Simulations never contact Instagram, even in live mode.

Only future webhook events trigger automation. Skipped comments are not replayed. Duplicate IDs are ignored. Replies and comments authored by your own account are excluded to prevent loops. Unconfirmed sends are not automatically retried; check Instagram before any manual action.

## Connect Instagram

Use a Business or Creator account and your Meta developer app. This app defaults to **Unified Login**, using `graph.facebook.com`. For an older Instagram Login app, set `GRAPH_API_HOST=graph.instagram.com`.

1. Supply `INSTAGRAM_ACCOUNT_ID`, `INSTAGRAM_ACCESS_TOKEN`, `META_APP_SECRET`, and `WEBHOOK_VERIFY_TOKEN` through `.env` or your cloud host’s secrets. Your token must have access to the specific Instagram account and the comment/messaging permissions required by its login setup.
2. Set `MODE=live` and recreate the container. Then sign into the dashboard and click **Sync Instagram** to verify account access and fetch recent posts.
3. Give Meta a stable public HTTPS URL ending in `/webhook`, using the same verify token. Subscribe to `comments` in the app and subscribe the Instagram account to your app. App roles, testing restrictions, access levels, and review requirements depend on how the Meta app is used.
4. Set your messages and selected posts in the dashboard, save, then test a comment from another account. Confirm both the DM (Inbox or Message Requests) and the public comment on Instagram.

Meta permits a single private reply per comment within seven days. This tool does not send follow-up DMs. It sends the public “DM sent” confirmation only after Meta returns a private-message ID. Partial results preserve the confirmed DM if only the public reply fails.

References: [Unified Login](https://developers.facebook.com/documentation/instagram-platform/instagram-api-with-unified-login), [private replies](https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login/messaging-api/private-replies), [webhooks](https://developers.facebook.com/docs/graph-api/webhooks/getting-started/webhooks-for-instagram/).

## Temporary local webhook tunnel

```sh
docker compose --profile webhook up --build -d
docker compose logs webhook-tunnel
```

Use the printed HTTPS hostname plus `/webhook` as Meta’s callback. Only `/webhook` is served on port 8081; dashboard/API routes return 404 there. The hostname changes when the tunnel is recreated. Keep Docker and the computer running during local tests.

```sh
docker compose --profile webhook stop webhook-tunnel
```

## Website through Cloudflare Tunnel

For a temporary public HTTPS dashboard, set `COOKIE_SECURE=true` in `.env`, then run:

```sh
docker compose --profile website up -d instagram-replies website-tunnel
docker compose logs website-tunnel
```

Open the printed `https://…trycloudflare.com` URL and sign in with your dashboard password. Account data and settings stay protected by the app’s session authentication. The existing webhook tunnel remains independent and does not need a new Meta callback for this option.

The app still runs on this computer. Keep Docker Desktop and the computer running. Quick Tunnel addresses change when their tunnel is recreated and are intended for testing; a named Cloudflare Tunnel with a domain provides a stable address. To stop website access, run `docker compose --profile website stop website-tunnel`.

## Cloud hosting

Deploy the Dockerfile as one instance with a persistent disk mounted at `/data`. The container builds the React frontend and serves it with the Python API. Set `PORT` to the host’s HTTP port, `DATA_DIR=/data`, and `COOKIE_SECURE=true` behind HTTPS. Set credentials and the one-time setup code through the host’s secret settings. Expose port 8080 through HTTPS for the protected dashboard/API and `/webhook`; port 8081 is optional for webhook-only routing. Do not deploy multiple replicas with the SQLite/in-process queue design.

Back up `/data/replies.db` with the app stopped before moving hosts. Preserve your database to retain the owner login, rules, selected posts, and activity. Keep snapshots private. Larger deployments need a shared database, durable queue, token renewal, and scheduled rate-limit handling.

GitHub Actions repository secrets do not automatically populate local Docker or cloud host settings. A deployment workflow must explicitly map secrets to the chosen host; this project does not yet include that workflow.

## Development and verification

The React app is in `frontend/`, using React and Vite with a committed lockfile. Node is required only for frontend development; the Docker build supplies it.

```sh
cd frontend
npm ci
npm run build
```

The Python runtime uses only the standard library. Run backend and HTTP integration tests with Python 3.12:

```sh
python3 -m unittest discover -s tests -v
```

Stop the app with `docker compose down`. Data is retained; `docker compose down -v` deletes the data volume. Credentials, generated builds, screenshots, and runtime data must never be committed. `.env.example` contains only placeholders.

### Multiple Instagram accounts under one login

Use **Settings → Add an Instagram account**, enter its professional Instagram account ID and access token generated for the same Meta app, then connect. The server verifies identity and subscribes that account to comments before saving it. New accounts start paused. Select the account in the top bar, sync posts, configure its rules and post selection, and save to enable replies.

Each account has separate settings, post cache, and activity. Signed webhook entries are routed by Instagram account ID; pending jobs use the originating account's credentials. Existing account settings, history, and owner login migrate automatically as account 1. Its credentials continue to come from `.env`. Additional tokens are stored only in the persistent SQLite database and are never returned by the API. Protect the Docker data volume and any backups; do not commit or publish them. This remains one owner's private dashboard, with no public registration or separate customer logins.

Additional accounts must be authorized for the configured Meta app. Connecting them does not bypass Meta app roles, access permissions, token expiry, or App Review requirements. The dashboard requires HTTPS when entering credentials over a public tunnel.

## Permanent Cloudflare Tunnel on a laptop

Create a named, remotely managed Cloudflare Tunnel and run its Docker connector on the same Docker network as this Compose project. Set its restart policy to `unless-stopped`. In Cloudflare, publish two HTTP services:

| Hostname | Origin service |
| --- | --- |
| `dashboard.yourdomain.com` | `http://instagram-replies:8080` |
| `hooks.yourdomain.com` | `http://instagram-replies:8081` |

Leave route paths blank. Set `COOKIE_SECURE=true` in the private `.env` and use the permanent `https://hooks.yourdomain.com/webhook` callback in Meta. Named tunnel hostnames remain stable across connector restarts. The laptop must stay awake, connected to the internet, with Docker and the tunnel connector running. Store the tunnel token privately; never commit its Docker command or token.

If the connector was created separately, attach it to the app's network shown by Docker network inspection. Network membership and the restart policy persist for that container; recreating the connector requires configuring them again. The optional `webhook` and `website` Compose profiles are temporary Quick Tunnels and are not needed for named-tunnel access.

## Branding

The deployed Rviyan wordmark, RV monogram, and browser icon are editable SVG assets in `frontend/public/`. `branding/rviyan-logo-v2.png` is a newer logo concept for review; it is not yet applied to the dashboard.
