# Reply Studio — private Instagram dashboard

A React dashboard for one owner’s Instagram professional account. It turns top-level post comments into private DMs, then posts a public confirmation after Meta accepts the DM.

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
