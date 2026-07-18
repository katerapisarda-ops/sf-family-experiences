#!/usr/bin/env bash
# Run after `npx expo export -p web` to inject PWA manifest + icon assets into dist/.
set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DIST="$SCRIPT_DIR/dist"
ASSETS="$SCRIPT_DIR/assets"

echo "Copying PWA icons..."
cp "$ASSETS/icon-192.png" "$DIST/icon-192.png"
cp "$ASSETS/icon-512.png" "$DIST/icon-512.png"

echo "Writing manifest.json..."
cat > "$DIST/manifest.json" <<'EOF'
{
  "name": "Little City",
  "short_name": "Little City",
  "description": "Family events in San Francisco",
  "start_url": "/",
  "display": "standalone",
  "background_color": "#FAFAFA",
  "theme_color": "#1E88E5",
  "icons": [
    {
      "src": "/icon-192.png",
      "sizes": "192x192",
      "type": "image/png",
      "purpose": "any maskable"
    },
    {
      "src": "/icon-512.png",
      "sizes": "512x512",
      "type": "image/png",
      "purpose": "any maskable"
    }
  ]
}
EOF

echo "Patching index.html..."
python3 - <<'PYEOF'
import re, pathlib
p = pathlib.Path("dist/index.html")
html = p.read_text()
inject = '''<link rel="manifest" href="/manifest.json" />
<link rel="apple-touch-icon" href="/icon-192.png" />
<meta name="apple-mobile-web-app-capable" content="yes" />
<meta name="apple-mobile-web-app-status-bar-style" content="default" />
<meta name="apple-mobile-web-app-title" content="Little City" />'''
if 'rel="manifest"' not in html:
    html = html.replace('<link rel="icon" href="/favicon.ico" /></head>',
                        f'<link rel="icon" href="/favicon.ico" />\n{inject}</head>')
    p.write_text(html)
    print("  Injected manifest + apple tags")
else:
    print("  Already patched, skipping")
PYEOF

echo "PWA patch complete. Ready to deploy: cd dist && vercel --prod"
