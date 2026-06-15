---
icon: lucide/globe
---

# Nginx

Nginx sits in front of the Daphne container. It terminates TLS, serves the static and media files
straight from disk, blocks obvious scanner traffic before it ever reaches Django, and proxies
everything else to Daphne. The example virtual host lives at
[`nginx/talks.example.com`](https://github.com/PioneersHub/pyconde-talks/blob/main/nginx/talks.example.com).
Copy it per site and substitute the domain.

## What the config does

### Upstream and proxy

Daphne is reached through a named upstream pointing at the container's published port:

```nginx
upstream django_talks_app {
    server 127.0.0.1:8000;
}
```

The catch-all `location /` proxies to that upstream over HTTP/1.1 with the `Upgrade`/`Connection`
headers set, so WebSocket connections (Daphne is an ASGI server) work. It forwards `Host`,
`X-Real-IP`, `X-Forwarded-For`, `X-Forwarded-Proto`, and `X-Forwarded-Host`, sets generous proxy
buffers, and uses 60-second connect/send/read timeouts.

!!! note "Bind only to localhost"

    `compose.yaml` publishes Daphne on `127.0.0.1:8000`, so the app is only reachable from Nginx on the
    same host. If you co-locate a second site, give it a different host port and a matching upstream
    here.

### TLS and Let's Encrypt

The HTTP server block (port 80) does nothing but redirect to HTTPS. The HTTPS block listens on 443
with `http2 on` and loads a Certbot-managed certificate:

```nginx
ssl_certificate /etc/letsencrypt/live/talks.example.com/fullchain.pem;
ssl_certificate_key /etc/letsencrypt/live/talks.example.com/privkey.pem;
include /etc/letsencrypt/options-ssl-nginx.conf;
ssl_dhparam /etc/letsencrypt/ssl-dhparams.pem;
```

The cipher suite and protocol options come from Certbot's `options-ssl-nginx.conf`, so they stay up
to date with the Certbot package rather than being hand-maintained here.

### Security headers

The server block sets the standard browser-hardening headers:

- `X-Content-Type-Options: nosniff`
- `X-Frame-Options: SAMEORIGIN`
- `X-XSS-Protection: 1; mode=block`
- `Referrer-Policy: strict-origin-when-cross-origin`
- `Strict-Transport-Security: max-age=63072000; includeSubDomains; preload`

A **Content-Security-Policy** is assembled from per-directive `set $csp` lines and added as one
header. It allows `'self'` plus the Vimeo, YouTube, and related CDN hosts that the embedded video
players and social-card images need (for example `*.vimeocdn.com`, `*.ytimg.com`,
`player.vimeo.com`, `youtube-nocookie.com`). The `script-src` directive includes `'unsafe-inline'`
and `'unsafe-eval'` because the embedded third-party players require them.

!!! warning "Headers are re-asserted inside the static location"

    Nginx's `add_header` does not inherit once a `location` block sets its own headers. The `/static/`
    location therefore re-declares `nosniff`, `X-Frame-Options`, `Referrer-Policy`, and
    `Strict-Transport-Security` so static assets are not served without them. If you add a header at the
    server level, add it inside `location /static/` too.

### Rate limiting and connection caps

Two zones are defined in the `http{}` context (they must live there, not inside `server{}`):

```nginx
limit_req_zone $binary_remote_addr zone=general:10m rate=10r/s;
limit_conn_zone $binary_remote_addr zone=conn:10m;
```

The request-rate zone allows 10 requests per second per IP, with a per-location burst:

| Location   | Burst              | Notes                                           |
| ---------- | ------------------ | ----------------------------------------------- |
| `/`        | `burst=10`         | The app; queued bursts are delayed, not dropped |
| `/static/` | `burst=30 nodelay` | Assets; a page load fetches many at once        |
| `/media/`  | `burst=20 nodelay` | Uploaded images                                 |

Every proxied and file-serving location also sets `limit_conn conn 10`, a per-IP concurrency cap.
HTTP/2 multiplexes over one TCP connection (a real browser uses one or two), so 10 leaves headroom
for API clients and WebSocket connections. Requests over the limit get a `429`.

### Blocking scanner traffic early

These checks run before any proxying, so blocked requests never reach Daphne (where even a 404 runs
auth middleware and a DB session lookup):

- Requests with an empty `User-Agent` get `444` (connection closed with no response). Real browsers
    and the Let's Encrypt ACME client always send one.
- Probe file extensions the app never serves (`.php`, `.env`, `.git`, `.sql`, `.bak`, `.yml`, ...)
    return `444`.
- Common exploit paths (WordPress, phpMyAdmin, VCS metadata, debug panels, `cgi-bin`, ...) return
    `444`. Django admin lives at `/admin/`, which none of these patterns match.

The static and media locations use the `^~` prefix match so legitimate asset URLs win over the regex
exploit-path patterns and are never tested against them.

### Compression

Gzip is on with `gzip_vary`, level 6, a 1000-byte minimum, and an explicit `gzip_types` list
covering CSS, JavaScript, JSON, SVG, XML, and font types. (Images and the already-minified video
embeds are not re-compressed.)

### Body size limit

```nginx
client_max_body_size 10m;
```

Talk images are uploaded through the Django admin and routinely exceed Nginx's 1 MB default, which
would otherwise reject them with an opaque `413` before reaching Daphne.

## How static files are served

Static files are served by Nginx directly from disk, not by Django:

```nginx
location ^~ /static/ {
    limit_conn conn 10;
    limit_req zone=general burst=30 nodelay;
    alias /var/cache/talks.example.com/staticfiles/;
    expires 30d;
    add_header Cache-Control "public, max-age=2592000, immutable";
    ...
}
```

The `alias` points at the per-target static cache directory that `ensure_permissions.sh` creates and
the deploy populates (see [Production deployment](index.md) and [CI/CD](ci-cd.md)). Assets are sent
with a 30-day expiry and `Cache-Control: public, max-age=2592000, immutable`. The `immutable` flag
is safe because every asset is content-hashed by `ManifestStaticFilesStorage`: a changed file gets a
new name, so caches never need to revalidate.

Media files are served from `/var/opt/talks.example.com/media/` with a matching 30-day expiry.

## Install steps

The example config is a complete virtual host. Install it per site:

```bash
sudo cp nginx/talks.example.com /etc/nginx/sites-available/${APP_DOMAIN}
sudo ln -s /etc/nginx/sites-available/${APP_DOMAIN} /etc/nginx/sites-enabled/
sudo certbot --nginx -d ${APP_DOMAIN}
sudo systemctl reload nginx
```

`certbot --nginx` obtains the Let's Encrypt certificate and writes the `ssl_certificate*` paths the
config expects. Run `sudo nginx -t` before reloading to catch syntax errors.

!!! danger "Add a catch-all default server"

    The `limit_req_zone`/`limit_conn_zone`/`map` directives at the top of the file belong in the
    `http{}` context, not inside a `server{}` block. Also make sure `/etc/nginx/sites-available/default`
    has catch-all `default_server` blocks for ports 80 and 443 that return `444`. Otherwise direct-IP
    and wrong-Host scans still reach Daphne.
