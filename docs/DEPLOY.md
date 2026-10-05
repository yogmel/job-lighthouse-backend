# Deploy

Push to `main` → `.github/workflows/deploy.yml`:

1. **build**: builds the one app image, pushes it to GHCR as
   `ghcr.io/yogmel/job-lighthouse-backend:sha-<commit>` (and `latest`).
2. **deploy**: copies `docker-compose.yml`, `nginx/default.conf` and
   `deploy/deploy.sh` to the droplet, then runs `deploy.sh <image>` over SSH.

`deploy.sh` pulls the image, runs `docker compose up -d --no-build --wait`
(migrations first, then both services, then Nginx), and fails if any app
container isn't on the new image, or if the stack isn't healthy within
3 minutes. It then reloads Nginx, because Nginx looks up the app
containers' IPs only at start and the deploy just gave them new ones
(PROJ-011). The GHCR login uses the job's short-lived
`GITHUB_TOKEN` and is logged out after the pull. No long-lived registry
credential lives on the droplet.

App secrets live **only** in the droplet's `.env`. GitHub holds only the SSH
details.

## Layout on the droplet

The droplet also hosts other sites, so the **host's** Nginx (systemd) owns
ports 80/443 and holds the Let's Encrypt cert. The compose Nginx listens on
localhost only and still routes by path:

```
client ──https──▶ host Nginx :443 ──https──▶ compose Nginx 127.0.0.1:8443 ──▶ job-runner / companies
                  (Let's Encrypt)            (self-signed, loopback only)
```

The container cert never leaves the machine, so it doesn't need renewing
with Let's Encrypt.

## One-time droplet setup

Do these once, in order. `<domain>` is the API host (e.g. `api.example.com`).
"As root" means root or an admin user with `sudo`.

### 1. Droplet + DNS

- Create an Ubuntu 24.04 droplet with **2 GB RAM or more**. The full stack
  uses ~600 MB at idle, and Docker, host Nginx and any other sites come on
  top. Playwright (later) needs more.
  - 512 MB does **not** work: the services get OOM-killed and restart in a
    loop, CPU sits at 100% and SSH stops responding.
  - When resizing, pick **CPU and RAM only** if you may want to downsize
    later. A disk resize can't be undone.
- Add your personal SSH key when creating it.
- Point an `A` record for `<domain>` at the droplet IP.
- Optional: a 1 GB swapfile as a buffer for spikes (e.g. image pulls). No
  extra cost, it lives on the droplet's disk:

  ```sh
  fallocate -l 1G /swapfile && chmod 600 /swapfile
  mkswap /swapfile && swapon /swapfile
  echo '/swapfile none swap sw 0 0' >> /etc/fstab
  ```

  If `free -h` shows swap in steady use, the droplet needs more RAM.

### 2. Docker + firewall

As root:

```sh
apt-get update && apt-get install -y ca-certificates curl
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
  https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
  > /etc/apt/sources.list.d/docker.list
apt-get update && apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin

ufw allow OpenSSH && ufw allow 80/tcp && ufw allow 443/tcp && ufw --force enable
```

Postgres and the service ports are bound to `127.0.0.1` in compose, so only
80/443 are public. Docker's own port rules bypass `ufw`, so keep it that way.

### 3. Deploy user

```sh
adduser --disabled-password --gecos "" deploy
usermod -aG docker deploy        # docker group is root-equivalent; deploy-only user
install -d -m 700 -o deploy -g deploy /home/deploy/.ssh
install -d -o deploy -g deploy /opt/job-lighthouse
```

Make a **dedicated CI keypair** on your machine (not your personal key):

```sh
ssh-keygen -t ed25519 -C "github-deploy" -f ./deploy_key -N ""
```

Put `deploy_key.pub` in `/home/deploy/.ssh/authorized_keys` (mode 600, owner
`deploy`). In `/etc/ssh/sshd_config` set `PasswordAuthentication no` and
`PermitRootLogin prohibit-password`, then `systemctl reload ssh`.

### 4. `.env`

Work as `deploy` (from an admin session: `sudo -iu deploy`) so the files are
owned by the user `deploy.sh` runs as. `.env.example` isn't copied by CI;
paste its contents in by hand.

```sh
cd /opt/job-lighthouse
nano .env        # contents of .env.example, with real, strong values
chmod 600 .env
```

Auth needs these (from v0.2):

- `JWT_SECRET`: **required**. Compose won't start without it. Generate
  it with `openssl rand -hex 32`. Both services read the same value, so
  changing it logs everyone out.
- `GOOGLE_CLIENT_ID`: optional. Without it, `POST /auth/google` returns 503.
- `JWT_TTL_SECONDS`: optional, 7 days by default.
- `CORS_ALLOWED_ORIGINS`: frontend origins the browser may call from,
  comma-separated, no trailing `/`. Unset means none, so the Vercel
  frontend is blocked. Set it to
  `CORS_ALLOWED_ORIGINS=https://job-lighthouse.vercel.app`.
  CORS headers come from the apps only. Don't add `Access-Control-*`
  headers in either Nginx: a duplicate header makes the browser reject
  the response.

Match scoring and add-company detection need these (from v0.5 / v0.8).
Both services read them:

- `OPENAI_API_KEY`: optional. Without it, runs still work but new jobs
  are stored unscored, and stay unscored. `POST /companies/detect` still
  matches known boards, but any other URL comes back `needs_custom`.
- `OPENAI_MODEL`: optional, `gpt-5-mini` by default. Must support
  structured outputs.
- `DETECT_LIMIT_PER_HOUR`: optional, 20 by default. `POST /companies/detect`
  calls each user may make per hour; over it is a 429. Counted in memory,
  so it assumes one Companies process, and resets on deploy.

Email needs these (from v0.6):

- `RESEND_API_KEY`: optional. Without it, no email is sent.
- `EMAIL_FROM`: required when `RESEND_API_KEY` is set, or the services
  won't start. A sender on a domain verified in Resend, e.g.
  `Job Lighthouse <digest@example.com>`.
- `PASSWORD_RESET_URL`: optional, Companies only (from v0.11). The
  frontend page that sets a new password, e.g.
  `https://job-lighthouse.vercel.app/reset-password`. Reset emails link to
  it with `?token=...`. Without it (or without `RESEND_API_KEY`),
  `POST /auth/password-reset/request` still answers 202 but sends nothing.

Scheduling needs nothing (from v0.7). The Job Runner ticks every minute and
starts the runs due by each user's `Config.cron` (UTC).

- `SCHEDULER_ENABLED`: optional, `true` by default. `false` stops scheduled
  runs; manual `POST /runs` still works.

Also set these, so the compose Nginx binds to localhost only and leaves
80/443 to the host Nginx:

```sh
NGINX_HTTP_PORT=127.0.0.1:8080
NGINX_HTTPS_PORT=127.0.0.1:8443
NGINX_CERTS_DIR=/opt/job-lighthouse/certs
```

Check both ports are free: `ss -tlnp | grep -E ':8080 |:8443 '` prints
nothing.

### 5. Container cert (self-signed)

Still as `deploy`:

```sh
install -d -m 700 /opt/job-lighthouse/certs
cd /opt/job-lighthouse/certs
openssl req -x509 -nodes -newkey rsa:2048 -days 3650 \
  -subj "/CN=localhost" -addext "subjectAltName=DNS:localhost,IP:127.0.0.1" \
  -keyout privkey.pem -out fullchain.pem
chmod 644 fullchain.pem && chmod 600 privkey.pem
```

Valid for 10 years. It only secures the loopback hop, so the host Nginx
doesn't verify it.

### 6. Host Nginx + Let's Encrypt

As root. Skip the install if Nginx and certbot are already there:

```sh
apt-get install -y nginx certbot python3-certbot-nginx
```

Save as `/etc/nginx/sites-available/job-lighthouse`:

```nginx
server {
    listen 80;
    listen [::]:80;
    server_name <domain>;

    location / {
        proxy_pass https://127.0.0.1:8443;
        proxy_http_version 1.1;
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Enable it and get the cert. `--nginx` answers the challenge through the
running Nginx, adds the `443` block, and renews without downtime:

```sh
ln -s /etc/nginx/sites-available/job-lighthouse /etc/nginx/sites-enabled/
nginx -t && systemctl reload nginx
certbot --nginx --redirect -d <domain>
certbot renew --dry-run
```

- If `/etc/nginx/nginx.conf` includes sites by exact path instead of
  `sites-enabled/*`, add `include /etc/nginx/sites-enabled/job-lighthouse;`
  next to the others.
- To add `<domain>` to an existing cert instead, pass `--cert-name <name>`
  and **every** domain it should cover. The list is replaced, not appended.
- `proxy_pass` must be **`https://`**127.0.0.1:8443. With `http://`, every
  request fails with `400 The plain HTTP request was sent to HTTPS port`.
- Until the first deploy, `https://<domain>` returns `502`. That's expected.

### 7. GitHub

Repo → Settings → Environments → create **`production`**, add these secrets:

| Secret | Value |
| --- | --- |
| `DROPLET_HOST` | droplet IP or `<domain>` |
| `DROPLET_USER` | `deploy` |
| `DROPLET_SSH_KEY` | contents of `deploy_key` (private key) |
| `DROPLET_KNOWN_HOSTS` | output of `ssh-keyscan -t ed25519 <exact DROPLET_HOST value>` (IP vs. domain must match; check the fingerprint against the droplet console) |

Then delete the local `deploy_key` files.

### 8. First deploy

Actions → **deploy** → *Run workflow* on `main`. Afterwards check:

```sh
curl https://<domain>/jobs      # reaches the Job Runner through Nginx
```

## Operations

On the droplet, always pass both env files (the image tag lives in
`image.env`, and there is no source to build from):

```sh
cd /opt/job-lighthouse
alias dc='docker compose --env-file .env --env-file image.env'
dc ps
dc logs -f job-runner
```

Host Nginx logs: `/var/log/nginx/access.log` and `error.log` (as root).

Which commit is running:

```sh
cat image.env
docker inspect --format '{{ index .Config.Labels "org.opencontainers.image.revision" }}' \
  "$(dc ps -q job-runner)"
```

## Rollback

A failed deploy turns the workflow red but does **not** roll back on its own:
the stack may be left on the broken image. `image.env` still names the last
good image, so the command below with `$(cut -d= -f2 image.env)` restores it.

**Preferred:** open an older **deploy** run in Actions → *Re-run all jobs*.
It redeploys that run's commit with the job's short-lived token, so no
credential is left on the droplet.

**By hand on the droplet** (as `deploy`), only if Actions is unavailable:

```sh
docker login ghcr.io -u <github-user>     # paste a read:packages token
bash /opt/job-lighthouse/deploy.sh ghcr.io/yogmel/job-lighthouse-backend:sha-<old-commit>
docker logout ghcr.io
```

- Docker stores the token **unencrypted** in `~/.docker/config.json` and it
  never expires there. Always `docker logout` afterwards.
- Use a token with **only** `read:packages` and a **short expiry** (e.g.
  7 days), and revoke it on GitHub when you're done.

Rolling back code does **not** roll back migrations. If the old code can't
run on the new schema, downgrade first with `dc run --rm migrate alembic
downgrade <rev>`.
