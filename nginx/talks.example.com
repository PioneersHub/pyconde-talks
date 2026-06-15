# ─── http{} context directives ────────────────────────────────────────────────────────────────────
# These must be included in the http{} block (e.g. via /etc/nginx/conf.d/ or a top-level include),
# NOT inside a server{} block.
#
# This file only covers the talks.example.com virtual host.
# Make sure /etc/nginx/sites-available/default has catch-all default_server blocks for ports 80 and
# 443 that return 444, otherwise direct-IP and wrong-Host scans still reach Daphne.

# Rate limiting: 30 req/s per IP with configurable burst per location.
# This cannot be too low, because many attendees share the same venue NAT IP.
limit_req_zone $binary_remote_addr zone=general:10m rate=30r/s;
limit_req_status 429;

# Per-IP concurrency cap.
# HTTP/2 multiplexes over one TCP connection, so a real browser uses 1-2 connections; 10 leaves
# headroom for API clients / websockets.
limit_conn_zone $binary_remote_addr zone=conn:10m;
limit_conn_status 429;

# Drop requests with an empty User-Agent.
# All real browsers and the Let's Encrypt ACME client send one.
map $http_user_agent $bad_ua {
    default 0;
    "" 1;
}

# Django/Daphne upstream.
upstream django_talks_app {
    server 127.0.0.1:8000;
}

# ─── HTTP -> HTTPS redirect ───────────────────────────────────────────────────────────────────────
server {
    listen 80;
    listen [::]:80;
    server_name talks.example.com;

    location / {
        return 301 https://$host$request_uri;
    }
}

# ─── HTTPS virtual host ───────────────────────────────────────────────────────────────────────────
server {
    listen 443 ssl;
    listen [::]:443 ssl;
    http2 on;
    server_name talks.example.com;

    # SSL Configuration (Managed by Certbot)
    ssl_certificate /etc/letsencrypt/live/talks.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/talks.example.com/privkey.pem;
    include /etc/letsencrypt/options-ssl-nginx.conf;
    ssl_dhparam /etc/letsencrypt/ssl-dhparams.pem;

    # Talk images are uploaded through the Django admin and routinely exceed nginx's 1 MB
    # default, which would otherwise reject them with an opaque 413 before reaching Daphne.
    client_max_body_size 10m;

    # ── Security headers ──────────────────────────────────────────────────────────────────────────
    add_header X-Content-Type-Options nosniff;
    add_header X-Frame-Options SAMEORIGIN;
    add_header X-XSS-Protection "1; mode=block";
    add_header Referrer-Policy strict-origin-when-cross-origin;
    # ── Content-Security-Policy ───────────────────────────────────────────────────────────────────
    set $csp "default-src 'self';";
    set $csp "${csp} script-src 'self' 'unsafe-inline' 'unsafe-eval' vimeo.com *.vimeo.com *.vimeocdn.com youtube.com *.youtube.com *.ytimg.com *.googlevideo.com *.newrelic.com *.nr-data.net;";
    set $csp "${csp} style-src 'self' 'unsafe-inline' *.vimeocdn.com *.ytimg.com;";
    set $csp "${csp} img-src 'self' data: vimeo.com *.vimeo.com *.vimeocdn.com youtube.com *.youtube.com *.ytimg.com *.ggpht.com;";
    set $csp "${csp} font-src 'self' data:;";
    set $csp "${csp} connect-src 'self' vimeo.com *.vimeo.com youtube.com *.youtube.com *.googlevideo.com;";
    set $csp "${csp} frame-src 'self' vimeo.com *.vimeo.com player.vimeo.com *.player.vimeo.com youtube.com www.youtube.com youtube-nocookie.com speech.phont.ai;";
    set $csp "${csp} child-src 'self' vimeo.com *.vimeo.com *.vimeocdn.com youtube.com *.youtube.com speech.phont.ai;";
    add_header Content-Security-Policy $csp;
    add_header Strict-Transport-Security "max-age=63072000; includeSubDomains; preload";

    # ── Compression ───────────────────────────────────────────────────────────────────────────────
    gzip on;
    gzip_vary on;
    gzip_proxied any;
    gzip_comp_level 6;
    gzip_min_length 1000;
    gzip_types application/atom+xml application/javascript application/json application/ld+json application/manifest+json application/rss+xml application/vnd.geo+json application/xml font/eot font/otf font/ttf image/svg+xml text/css text/javascript text/plain text/xml;

    # ── Early-reject junk requests ────────────────────────────────────────────────────────────────
    # These checks run before any proxying, so blocked requests never reach Daphne
    # (where even a 404 runs auth middleware + DB session lookup).

    # Empty User-Agent: primitive scanners. See the map{} block above.
    if ($bad_ua) {
        return 444;
    }

    # Probe file extensions that this app never serves.
    location ~* \.(php|aspx?|jsp|cgi|env|git|sql|bak|old|orig|save|swp|ini|sh|conf|yml|yaml|toml|log|DS_Store)$ {
        return 444;
    }

    # CMS admin panels, package managers, VCS metadata, and other paths that only appear in
    # automated exploit scanners. Django admin is at /admin/, which none of these patterns match.
    location ~* ^/(wp-admin|wp-login|wp-content|wp-includes|wordpress|xmlrpc\.php) {
        return 444;
    }
    location ~* ^/(phpmyadmin|pma|adminer|dbadmin|myadmin|mysql|sql) {
        return 444;
    }
    location ~* ^/(\.git|\.env|\.svn|\.hg|\.DS_Store|\.well-known/security\.txt) {
        return 444;
    }
    location ~* ^/(vendor/|node_modules/|composer\.|package\.json|Gruntfile|Makefile|Rakefile) {
        return 444;
    }
    location ~* ^/(cgi-bin|scripts/|shell|console|setup\.php|install\.php|config\.php) {
        return 444;
    }
    location ~* ^/(telescope|_ignition|_profiler|__debug__|debug/default/view|elmah\.axd) {
        return 444;
    }

    # ── Static files ──────────────────────────────────────────────────────────────────────────────
    # "^~" makes this a prefix match that wins over the regex locations above, so asset URLs are
    # never tested against the exploit-path patterns.
    location ^~ /static/ {
        limit_conn conn 10;
        limit_req zone=general burst=30 nodelay;
        alias /var/cache/talks.example.com/staticfiles/;
        expires 30d;
        add_header Cache-Control "public, max-age=2592000, immutable";
        # nginx's add_header does NOT inherit once a location sets its own, so the server-level
        # security headers above are otherwise dropped for every static response. Re-assert the
        # ones that matter for assets (nosniff above all) so they are not served bare.
        add_header X-Content-Type-Options nosniff;
        add_header X-Frame-Options SAMEORIGIN;
        add_header Referrer-Policy strict-origin-when-cross-origin;
        add_header Strict-Transport-Security "max-age=63072000; includeSubDomains; preload";
    }

    # ── Media files ───────────────────────────────────────────────────────────────────────────────
    location ^~ /media/ {
        limit_conn conn 10;
        limit_req zone=general burst=20 nodelay;
        alias /var/opt/talks.example.com/media/;
        expires 30d;
    }

    # ── Django/Daphne app ─────────────────────────────────────────────────────────────────────────
    location / {
        # Per-IP concurrency: HTTP/2 multiplexes over one connection, so a real browser uses 1-2; 10
        # leaves room for API clients and websockets.
        limit_conn conn 10;
        limit_req zone=general burst=10;
        proxy_pass http://django_talks_app;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_redirect off;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-Host $server_name;

        # Buffer settings for better performance
        proxy_buffer_size 128k;
        proxy_buffers 4 256k;
        proxy_busy_buffers_size 256k;

        # Timeouts
        proxy_connect_timeout 60s;
        proxy_send_timeout 60s;
        proxy_read_timeout 60s;
    }
}
