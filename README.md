# Instagram Reply Studio

Local Docker prototype for **automatic comment-to-DM replies on Instagram**. No Python packages, AI API key, or Instagram credentials are needed for the simulator. Each new top-level comment triggers a private DM using a matching keyword template (or fallback), followed by a public “DM sent” comment reply. The public confirmation is sent only after Meta returns a message ID. Both texts are editable.

## Run locally

```sh
cp .env.example .env
docker compose up --build -d
```

Open http://localhost:8080. Save your reply rules, then simulate a comment. Its private DM and public reply appear automatically in Comment activity. Demo mode never posts to Instagram. Rules and the activity log survive restarts in the `reply-data` Docker volume.

```sh
docker compose logs -f
docker compose down
```

Stopping the app retains data; `docker compose down -v` deletes the data volume. The dashboard shows the latest 100 comments. All records remain in SQLite.

## Connect real Instagram comments

The implementation defaults to **Instagram API with Unified Login** (`GRAPH_API_HOST=graph.facebook.com`). For an older Instagram Login app, set `GRAPH_API_HOST=graph.instagram.com`. Use a professional Business or Creator account and a Meta developer app with the Instagram product. Configure the login setup offered by your Meta app and obtain an access token with `instagram_business_basic`, `instagram_business_manage_comments`, and `instagram_business_manage_messages`. Account/app roles, app review, and access level requirements depend on which accounts the app serves.

1. Configure `INSTAGRAM_ACCOUNT_ID`, `INSTAGRAM_ACCESS_TOKEN`, `META_APP_SECRET`, and a random `WEBHOOK_VERIFY_TOKEN` in `.env`. Set `MODE=live`. Never commit this file.
2. Give Meta a public HTTPS callback ending in `/webhook`, for example using a local tunnel. **Only forward `/webhook`** through a reverse proxy; keep dashboard and `/api/*` private. This prototype dashboard has no user authentication.
3. Set the same verify token in Meta, subscribe to the `comments` field, and subscribe the professional account to your app as instructed by Meta.
4. Recreate the container: `docker compose up -d --force-recreate`.
5. Add a real top-level comment to an owned post from another account. Confirm both the DM (Inbox or Message Requests) and the public comment reply in the dashboard and Instagram.

Webhook events require a valid `X-Hub-Signature-256` signature and the configured account ID. Replies to comments and comments authored by the configured account are ignored to prevent reply loops. Duplicate comment IDs are ignored. Only future received events are processed; historical comments are not fetched.

Automatic DMs and public replies begin as soon as valid events arrive and automation is enabled. Pause and save automation before setup if you want to edit templates first. Paused comments are skipped, not replayed when resumed. Events with an uncertain send outcome are never automatically retried, to avoid duplicate public replies. Check Instagram before taking manual action.

Meta permits one private reply per comment within seven days. A further conversation requires the recipient to respond; this prototype sends no follow-up DMs. If the DM cannot be confirmed, no public confirmation is attempted. If only the public reply fails, the confirmed DM is retained as a partial result.

Official references: [private replies](https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login/messaging-api/private-replies), [comment moderation](https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login/comment-moderation), [webhooks](https://developers.facebook.com/docs/graph-api/webhooks/getting-started/webhooks-for-instagram/), [Meta API collection](https://www.postman.com/meta/instagram/documentation/6yqw8pt/instagram-api).

## Move to cloud

Deploy the same Dockerfile on a container host with a persistent disk mounted at `/data`, and inject `.env` values through its secret manager. Run **one instance**: this prototype uses SQLite and an in-process worker. Back up `/data/replies.db` while the container is stopped before migration. Keep the dashboard behind authentication/private access and route only the webhook publicly over HTTPS.

Before a broader production rollout, add dashboard authentication, token refresh/expiry handling, monitoring and rate-limit scheduling. For multiple instances, move records and jobs to a shared database and durable queue. This prototype has no AI generation, automatic historical import, or automatic retry of failed sends.

## Tests

```sh
python3 -m unittest discover -s tests -v
```

## Temporary local webhook tunnel

Run `docker compose --profile webhook up --build -d` and use `docker compose logs webhook-tunnel` to find the HTTPS address. The tunnel forwards to port 8081, which serves only `/webhook`; the dashboard stays on localhost:8080. Configure Meta with the HTTPS address plus `/webhook`. The generated hostname changes when the tunnel is recreated. Stop it with `docker compose --profile webhook stop webhook-tunnel`. Demo mode accepts webhook verification but rejects comment event delivery; enable live mode only after validating the token, account access, and templates.
