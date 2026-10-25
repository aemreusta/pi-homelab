# Homelab Design System

> **Pencil design dosyaları:** `design/homelab-homepage.pen`, `design/ntfy-notifications.pen` (not included in this repository)
>
> **Preview images:** `design/images/` (git-ignored; generated locally from the `.pen` files)
>
> Bu dosya hem tasarım sisteminin spec'ini hem de implementation snippet'larını içerir (önceki `implementation-notes.md` 2026-05-23'te buraya merge edildi).

## Uygulama Stratejisi

- **Homepage:** gethomepage.dev + custom CSS/JS (`custom.css.j2`, `custom.js.j2`) ile design system uygulanacak
- **Control Dashboard:** Full-page 2-column layout (`control-api.py.j2` embedded HTML)
- **ntfy:** Custom CSS override via volume mount (`/etc/ntfy/web/custom.css`)

---

## Color Tokens

### Trabzonspor Theme

| Token | Hex | Usage |
|-------|-----|-------|
| `--ts-bordo` | `#7B2040` | Primary accent, section headers, card glow start |
| `--ts-mavi` | `#1E3A8A` | Secondary accent, network headers, card glow end |
| `--ts-bordo-light` | `#9B3060` | Section icons, lighter accent |
| `--ts-mavi-light` | `#4B6BC4` | Network section icons, lighter accent |

### Base Palette (Dark Flat)

| Token | Hex | Usage |
|-------|-----|-------|
| `--bg-page` | `#0f172a` | Page/frame background |
| `--bg-overlay` | `#0f172aCC` | Content overlay on background image |
| `--bg-panel` | `#0f172aB3` | Glass panel backgrounds (70% opacity) |
| `--bg-card` | `#1e293bB3` | Card backgrounds (70% opacity) |
| `--bg-card-hover` | `#1e293bFF` | Card hover (100% opacity) |
| `--bg-input` | `#070c18` | Log area, code blocks |
| `--border` | `#334155` | Default borders |
| `--border-subtle` | `#33415540` | Panel/card borders (25% opacity) |
| `--text-primary` | `#e2e8f0` | Headings, card names, section labels |
| `--text-secondary` | `#94a3b8` | Stats, descriptions, resource bar |
| `--text-muted` | `#64748b` | Timestamps, metadata, tertiary |
| `--text-dim` | `#475569` | Placeholders, inactive elements |
| `--success` | `#22c55e` | Online, healthy, media notifications |
| `--warning` | `#f59e0b` | Restarting, caution, CPU high |
| `--danger` | `#ef4444` | Error, shutdown, critical alerts |
| `--info` | `#3b82f6` | Info alerts, links, update notifications |
| `--cyan` | `#22d3ee` | Terminal highlight, ntfy server |

## Typography

| Element | Font | Size | Weight | Tracking |
|---------|------|------|--------|----------|
| Page title | Syne | 22px (desktop) / 16px (mobile) | 700 | 4px / 2px |
| Section header text | Inter | 14px (desktop) / 12px (mobile) | 800 | 3px / 2px |
| Panel title | Inter | 13px | 800 | 3px |
| Card name | Inter | 13px | 500 | 0 |
| Card stat | Inter | 11px | 400 | 0 |
| Resource bar | JetBrains Mono | 11px | 500 | 0 |
| Metric value | JetBrains Mono | 16-24px | 700 | 0 |
| Date/weather | Inter | 11px | 500 | 0 |
| Button label | DM Sans | 13px | 600 | 0 |
| Log text | JetBrains Mono | 10px | 400 | 0 |
| Alert title | DM Sans | 12px | 600 | 0 |
| Alert desc | DM Sans | 10px | 400 | 0 |
| Notif title | Inter | 14px | 700 | 0 |
| Notif body | Inter | 12px | 400 | 0, lineHeight 1.5 |
| Priority badge | JetBrains Mono | 9px | 700 | 1px |
| Service name | JetBrains Mono | 12px | 500 | 0 |

## Shared Header (all pages)

```text
[HOMELAB] | [resource bar] ... [date/weather] [TS Crest 44px]
                                                 |
                                            hover/click
                                       +---------------+
                                       | Settings      |
                                       | Restart       |
                                       | Shutdown      |
                                       | Update        |
                                       | Clean         |
                                       +---------------+
```

- Height: 64px
- TS logo: `https://cdn.trabzonspor.org.tr/trabzonspor_2bf6f45f5cc14cbe815f9466832042c9.png` (44x44, cornerRadius 20)
- Header bg: `#0f172aE6` (90% opacity)
- Bottom border: bordo->mavi linear-gradient 90deg, 2px
- Dividers: 1px `#334155` vertical lines between header groups

### Shared Header HTML

```html
<header class="homelab-header">
  <div class="header-left">
    <span class="title">HOMELAB</span>
    <span class="divider"></span>
    <span class="resource-bar">94% CPU · 5.7 GB · 744 GB · 52°C · 3d 21h</span>
  </div>
  <div class="header-right">
    <span class="date-weather">Mar 12, 14:37 · Istanbul 10°C</span>
    <div class="ts-crest-wrap">
      <img src="https://cdn.trabzonspor.org.tr/trabzonspor_2bf6f45f5cc14cbe815f9466832042c9.png"
           class="ts-crest" alt="TS" />
      <div class="ts-dropdown" hidden>
        <button data-action="settings">Settings / Control</button>
        <button data-action="restart">Restart</button>
        <button data-action="shutdown">Shutdown</button>
        <button data-action="update">Update</button>
        <button data-action="clean">Clean</button>
        <button data-action="junk">Junk Scan</button>
      </div>
    </div>
  </div>
</header>
```

### Shared Header CSS

```css
.homelab-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: 64px;
  padding: 0 40px;
  background: rgba(15, 23, 42, 0.9);
  border-bottom: 2px solid;
  border-image: linear-gradient(90deg, #7B2040, #1E3A8A) 1;
}

.header-left, .header-right {
  display: flex;
  align-items: center;
  gap: 20px;
}

.divider {
  width: 1px;
  height: 24px;
  background: #334155;
}

.title {
  font-family: 'Syne', sans-serif;
  font-size: 22px;
  font-weight: 700;
  letter-spacing: 4px;
  color: #e2e8f0;
}
```

### TS Crest Dropdown Behavior

- **Hover (desktop)**: Crest glows with bordo shadow, dropdown appears after 200ms delay
- **Click (mobile)**: Toggles dropdown visibility
- **Dropdown**: glassmorphism bg, bordo-mavi gradient top border, 8px corner radius
- **Button hover in dropdown**: bg shifts to `--bg-card-hover`, left border accent

## Section Headers (improved readability)

```text
[icon 16px] [SECTION NAME white] [--- gradient line ----->]
```

- Icon: lucide, colored per section (bordo for media/infra, blue for network)
- Text: `#e2e8f0` white (NOT colored) for maximum readability
- Gradient line: section color at 50% -> transparent, fills remaining width
- Gap: 12px (desktop), 10px (mobile)

### Section Icons

| Section | Icon | Color |
|---------|------|-------|
| Media | `film` | `#9B3060` |
| Network | `network` | `#4B6BC4` |
| Infrastructure | `server` | `#9B3060` |

### Section Header CSS

```css
.section-header {
  display: flex;
  align-items: center;
  gap: 12px;
  width: 100%;
}

.section-header .icon {
  width: 16px;
  height: 16px;
  color: var(--section-color); /* bordo or blue */
}

.section-header .label {
  font-family: 'Inter', sans-serif;
  font-size: 14px;
  font-weight: 800;
  letter-spacing: 3px;
  color: #e2e8f0; /* white, NOT section color */
  text-transform: uppercase;
  white-space: nowrap;
}

.section-header .line {
  flex: 1;
  height: 1px;
  background: linear-gradient(
    90deg,
    color-mix(in srgb, var(--section-color) 50%, transparent),
    transparent
  );
}
```

## Card Design

### Standard Service Card

- Background: `--bg-card` (glassmorphism over page background)
- Border: 1.5px, bordo->mavi gradient at 135deg, 40% opacity
- Corner radius: 8px
- Height: 68px (desktop) / 56px (mobile)
- Padding: 0 16px
- Hover: scale(1.02), border glow animation activates

### CSS Rotating Glare Border Effect

Each service card (Portainer, Beszel, etc.) uses a `@property`-based conic gradient that rotates continuously with bordo-mavi flow. The effect is subtle at rest (0.4 opacity) and intensifies on hover (0.9) and click (1.0).

```css
/* Register custom property for animation */
@property --border-angle {
  syntax: "<angle>";
  initial-value: 0deg;
  inherits: false;
}

.card {
  position: relative;
  background: var(--bg-card);
  border-radius: 8px;
  overflow: hidden;
  --glow-opacity: 0.4;
  cursor: pointer;
}

.card::before {
  content: '';
  position: absolute;
  inset: -2px;
  border-radius: 10px;
  background: conic-gradient(
    from var(--border-angle),
    #7B2040,      /* bordo */
    #1E3A8A,      /* mavi */
    #7B2040       /* bordo again for seamless loop */
  );
  opacity: var(--glow-opacity);
  z-index: -1;
  animation: rotate-border 4s linear infinite;
}

.card::after {
  content: '';
  position: absolute;
  inset: 2px;
  border-radius: 6px;
  background: var(--bg-card);
  z-index: -1;
}

@keyframes rotate-border {
  to { --border-angle: 360deg; }
}

/* Hover: glow intensifies + subtle lift */
.card:hover {
  --glow-opacity: 0.9;
  transform: translateY(-2px) scale(1.02);
  transition: transform 0.2s ease, --glow-opacity 0.3s ease;
}

/* Active/click: brief pulse with bordo-mavi flash */
.card:active {
  --glow-opacity: 1;
  transform: translateY(0) scale(0.98);
}
```

### Fallback for Browsers Without @property

```css
@supports not (background: conic-gradient(red, blue)) {
  .card {
    border: 2px solid transparent;
    border-image: linear-gradient(135deg, #7B2040, #1E3A8A) 1;
  }
}
```

### Performance Notes

- `@property` animation is GPU-accelerated in Chromium/WebKit
- Use `will-change: opacity, transform` on cards with the effect
- Limit to ~20 visible cards on screen (our use case fits)
- `animation-play-state: paused` when card is outside viewport (IntersectionObserver)

## Control Dashboard

### Layout

The control dashboard is a full-page view accessible from the TS crest dropdown or direct URL. It shares the same header and background image as the homepage.

- Full 1440x900 matching homepage dimensions
- Same background image as homepage with `#0f172aCC` overlay
- Two-column layout: left (expandable) + right (380px fixed)
- 28px gap between columns, 40px horizontal padding, 32px top padding

```text
+------------------------------------------------------------------+
| HOMELAB | stats | .................... | date/weather | [TS logo] |
|================================================================= |
| [System Monitoring - 4 metric cards]     | Running Services (8)   |
|                                          |   jellyfin  Up 14d     |
| [Quick Actions - 6 buttons]             |   sonarr    Up 14d     |
|   Restart  Shutdown  Update              |   traefik   Up 14d     |
|   Clean    Junk Scan  Notifications      |   qbit      Restarting |
|                                          |   ...                  |
| [System Logs]                           |--------------------------|
|   source: [system v] [50] [>]           | Recent Alerts (5)       |
|   +----------------------------------+  |   ! qBittorrent restart |
|   | 09:14:21 kernel: usb device      |  |   x Disk usage >80%    |
|   | 09:14:22 systemd: docker.svc     |  |   i Watchtower updated |
|   | 09:15:03 qbit: Connection lost   |  |   v Junk scan complete |
|   +----------------------------------+  |   * ntfy started       |
+------------------------------------------------------------------+
```

### Panels (glassmorphism)

- Background: `#0f172aB3` (70% opacity)
- Border: 1px `#33415540`
- Corner radius: 12px
- Padding: 20px
- `backdrop-filter: blur(12px)`

### System Monitoring

4 metric cards in horizontal grid (CPU orange/red, RAM blue/purple, Disk green, Temp centered icon + value):

```css
.metric-card {
  background: rgba(30, 41, 59, 0.7);
  border: 1.5px solid;
  border-image: linear-gradient(135deg, #7B204060, #1E3A8A60) 1;
  border-radius: 10px;
  padding: 16px 18px;
}

.metric-card .value {
  font-family: 'JetBrains Mono', monospace;
  font-weight: 700;
}

/* Color per metric */
.metric-card.cpu .value { color: #f59e0b; }
.metric-card.ram .value { color: #3b82f6; }
.metric-card.disk .value { color: #22c55e; }
.metric-card.temp .value { color: #f59e0b; }

/* Progress bar gradients */
.cpu .progress-fill { background: linear-gradient(90deg, #f59e0b, #ef4444); }
.ram .progress-fill { background: linear-gradient(90deg, #3b82f6, #8b5cf6); }
.disk .progress-fill { background: linear-gradient(90deg, #22c55e, #16a34a); }
```

### Quick Actions

Two rows of buttons:

- Row 1: Restart (warning), Shutdown (danger), Update (info)
- Row 2: Clean (neutral), Junk Scan (neutral), Notifications (bordo)
- Each: 44px height, cornerRadius 10, colored fill/stroke per action type

### Services List

- Service rows: 8px padding, gradient bordo->mavi border at 30% opacity
- Status dot: 8px ellipse, colored per state
- Name: JetBrains Mono 12px, #e2e8f0
- Status text: JetBrains Mono 10px, colored per state

### System Logs Terminal

- Dark terminal: `#070c18` bg, 1px `#1e293b` border
- Log lines: JetBrains Mono 10px, color-coded per level
  - Green `#4ade80`: kernel, success
  - Default `#94a3b8`: info
  - Muted `#64748b`: cron, background
  - Red `#ef4444`: errors
  - Cyan `#22d3ee`: ntfy

## Notification Cards (ntfy)

### Alert Types (compact, in control dashboard)

| Type | Icon (lucide) | Color | Bg Alpha |
|------|--------------|-------|----------|
| Error | `circle-x` | `#ef4444` | `#ef444410` |
| Warning | `triangle-alert` | `#f59e0b` | `#f59e0b10` |
| Info | `info` | `#3b82f6` | `#3b82f610` |
| Success | `circle-check` | `#22c55e` | `#22c55e10` |
| ntfy | `bell` | `#9B3060` | `#7B204010` |

### Compact Alert Card (control dashboard)

```text
[icon 16px] [title 12px + description 10px] [timestamp]
```

- Border: 1px, color at 15% opacity
- Corner radius: 8px
- Padding: 10px 12px
- Gap: 10px

### Rich Notification Card (ntfy page)

```text
+-------------------------------------------------------+
| [icon 18px] [title 14px bold]  [priority badge]  time |
| description text 12px, line-height 1.5                 |
| [topic tag]  [action btn 1]  [action btn 2]           |
+-------------------------------------------------------+
```

- Background: `#0f172aB3` with `backdrop-filter: blur(8px)`
- Border: 1px, colored per type at 20% opacity
- Corner radius: 12px
- Padding: 18px 20px
- Priority badge: colored bg, JetBrains Mono 9px uppercase
- Topic tag: colored bg/border, JetBrains Mono 10px
- Action buttons: colored bg at 20% opacity, Inter 10px 600

### ntfy Notification Card CSS

```css
.notif-card {
  background: rgba(15, 23, 42, 0.7);
  backdrop-filter: blur(8px);
  border: 1px solid var(--notif-color-30);
  border-radius: 12px;
  padding: 18px 20px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.notif-card .priority-badge {
  font-family: 'JetBrains Mono', monospace;
  font-size: 9px;
  font-weight: 700;
  letter-spacing: 1px;
  text-transform: lowercase;
  padding: 2px 8px;
  border-radius: 6px;
  background: var(--notif-color-25);
  color: var(--notif-color);
}

.notif-card .topic-tag {
  font-family: 'JetBrains Mono', monospace;
  font-size: 10px;
  padding: 4px 10px;
  border-radius: 6px;
  background: var(--notif-color-15);
  border: 1px solid var(--notif-color-20);
  color: var(--notif-color);
}

.notif-card .action-btn {
  font-family: 'Inter', sans-serif;
  font-size: 10px;
  font-weight: 600;
  padding: 4px 10px;
  border-radius: 6px;
  cursor: pointer;
  transition: opacity 0.2s;
}

.notif-card .action-btn:hover {
  opacity: 0.8;
}
```

### ntfy Topics

| Topic | Color | Trigger |
|-------|-------|---------|
| `homelab-alerts` | `#ef4444` | Container crash, disk >80%, service down |
| `homelab-updates` | `#3b82f6` | Watchtower image pull, system update |
| `homelab-media` | `#22c55e` | New media added, download complete |

### ntfy Integration Architecture

- ntfy server runs as Docker container alongside existing services
- Port: 2586 (internal), exposed via Traefik at `ntfy.example`
- Topics: `homelab-alerts`, `homelab-updates`, `homelab-media`
- Control API forwards alerts to ntfy on: container restart, disk threshold, update events
- Homepage polls ntfy API for recent notifications (SSE or periodic fetch)
- Custom CSS override via volume mount at `/etc/ntfy/web/custom.css`
- Dark theme matching homelab palette (#0f172a base), TS bordo-mavi accent on subscription cards

## Alternative Backgrounds

### Teal Glow Gradient

Açık tema veya landing page arka planları için kullanılabilecek radial gradient:

```css
.teal-glow-bg {
  background-image: radial-gradient(125% 125% at 50% 10%, #ffffff 40%, #14b8a6 100%);
  background-size: 100% 100%;
}
```

React/Tailwind kullanımı:

```jsx
<div className="min-h-screen w-full bg-white relative">
  <div
    className="absolute inset-0 z-0"
    style={{
      backgroundImage: 'radial-gradient(125% 125% at 50% 10%, #ffffff 40%, #14b8a6 100%)',
      backgroundSize: '100% 100%',
    }}
  />
  {/* Content */}
</div>
```

## Responsive Breakpoints

| Breakpoint | Layout |
|-----------|--------|
| Desktop >=1440px | 4-column card grid, 2-column control dashboard |
| Tablet ~768px | 2-column card grid, stacked dashboard |
| Mobile <=375px | 1-column cards, stacked sections |
