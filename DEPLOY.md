# Running the backend on a server

The phone app is a thin client. Everything it shows comes from `serve.py`, so
"put the app on a server" means putting **this backend** somewhere always-on
and pointing the app at it. Your laptop then stops mattering.

## What I cannot do for you

**Provisioning the server is yours.** Creating a hosting account and entering
payment details is not something I'll do on your behalf — that's an account in
your name and a card you're responsible for. Everything after "you have an SSH
prompt" is below.

## Sizing, measured not guessed

Taken from this stack running all six timeframes with a live collector:

| | |
|---|---|
| API memory | **~300 MB** resident |
| Collector memory | ~12 MB |
| Disk | **111 MB** of bars + 1 MB of models, growing ~30 MB/month at 1m |
| CPU, steady state | a few percent of one core |
| CPU, per 1m bar close | ~3 s to recompute features |

**One shared vCPU and 1 GB RAM is enough.** That is the cheapest tier almost
everywhere (~€4/month). 512 MB would fit today but leaves nothing for the
feature recompute spike.

Nothing here needs an API key, an exchange account, or a GPU. It reads public
Binance endpoints and public SEC filings.

## Where to rent it

Any provider with a €4/month tier works; nothing here is provider-specific.
Two that fit:

- **Hetzner Cloud** (`console.hetzner.com`) — CX22, ~€4/month for 2 vCPU and
  4 GB, which is comfortably more than the measurements above need. Cheapest
  of the credible options. New accounts are sometimes asked for ID before the
  first server can be created.
- **DigitalOcean** (`cloud.digitalocean.com`) — $6/month for 1 vCPU and 1 GB.
  More expensive for less machine, but signup is usually instant.

Pick **Debian 12** or **Ubuntu 24.04**, add your SSH key during creation
rather than using a root password, and choose a region near you — Falkenstein
or Helsinki for Hetzner, Frankfurt or Amsterdam for DigitalOcean. Latency only
affects how fast the app feels; the collector does not care.

## Steps

On a fresh Debian/Ubuntu box:

```bash
curl -fsSL https://get.docker.com | sh          # docker + compose plugin
```

### Getting the code there

**Copy it from your laptop.** One command, no GitHub credentials on the
server, and it brings the bars and the already-validated models with it, so
there is no training run to wait for:

```bash
rsync -avz --exclude .git --exclude mobile --exclude app_idea \
      --exclude __pycache__ --exclude .env \
      ~/Desktop/projects/TradingBot/ root@SERVER_IP:/root/tradingbot/
```

~113 MB, a couple of minutes. `mobile/` is excluded because it is 390 MB of
Flutter build output the server has no use for.

The alternative is `git clone`, which needs a deploy key or a personal access
token on the server for a private repo — **and only works if the deployment
work is committed and pushed first.** `data_cache/` and `output/` are
gitignored either way, so a clone arrives with no bars and no models and you
must train on the server:

```bash
docker compose build
docker compose run --rm api python3 train.py     # ~111 MB download, fits all twelve
```

### Starting it

```bash
cd /root/tradingbot
cp .env.example .env
sed -i "s/replace-me/$(openssl rand -hex 32)/" .env    # a real token
cat .env                                                # copy it, the app needs it

docker compose up -d --build
docker compose logs -f api
```

The collector keeps every interval current from then on.

## How it is actually deployed (2026-08-29)

Public HTTPS, no VPN. Caddy terminates TLS on 443 with a Let's Encrypt
certificate and proxies to the API on loopback; `BIND_ADDR=127.0.0.1` in
`.env` means Caddy is the only route in and port 8787 is closed from outside.

The hostname is `165.232.127.165.sslip.io` — sslip.io resolves any IP embedded
in the name back to that IP, which gets a real certificate with no domain
purchase. **It also embeds the server address, so moving the droplet changes
the URL and breaks every installed app.** Buy a real domain before shipping
this to anyone else; it is a one-line change in the Caddyfile and a rebuild of
the app.

Because the login endpoint is now reachable by anyone, `api/throttle.py`
limits sign-in attempts per IP *and* per identifier — the second is what stops
a botnet spread across many addresses grinding one account. Caddy passes
`X-Forwarded-For`, and the API trusts that header only from loopback.

### Deploying a code change — read this first

`api/` is **not** a bind mount. The code is baked into the image by
`COPY . .`, so:

```bash
rsync -az api/ bot:/root/tradingbot/api/
ssh bot 'cd /root/tradingbot && docker compose up -d --build'
```

Without `--build` the container restarts on the *old* image and the change
silently does not exist. That has already happened once here: rate limiting
was rsynced, the container restarted, and twelve wrong passwords in a row
were still accepted at full speed.

## The older option: Tailscale

The compose file publishes the port on **127.0.0.1 only**, deliberately.
Binding `0.0.0.0` in Docker writes iptables rules that bypass `ufw`, so a
"firewalled" box would be serving the internet anyway.

### Tailscale — recommended

No public port, no domain, no certificate, and the token never crosses the
open internet.

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
tailscale ip -4          # e.g. 100.x.y.z
```

Install Tailscale on the phone, sign into the same account, and point the app
at `http://100.x.y.z:8787`. The server stays invisible to everyone else.

Set `BIND_ADDR` in `.env` to that `100.x.y.z` address and
`docker compose up -d` again, so the container is published on the Tailscale
interface rather than only on loopback.

**Bind to the tailnet IP, not `0.0.0.0`.** They look interchangeable and are
not: `0.0.0.0` publishes port 8787 on the box's *public* address too, and
because Docker writes its own iptables rules, `ufw` will not stop it. The
machine would be serving the internet while every firewall command you ran
reports that it is closed.

### A public domain with HTTPS

Only if you actually want it reachable from anywhere. Put Caddy in front — it
gets and renews a certificate on its own:

```
# /etc/caddy/Caddyfile
bot.example.com {
    reverse_proxy 127.0.0.1:8787
}
```

Point an A record at the server, `systemctl reload caddy`, and use
`https://bot.example.com` in the app.

**Do not skip the TLS step.** The token is a bearer credential: over plain
`http://` it travels in clear text and anyone on the path can lift it and
replay it.

## Never rsync over the server's `.env`

```bash
rsync -az --delete \
  --exclude '.git' --exclude '.env' \
  --exclude 'mobile' --exclude 'data_cache' \
  --exclude '__pycache__' --exclude 'output' \
  ./ bot:/root/tradingbot/
```

**`--exclude '.env'` is not optional.** The file exists only on the server —
it is gitignored, so a local checkout has nothing to sync in its place, and
`--delete` removes it. The containers keep running on the environment they
already loaded, so nothing breaks until the next `docker compose` command,
which then fails with:

```
error while interpolating services.api.environment.TRADINGBOT_TOKEN:
required variable TRADINGBOT_TOKEN is missing a value
```

If it does happen and the containers are still up, the values are recoverable
from the running process rather than lost:

```bash
docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' tradingbot-api-1 \
  | grep -E '^(TRADINGBOT_TOKEN|BIND_ADDR)=' > /root/tradingbot/.env
chmod 600 /root/tradingbot/.env
```

Do that **before** restarting anything. Once the containers are down the
token is gone and every device holding a bearer credential has to be
re-issued one.

## Pointing the app at it

Profile → **API host** → enter the URL, then **Bearer token** → paste the
token from `.env`. Both are stored on the device.

To bake them into a build instead:

```bash
flutter run \
  --dart-define=API_BASE=https://bot.example.com \
  --dart-define=API_TOKEN=<token>
```

## What the token does and does not protect

It gates reads. There is nothing here that trades, holds keys, or moves money,
so the worst a leaked token yields is your market analysis — plus somebody
else burning your Binance rate limit.

`serve.py` **refuses to start** bound to anything but loopback without one.
That is deliberate: a process that looks healthy while quietly serving the
whole internet is not a failure anyone notices by looking at it.

## Honest status of the container

The Dockerfile and compose file are written and structurally checked, but
**Docker is not installed on the machine I built them on, so the image has
never been built or run.** The commands inside them are the same ones running
natively here, and `libgomp1` is included because the frozen judges are
LightGBM models that will not import without it — but expect to debug the
first `docker compose build` rather than assuming it is clean.

## Notifications that reach a closed phone

The app's own background polling cannot be relied on for this, and that is not
a bug anyone can fix in the app. On iOS a `BGAppRefreshTask` runs when iOS
decides — a few times a day at best, and never once the app is swiped out of
the switcher or the phone is in Low Power Mode. The only thing that wakes an
iOS app on someone else's schedule is APNs, whose entitlement needs a paid
Apple Developer account.

So the server relays instead. `api/push.py` hands each new alert to
[ntfy](https://ntfy.sh) — free, no account — whose own iOS and Android apps
hold real push entitlements. Set-up is entirely in the app: **Profile →
Delivery with the app closed**, which generates a random topic, registers it,
and offers a link that subscribes the ntfy app to it.

Things worth knowing:

* **The topic is the password.** ntfy has no accounts, so anyone who knows a
  topic can read it and post to it. Topics are 128 bits from a cryptographic
  generator and are stored `0600` in `data_cache/push.v1.json`. What a leaked
  topic exposes is the alert text — which pairs are watched and what the model
  said. Not the account, not the token, not a position.
* **The filtering settings live on the phone and are shipped up.** Sensitivity,
  news level and the mute list are re-registered whenever they change, so the
  lock screen and the dashboard cannot disagree. See the fingerprint in
  `mobile/lib/api/push.dart`.
* **Both paths still run.** The relay does not replace the app's polling. The
  server records which alert ids it managed to push and reports that back on
  `/api/alerts`, so the app skips a notification that already arrived — and a
  push that FAILED reports false, so the app notifies as it always did. There
  is no state in which both go quiet.
* **`PUSH_SERVER`** in `.env` points the relay at a self-hosted ntfy instead of
  the public one. The public server sees the topic and the alert text.

```bash
# is anything registered, and has it been failing?
docker compose exec api python3 -c \
  "from api.push import get_relay; print(get_relay().count())"
docker compose logs --tail=50 api | grep -i push
```

## Keeping it fed

```bash
docker compose ps                    # both containers healthy?
docker compose logs --tail=50 collector
docker compose exec collector python3 collect.py --status \
  --interval 1m --interval 1h        # gaps?
```

A collector that is silently failing is worse than one that is loudly down —
`--status` reports gaps, and the app shows `STALE` rather than presenting an
expired window as current.
