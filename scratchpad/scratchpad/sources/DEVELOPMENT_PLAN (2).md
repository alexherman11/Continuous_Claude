# Agora — Development Plan & Code Architecture

> **Agora is not an app. Agora is a kernel that runs apps — and the first 200 users are the developer's actual friends.**
>
> This document is the single, complete reference for the Agora project. It defines the full architecture — kernel, micro apps, message bus, sandbox runtime, design system, agent constellation, canvas/widget slots, external app wrapping — and the development sequencing, launch strategy, and feature prioritization built around a friends-first launch in San Luis Obispo.
>
> The architecture is a React Native app with a Python/FastAPI backend. The primary interface is a spatial map canvas. A thin kernel provides identity, a typed pub/sub message bus, a capability-based permission system, sandboxed WebView app runtimes, scoped encrypted storage, and mesh transport. Every feature — first-party or user-created — is a micro app described by a manifest, running in a sandbox, communicating only through the message bus.
>
> The intelligence layer — attention scoring, action extraction, morning briefings, civic proximity alerts, deliberation observation, bridging detection — is built as a constellation of specialized AI agents on the message bus. Each agent is a headless micro app with a manifest, declared capabilities, and a sandbox. One central orchestrator triages events; specialist agents wake on interrupt to handle specific tasks.
>
> Two extension slot types — **canvas** (full-screen non-spatial workspaces) and **widget** (persistent glanceable units on the map) — break the map-only ceiling. An **external app integration layer** brings third-party web apps into the ecosystem via sandboxed WebView wrappers with bridge adapters.
>
> **Launch thesis:** Lead with AI-powered daily life utility. Let friends build community features organically. Defer formal governance until the community outgrows personal trust. Grow through viral utility — tools people build for themselves that their friends want to fork — not through marketing or ideology.

---

## Table of Contents

1. [Project-Wide Architecture](#1-project-wide-architecture)
2. [The Agora Kernel](#2-the-agora-kernel)
3. [The Shared Foundation (Design System)](#3-the-shared-foundation-design-system)
4. [The App Manifest & Extension Slots](#4-the-app-manifest--extension-slots)
5. [The Message Bus](#5-the-message-bus)
6. [The App Runtime & Sandbox](#6-the-app-runtime--sandbox)
7. [The Map Shell](#7-the-map-shell)
8. [First-Party Micro Apps](#8-first-party-micro-apps)
9. [The App Creation Pipeline](#9-the-app-creation-pipeline)
10. [Backend Services](#10-backend-services)
11. [Community Taxonomy](#11-community-taxonomy)
12. [Development Phases & Sequencing](#12-development-phases--sequencing)
13. [The Transition Map](#13-the-transition-map)

---

## 1. Project-Wide Architecture

Agora is a React Native mobile app (iOS + Android) with a Python (FastAPI) backend. The primary interface is a **spatial map canvas**. Every feature is a micro app that registers into **extension slots** on that canvas — as map layers, rising panels, radial menu actions, compose hooks, full-screen canvases, persistent widgets, or headless data processors.

### Top-Level Directory

```
agora/
├── mobile/                        # React Native app
│   ├── src/
│   │   ├── kernel/                # The stable core — NEVER contains feature code
│   │   │   ├── identity/          # Auth, keypair management, Proof of Humanity
│   │   │   ├── capabilities/      # Capability token system (request, grant, revoke)
│   │   │   ├── message-bus/       # Typed pub/sub event system for inter-app communication
│   │   │   ├── app-runtime/       # WebView sandbox, lifecycle management, resource limits
│   │   │   ├── app-registry/      # Manifest parser, slot registration, app discovery
│   │   │   ├── storage/           # On-device encrypted store, CRDT sync, scoped access
│   │   │   └── mesh-transport/    # BLE mesh networking layer, message routing
│   │   │
│   │   ├── shell/                 # The map canvas and navigation — renders slots, not features
│   │   │   ├── MapCanvas.tsx
│   │   │   ├── SemanticZoomController.tsx
│   │   │   ├── PanelManager.tsx
│   │   │   ├── SlotRenderer.tsx       # Mounts micro app UIs into their registered slots
│   │   │   ├── PeekBar.tsx
│   │   │   ├── RadialContextMenu.tsx
│   │   │   ├── CanvasManager.tsx      # Full-screen workspace host
│   │   │   ├── WidgetLayer.tsx        # Persistent widget overlay
│   │   │   ├── AppSwitcher.tsx        # Map ↔ canvas navigation
│   │   │   ├── MapColorPulse.tsx
│   │   │   ├── TimeOfDayShader.tsx
│   │   │   └── layers/
│   │   │       └── LayerCompositor.tsx
│   │   │
│   │   ├── foundation/            # Design system — shared UI primitives
│   │   │   ├── tokens/
│   │   │   ├── components/        # AgoraButton, AgoraCard, AgoraPanel, etc.
│   │   │   └── web-components/    # Design system exported as Web Components for sandboxes
│   │   │
│   │   ├── sdk/                   # The Agora SDK — the ONLY import available inside sandboxes
│   │   │   ├── agora-sdk.ts
│   │   │   ├── types.ts
│   │   │   ├── ui-bridge.ts
│   │   │   └── agora-external-bridge.js  # Bridge script for external WebViews
│   │   │
│   │   ├── apps/                  # First-party micro apps
│   │   │   ├── agora.civic.tasks/         # ✅ Civic task board
│   │   │   ├── agora.feed.bridging/       # ✅ Bridging algorithm
│   │   │   ├── agora.feed.renderer/       # ✅ Feed UI
│   │   │   ├── agora.agent.attention-scorer/ # ✅ Attention scoring
│   │   │   ├── __test-harness__/          # ✅ Mock data generators
│   │   │   ├── agora.social.feed/         # Chronological friends feed
│   │   │   ├── agora.social.places/       # Local knowledge map
│   │   │   ├── agora.ai.chat/            # AI assistant (canvas + widget)
│   │   │   ├── agora.ai.create/          # "Speak It Into Being" app creation
│   │   │   ├── agora.messaging.core/     # Native DMs, groups, channels
│   │   │   ├── agora.deliberation.polis/
│   │   │   ├── agora.spatial.rooms/
│   │   │   ├── agora.moderation.jury/
│   │   │   ├── agora.moderation.ai-mediator/
│   │   │   ├── agora.events/
│   │   │   ├── agora.mesh.ui/
│   │   │   ├── agora.profile.customizer/
│   │   │   ├── agora.inbox.core/
│   │   │   ├── agora.inbox.compose/
│   │   │   ├── agora.inbox.connector.gmail/
│   │   │   ├── agora.inbox.connector.slack/
│   │   │   ├── agora.search.router/
│   │   │   ├── agora.notes/
│   │   │   ├── agora.projects/
│   │   │   ├── agora.calendar/
│   │   │   ├── agora.external.spotify/
│   │   │   ├── agora.external.banking/
│   │   │   ├── agora.external.browser/
│   │   │   ├── agora.meta.feedback/
│   │   │   │
│   │   │   ├── agora.agent.orchestrator/
│   │   │   ├── agora.agent.action-extractor/
│   │   │   ├── agora.agent.morning-briefing/
│   │   │   ├── agora.agent.civic-proximity/
│   │   │   ├── agora.agent.deliberation-observer/
│   │   │   └── agora.agent.bridge-spotter/
│   │   │
│   │   ├── onboarding/
│   │   └── services/
│   │
│   ├── assets/
│   │   ├── fonts/                 # Fraunces + Inter variable font files
│   │   ├── rive/                  # Rive animation state machines
│   │   ├── lottie/                # Lottie micro-animation JSON
│   │   ├── sounds/                # Earcon library (warm acoustic samples)
│   │   └── textures/              # Grain overlays, halftone patterns (SVG)
│   └── ios/ android/
│
├── backend/                       # Python FastAPI server
│   ├── api/                       # REST endpoints
│   ├── kernel-services/           # Backend kernel: auth, capability validation, app registry sync
│   ├── router/                    # Simplified query router (roster, escalation, gateway)
│   │   ├── roster.py
│   │   ├── escalation.py
│   │   ├── gateway.py
│   │   ├── streaming_handler.py
│   │   ├── cost_tracker.py
│   │   ├── feedback.py
│   │   ├── tool_registry.py
│   │   ├── tool_search.py
│   │   ├── voice/
│   │   └── observability/
│   ├── tools/                     # AI tool implementations
│   │   ├── search_places.py
│   │   ├── get_friend_activity.py
│   │   ├── search_apps.py
│   │   ├── query_bus_history.py
│   │   └── get_community_info.py
│   ├── agents/                    # Agent constellation
│   │   ├── orchestrator.py
│   │   ├── morning_briefing.py
│   │   ├── civic_proximity.py
│   │   ├── specialist_registry.py
│   │   └── scripts/
│   │       ├── compress_events.py
│   │       ├── gather_briefing_data.py
│   │       └── proximity_filter.py
│   ├── app-engine/                # App creation pipeline
│   │   ├── skill_schema.py
│   │   ├── skill_loader.py
│   │   ├── spec_builder.py
│   │   ├── app_generator.py
│   │   ├── safety_review.py
│   │   ├── manifest_validator.py
│   │   ├── versioning.py
│   │   ├── community_publishing.py
│   │   └── app_store.py
│   ├── community-engine/
│   ├── inbox-engine/
│   ├── integrations/
│   ├── models/
│   └── workers/
│
├── skills/                        # Skill library for app creation
│   ├── expense-splitting/
│   ├── location-finder/
│   ├── event-rsvp/
│   ├── group-decision/
│   ├── free-stuff-board/
│   ├── countdown-timer/
│   ├── ride-share/
│   └── study-group/
│
├── ml/                            # Machine learning models & training
│   ├── query-classifier/
│   ├── voice-enhancer/
│   ├── message-classifier/
│   └── bridging-scorer/
│
└── docs/
    ├── CLAUDE.md
    └── DEVELOPMENT_PLAN.md
```

### Key Architectural Decisions

**Why "everything is a micro app."** The micro app runtime IS the platform, and all features — search, inbox, community, civic tools — are apps running on it. This ensures: (1) any feature can be replaced, upgraded, or forked independently, (2) user-created apps have identical capabilities to first-party apps, (3) communities can govern which apps they enable via PolicyKit, and (4) the cold-start problem becomes an "app store" problem — each atomic network gets a tailored app bundle.

**Why a spatial map shell, not tabs.** The map canvas IS the home screen. Every micro app renders into an extension slot on that canvas — as a map layer, a rising panel, a radial menu action, or a headless data processor. The shell doesn't know about "feeds" or "inboxes" — it knows about slots.

**Why the kernel must be thin.** The kernel contains identity, capabilities, the sandbox runtime, the message bus, storage, and mesh transport. It does NOT contain any feature code. The test: "Could a community disable this without breaking the kernel?" If no, it belongs in the kernel. If yes, it's an app.

**Why FastAPI + Python backend.** ML workloads are Python-native. LiteLLM is Python.

**Why local-first data.** Message data lives on-device. The kernel's storage layer provides scoped, encrypted access. Apps request storage capabilities (`storage:personal`, `storage:community`) and get sandboxed partitions.

---

## 2. The Agora Kernel

The kernel is the stable, non-user-modifiable layer. It provides the "operating system" that all apps run on.

### 2.1 Identity & Auth

> **Current status:** Identity is a stub (`identity-types.ts` + `identity-context.ts` with mock user data). Real OAuth and Proof-of-Humanity deferred to Phase 6+.

```
mobile/src/kernel/identity/
├── identity-types.ts             # ✅ Type definitions for user identity
├── identity-context.ts           # ✅ Mock user data context (stub)
├── community-types.ts            # Community type, scale, gate, feature set definitions
├── auth-manager.ts               # OAuth flow management, session persistence
├── keypair-manager.ts            # Client-side keypair generation for E2E encryption
├── proof-of-humanity.ts          # Bot prevention verification flow
├── geofence-validator.ts         # Lightweight location verification for place-rooted communities
├── user-profile-store.ts         # Core identity: ID, display name, avatar ref, keypair
└── community-membership.ts       # Which communities this user belongs to, with type metadata
```

**Design decisions:**

- The kernel stores the minimal identity: user ID, display name, public key, community memberships, and civic reputation score. Everything else (profile page, avatar customization, facets) is handled by micro apps.
- Proof of Humanity runs once at signup. The kernel stores a boolean `is_verified`. Apps can request the `identity:read_verified` capability to check this.
- Community membership is a kernel concept because it governs which apps are available (communities enable/disable apps via PolicyKit). An app cannot determine community membership — the kernel provides it.

### 2.2 Capability System

```
mobile/src/kernel/capabilities/
├── capability-registry.ts        # Master list of all capability keys and their descriptions
├── capability-validator.ts       # Validates app requests against granted capabilities
├── capability-prompt.ts          # UI for showing the user what an app is requesting
├── capability-store.ts           # Persists which capabilities each app has been granted
└── capability-types.ts           # TypeScript types for all capability keys
```

**Design decisions:**

- Capabilities follow the Android/iOS permission model: apps declare what they need in their manifest, users grant or deny at install time, and the kernel enforces at runtime.
- Capabilities are granular. `map:read_location` and `map:write_pin` are separate. An app that reads your location doesn't automatically get to place markers.
- Capabilities are revocable. A community can vote (via PolicyKit) to revoke a capability from an app. The kernel stops delivering data on that channel immediately.
- Runtime capability requests are possible: an app can call `agora.requestCapability("notification:send")` and the user sees a permission prompt.
- The capability registry defines approximately 30 capability keys across these domains: `identity:*`, `map:*`, `content:*`, `votes:*`, `reputation:*`, `storage:*`, `panel:*`, `notification:*`, `network:*`, `inbox:*`, `mesh:*`, `audio:*`, `agent:*`.

**Agent capability keys:**

- `agent:run_persistent` — Permission to use the persistent lifecycle mode (stay active across panel close, app switch, and screen lock).
- `agent:escalate_cloud` — Permission to emit `search:query` for cloud LLM inference via the search router.
- `agent:read_bus_log` — Permission to read historical bus events for working memory reconstruction.
- `agent:emit_notification` — Distinguished from `notification:send` so communities can allow user notifications but ban agent-generated ones.
- `agent:cross_app_context` — Permission to subscribe to channels across multiple capability domains simultaneously.

**Canvas, widget, and external app capability keys:**

- `canvas:register` — Permission to register a full-screen canvas workspace.
- `canvas:background_persist` — Permission to keep canvas app state alive when user returns to map.
- `widget:register` — Permission to register a persistent widget overlay.
- `widget:always_visible` — Permission for widget to render on top of all map layers.
- `external:network_domain` — Permission for an external web app wrapper to reach its declared domain.
- `external:oauth_bridge` — Permission to pass OAuth tokens between the Agora identity system and the external app.
- `messaging:read` — Permission to read native Agora messages.
- `messaging:write` — Permission to send native Agora messages.
- `messaging:create_channel` — Permission to create new message channels or groups.

### 2.3 Message Bus

```
mobile/src/kernel/message-bus/
├── bus-core.ts                   # Pub/sub engine: subscribe, publish, unsubscribe
├── channel-registry.ts           # All valid message channels with payload type definitions
├── capability-gate.ts            # Filters subscriptions by app's granted capabilities
├── message-logger.ts             # Debug logging for all bus traffic (dev mode only)
├── message-types.ts              # TypeScript types for all message payloads
└── priority-dispatcher.ts        # Priority lanes (high/normal/low) for bus dispatch
```

Full specification in [Section 5: The Message Bus](#5-the-message-bus).

### 2.4 App Runtime & Sandbox

```
mobile/src/kernel/app-runtime/
├── sandbox-manager.ts            # Creates, manages, destroys sandboxed WebView instances
├── lifecycle-controller.ts       # Mount, unmount, suspend, resume app lifecycle
├── resource-monitor.ts           # CPU, memory, execution time enforcement
├── slot-manager.ts               # Maps apps to their registered extension slots
├── app-loader.ts                 # Loads app code from local storage or network
├── error-boundary.ts             # Catches app crashes, logs, renders fallback UI
├── keep-alive-manager.ts         # Manages persistent-lifecycle apps (agents), wake-on-event
├── external-app-host.ts          # WebView host for external web apps
├── bridge-injector.ts            # Injects Agora bridge JS into external WebViews
├── navigation-guard.ts           # Blocks navigation outside declared domain
└── external-app-sandbox.ts       # Capability enforcement for external apps
```

Full specification in [Section 6: The App Runtime & Sandbox](#6-the-app-runtime--sandbox).

### 2.5 Storage Layer

```
mobile/src/kernel/storage/
├── storage-engine.ts             # ✅ AsyncStorage wrapper for on-device key-value store
├── scoped-partition.ts           # ✅ Creates isolated storage partitions per app (namespaced)
├── crdt-merge.ts                 # ✅ LWW implementation (Automerge interface defined)
├── sync-coordinator.ts           # Coordinates sync with backend
└── storage-types.ts              # Scope types: personal, community, app-private
```

**Design decisions:**

- Storage is scoped: `storage:personal` (only this user, only this app), `storage:community` (shared within a community, governed by community rules), `storage:app-private` (app's internal state, not visible to user or other apps).
- All storage is encrypted on-device. The backend receives only anonymized metadata for sync.
- CRDT-based merge for offline edits: when two devices edit the same community data offline, Yjs handles the merge automatically.

### 2.6 Mesh Transport

```
mobile/src/kernel/mesh-transport/
├── ble-manager.ts                # Bluetooth Low Energy connection management
├── mesh-router.ts                # Multi-hop message routing between peers
├── peer-discovery.ts             # Discover nearby mesh peers
├── mesh-message-queue.ts         # Queue and deliver mesh messages
└── mesh-types.ts                 # Peer ID, mesh message format
```

**Design decisions:**

- Mesh transport is a kernel service because it requires native BLE access. Apps interact with mesh through the message bus (`mesh:broadcast`, `mesh:received` channels) via capabilities.
- Mesh messages are text-only, max 500 characters, no attachments. Location can be optionally attached.
- The kernel manages peer discovery and routing. Apps never see individual peer identities — they see message content and optional location.

---

## 3. The Shared Foundation (Design System)

The design system is kernel-adjacent: it's not part of the runtime, but it provides the shared visual language that all apps use. It's available inside sandboxes as Web Components.

### 3.1 Design Token System

```
mobile/src/foundation/tokens/
├── color-palette.ts              # Static color definitions (Civic Coral, Deep Teal, Agora Amber, etc.)
├── color-dynamic.ts              # Time-of-day color shifting logic + community color inheritance
├── color-accessibility.ts        # Contrast checking utilities, WCAG AA/AAA validators
├── typography.ts                 # Font family definitions, size scale, variable font axis configs
├── spacing.ts                    # 4px base grid, spacing scale
├── elevation.ts                  # Neubrutalist shadow system (offset shadows, not diffused)
├── motion.ts                     # Spring presets: civic-snappy, organic-bouncy, gentle-settle
└── haptics.ts                    # Haptic pattern definitions (vote-cast, consensus-reached, etc.)
```

**Design decisions:**

- `color-dynamic.ts`: continuous gradient through the day. Morning: full brightness cream. Afternoon: amber warming. Evening: deeper warmth. After 10pm: near-dark-mode. Not a binary toggle.
- `elevation.ts`: offset shadows (3px right, 3px down), not blur shadows. Pressed states collapse offset to 0.
- `motion.ts`: React Native Reanimated `withSpring()` config objects. No duration-based animations. Three presets: `civic-snappy` (high stiffness, moderate damping), `organic-bouncy` (moderate stiffness, low damping), `gentle-settle` (low stiffness, high damping).

### 3.2 Primitive Components

```
mobile/src/foundation/components/
├── AgoraButton.tsx               # Neubrutalist button with offset shadow, spring press animation
├── AgoraCard.tsx                 # Content card with bold border, grain texture, press-to-expand
├── AgoraInput.tsx                # Text input with bold border, warm focus glow
├── AgoraToggle.tsx               # Toggle switch with spring bounce, haptic feedback
├── AgoraChip.tsx                 # Tag/filter chip (audience selector, category indicator)
├── AgoraBadge.tsx                # Civic reputation badge, pixel-art style
├── AgoraPanel.tsx                # Sliding panel from bottom/side, glassmorphic background
├── AgoraRadialMenu.tsx           # Radial context menu (long-press on map)
├── GrainOverlay.tsx              # SVG feTurbulence noise overlay
├── TypographyDisplay.tsx         # Fraunces display text with variable axis controls
├── TypographyBody.tsx            # Inter body text with system font scaling
└── EmptyState.tsx                # Illustrated empty states, always positive and community-oriented
```

**Design decisions:**

- `AgoraButton`: three states — default (full shadow), pressed (shadow collapses with `civic-snappy` spring), disabled (border thins, never opacity alone).
- `AgoraCard`: `GrainOverlay` as child, 2-3px borders, warm corners (8-12px).
- `AgoraRadialMenu`: max 6 items, equidistant from center (Fitts's Law), `organic-bouncy` staggered spring.
- `EmptyState`: always positive. "Your inbox is peaceful" with an illustrated scene. Never "Nothing here."

### 3.3 Web Components Export

```
mobile/src/foundation/web-components/
├── agora-button.ts               # ✅ <agora-button> Web Component
├── agora-card.ts                 # ✅ <agora-card> Web Component
├── agora-input.ts                # ✅ <agora-input> Web Component
├── agora-badge.ts                # ✅ <agora-badge> Web Component
├── agora-vote-buttons.ts         # ✅ <agora-vote-buttons> Agree/Disagree/Pass
├── agora-creative-post.ts        # ✅ <agora-creative-post> Content card w/ bridging score
├── agora-threaded-conversation.ts # ✅ <agora-threaded-conversation> Branching reply tree
├── agora-finite-scroll-list.ts   # ✅ <agora-finite-scroll-list> Finite list + stopping cue
├── agora-why-seeing-this.ts      # ✅ <agora-why-seeing-this> Transparency panel
├── agora-audience-selector.ts    # ✅ <agora-audience-selector> Chip-based audience picker
├── register-all.ts               # ✅ Registers all Web Components in the sandbox WebView
└── theme-bridge.ts               # ✅ Injects current design tokens into WebView
```

**Design decisions:**

- Web Components are the bridge between the kernel's design system and sandboxed micro apps. Every micro app — first-party or user-created — uses these components via `<agora-button>`, `<agora-card>`, etc. inside their WebView sandbox.
- `theme-bridge.ts` injects current design tokens (time-of-day colors, community theme overrides) as CSS custom properties into every WebView. User-created apps automatically inherit the correct visual language.

### 3.4 Animation Assets

```
assets/rive/
├── mascot-idle.riv               # Mascot idle state machine
├── mascot-wave.riv               # First-open greeting
├── mascot-celebrate.riv          # Achievement/consensus moments
├── mascot-thinking.riv           # AI processing indicator
└── mascot-encourage.riv          # Nudge/suggestion moments

assets/lottie/
├── civic-task-complete.json
├── consensus-sparkle.json
├── bridging-glow.json
└── mesh-pulse.json

assets/sounds/
├── earcon-vote-cast.wav          # Warm wooden thud
├── earcon-message-sent.wav       # Soft whoosh with chime tail
├── earcon-consensus.wav          # Gentle bell chord (celebratory)
├── earcon-task-complete.wav      # Satisfying click-clack
├── earcon-navigate.wav           # Subtle tonal shift
├── earcon-error.wav              # Low warm tone (concerned, not alarming)
├── ambient-morning.wav           # Birds, soft light
├── ambient-evening.wav           # Crickets, warmth
└── ambient-civic-room.wav        # Subtle presence hum
```

---

## 4. The App Manifest & Extension Slots

Every micro app — first-party or user-created — is described by a single manifest.

### 4.1 The Manifest Schema

```
mobile/src/kernel/app-registry/
├── manifest-schema.ts            # TypeScript schema definition for agora-app.manifest.json
├── manifest-parser.ts            # Parse and validate a manifest file
├── manifest-store.ts             # Persist installed app manifests
├── slot-registry.ts              # Map of all extension slot types and their contracts
├── app-dependency-resolver.ts    # Resolve app dependencies and load order
└── community-app-config.ts       # Which apps are enabled for which communities
```

**The manifest format:**

```json
{
  "id": "agora.civic.tasks",
  "version": "1.2.0",
  "name": "Civic Task Board",
  "description": "Civic drop points, cleanup events, mapping tasks with gamification",
  "author": { "id": "user_abc", "name": "Agora Core Team", "type": "cooperative" },
  "forked_from": null,
  "license": "agora-commons",

  "slots": [
    { "type": "map-layer", "priority": "primary" },
    { "type": "panel", "trigger": "map-tap-civic-pin", "position": "half" },
    { "type": "radial-action", "label": "Create Civic Task", "icon": "civic-pin" }
  ],

  "capabilities": [
    "map:read_location", "map:write_pin", "map:write_layer",
    "reputation:read", "reputation:write_badge",
    "storage:community", "panel:register", "notification:send"
  ],

  "subscribes_to": ["user:location_changed", "user:tapped_map", "reputation:updated"],
  "emits": ["civic:task_created", "civic:task_completed", "reputation:badge_awarded"],

  "resources": {
    "max_memory_mb": 50,
    "max_cpu_ms_per_frame": 16,
    "network": "community_api_only",
    "storage_quota_mb": 10
  },

  "governance": {
    "scope": "platform",
    "approved_by": "cooperative_vote",
    "can_be_disabled_by": "community_vote",
    "can_be_forked": true
  },

  "lifecycle": "standard",
  "agent": null,

  "entry": {
    "logic": "./src/civic-tasks.js",
    "ui": "./src/civic-tasks-panel.html"
  }
}
```

### 4.2 Extension Slot Types

| Slot Type | What it does | Contract |
|-----------|-------------|----------|
| `feed-algorithm` | Provides content ranking logic | Receives raw content + vote data → must emit `feed:content_ranked` |
| `feed-renderer` | Renders the content feed UI | Subscribes to `feed:content_ranked` → renders in panel |
| `map-layer` | Renders features on the map canvas | Receives viewport bounds + zoom → returns renderable GeoJSON |
| `panel` | Interactive UI in a rising bottom panel | Gets a sandboxed WebView area, triggered by navigation events |
| `compose-hook` | Intercepts outgoing messages | Receives draft message → can suggest modifications → user has final say |
| `moderation-rule` | Evaluates content against a rule | Receives content → returns pass/flag/block recommendation |
| `notification-handler` | Decides what becomes a notification | Receives events → returns notification priority + content |
| `radial-action` | Contributes an action to the map long-press menu | Provides label, icon, and handler function |
| `data-connector` | Bridges an external platform into unified schema | Implements connect/sync/send/normalize interface |
| `headless` | Background data processing (no UI) | Subscribes to events, emits events. No panel or map presence. |
| `canvas` | Full-screen non-spatial workspace | App owns the entire screen when focused. Receives `canvas:focus` / `canvas:blur` lifecycle events. Shell provides back-to-map gesture. |
| `widget` | Persistent glanceable unit on map or dashboard | Small, always-visible UI (max 200×200pt). Receives `widget:tap` for expand-to-panel behavior. Renders in a sandboxed mini-WebView with tight resource limits (10MB memory, 8ms/frame). |
| `external-web-app` | Sandboxed wrapper for third-party web apps | Loads an external URL in a capability-gated WebView with injected bridge adapter. Network policy: allowed to reach the external app's domain only. |

**Design decisions:**

- A single app can register for multiple slots. The civic task app registers for `map-layer`, `panel`, and `radial-action`.
- Slots have a `priority` field: `primary` (enabled by default) or `available` (user must enable).
- Only one app can be `primary` for `feed-algorithm` at a time per community. The community votes on which feed algorithm is primary.
- `panel` slots specify a `trigger` (what event causes the panel to open) and a `position` (`full`, `half`, `peek`).

---

## 5. The Message Bus

All inter-app communication flows through the typed, asynchronous message bus. Apps never import each other. Apps never hold references to each other's state.

### 5.1 Channel Definitions

```typescript
// mobile/src/kernel/message-bus/message-types.ts

type AgoraMessage =
  // === USER ACTIONS (emitted by kernel) ===
  | { channel: "user:location_changed"; payload: { lat: number; lng: number } }
  | { channel: "user:zoom_changed"; payload: { level: ZoomLevel } }
  | { channel: "user:tapped_map"; payload: { lat: number; lng: number; feature_id?: string } }
  | { channel: "user:opened_panel"; payload: { panelId: string } }
  | { channel: "user:long_pressed_map"; payload: { lat: number; lng: number } }

  // === CONTENT EVENTS ===
  | { channel: "content:new_post"; payload: { post: ContentItem } }
  | { channel: "content:updated"; payload: { post: ContentItem } }
  | { channel: "feed:content_ranked"; payload: { items: ContentItem[]; has_more: boolean } }
  | { channel: "feed:stopping_cue"; payload: { message: string } }

  // === VOTING & DELIBERATION ===
  | { channel: "votes:cast"; payload: { contentId: string; vote: "agree" | "disagree" | "pass" } }
  | { channel: "deliberation:statement_added"; payload: { statement: Statement } }
  | { channel: "deliberation:consensus_reached"; payload: { statementId: string; support: number } }

  // === REPUTATION ===
  | { channel: "reputation:updated"; payload: { userId: string; score: number } }
  | { channel: "reputation:badge_awarded"; payload: { userId: string; badge: Badge } }

  // === CIVIC ===
  | { channel: "civic:task_created"; payload: { task: CivicTask } }
  | { channel: "civic:task_completed"; payload: { taskId: string; userId: string } }

  // === INBOX ===
  | { channel: "inbox:message_received"; payload: { message: UnifiedMessage } }
  | { channel: "inbox:attention_scored"; payload: { messageId: string; score: number } }
  | { channel: "inbox:action_extracted"; payload: { messageId: string; action: ExtractedAction } }
  | { channel: "inbox:briefing_ready"; payload: { briefing: MorningBriefing } }

  // === APP LIFECYCLE (emitted by kernel) ===
  | { channel: "app:mounted"; payload: { appId: string; slot: SlotType } }
  | { channel: "app:unmounted"; payload: { appId: string } }
  | { channel: "app:error"; payload: { appId: string; error: string } }

  // === COMMANDS (apps request kernel actions) ===
  | { channel: "cmd:show_panel"; payload: { appId: string; content: string } }
  | { channel: "cmd:add_map_pin"; payload: MapPin }
  | { channel: "cmd:add_map_layer"; payload: { appId: string; geojson: GeoJSON } }
  | { channel: "cmd:send_notification"; payload: { title: string; body: string } }
  | { channel: "cmd:request_capability"; payload: { cap: CapabilityKey } }
  | { channel: "cmd:play_earcon"; payload: { sound: EarconName } }
  | { channel: "cmd:trigger_haptic"; payload: { pattern: HapticPattern } }

  // === MESH ===
  | { channel: "mesh:broadcast"; payload: { text: string; location?: Coord } }
  | { channel: "mesh:received"; payload: { from: PeerId; text: string; location?: Coord } }
  | { channel: "mesh:peer_count_changed"; payload: { count: number } }

  // === CANVAS & WIDGET ===
  | { channel: "canvas:focus"; payload: { appId: string } }
  | { channel: "canvas:blur"; payload: { appId: string } }
  | { channel: "canvas:state_saved"; payload: { appId: string; version: number } }
  | { channel: "widget:tap"; payload: { widgetId: string } }
  | { channel: "widget:data_refresh"; payload: { widgetId: string } }

  // === NATIVE MESSAGING ===
  | { channel: "messaging:received"; payload: { message: AgoraMessage; channelId: string } }
  | { channel: "messaging:sent"; payload: { message: AgoraMessage; channelId: string } }
  | { channel: "messaging:channel_created"; payload: { channelId: string; type: "dm" | "group" | "community" } }

  // === EXTERNAL APP BRIDGE ===
  | { channel: "external:loaded"; payload: { appId: string; domain: string } }
  | { channel: "external:navigation"; payload: { appId: string; url: string; blocked: boolean } }
  | { channel: "external:bridge_event"; payload: { appId: string; eventType: string; data: any } }

  // === AGENT COORDINATION ===
  | { channel: "agent:task_dispatch"; payload: { taskType: string; target: string; context: any } }
  | { channel: "agent:task_complete"; payload: { taskType: string; result: any } }
  | { channel: "search:query"; payload: { prompt: string; source: "user" | "agent"; priority?: string } }
  | { channel: "search:result"; payload: { content: string; source: "user" | "agent" } }
  | { channel: "feed:annotation"; payload: { contentId: string; annotation: string; type: "bridge" | "consensus" | "context" } }
  | { channel: "user:session_started"; payload: { timestamp: number; resuming: boolean } }
```

### 5.2 Communication Rules

1. **Capability-gated subscriptions.** An app can only subscribe to channels its capabilities permit. The civic task app can listen to `user:location_changed` (it has `map:read_location`) but NOT `inbox:message_received` (it doesn't have `inbox:read`).

2. **Fire-and-forget commands.** Apps emit commands (`cmd:show_panel`) but don't know if anyone handles them. The kernel validates and routes. If the command requires a capability the app doesn't have, it's silently dropped and logged.

3. **No direct app-to-app messages.** App A cannot send a message specifically to App B. It publishes to a channel. Any app subscribed to that channel receives it. This prevents tight coupling.

4. **Serializable payloads only.** All message payloads must be JSON-serializable. No functions, no object references, no callbacks. This is what makes the sandbox boundary enforceable.

5. **Channel namespacing.** Custom channels from user-created apps are prefixed with `custom:{appId}:`. This prevents collisions with kernel channels.

6. **Priority lanes.** The bus dispatches messages at three priority levels: **high** (user-initiated events like taps and panel opens — dispatched synchronously), **normal** (app-emitted data events — dispatched in next microtask via `queueMicrotask()`), and **low** (agent-subscribed processing — dispatched via `requestIdleCallback()`). This ensures agent processing never delays user-facing interactions.

---

## 6. The App Runtime & Sandbox

### 6.1 Dual-Realm Architecture

Every micro app runs in two isolated realms:

**Logic Realm** — A sandboxed JavaScript environment (WebView with network disabled, or QuickJS-in-WASM for maximum isolation). Can access the Agora SDK (message bus, storage, capabilities) but NOT browser APIs, DOM, or device APIs.

**UI Realm** — A sandboxed WebView with the design system's Web Components injected. Can render HTML/CSS/JS but can only communicate with the Logic Realm through message passing. Cannot access the Agora SDK directly.

The two realms communicate through a bridge (`sdk/ui-bridge.ts`) that serializes messages between them.

### 6.2 Resource Limits

| Resource | Limit | Enforcement |
|----------|-------|-------------|
| Memory | 50 MB per app | WebView memory monitoring; terminate if exceeded |
| CPU | 16ms per frame (60fps target) | Long-task detection; throttle if exceeded |
| Execution time | 5 seconds for any single operation | Timeout and kill |
| Storage | 10 MB default (configurable per manifest) | Quota enforcement in scoped partition |
| Network | Community API only (no arbitrary URLs by default) | Network policy in WebView sandbox |

### 6.3 Lifecycle

```
INSTALLED → RESOLVED → MOUNTING → ACTIVE → SUSPENDED → UNMOUNTED
                                    ↑                      |
                                    └──────────────────────┘
```

- **INSTALLED:** Manifest parsed and stored. Not yet running.
- **RESOLVED:** Dependencies checked, capabilities validated. Ready to mount.
- **MOUNTING:** Sandbox WebView created, app code loaded, SDK injected.
- **ACTIVE:** App is running, subscribed to events, rendering in its slot.
- **SUSPENDED:** App is backgrounded. Event subscriptions paused. State preserved.
- **UNMOUNTED:** App torn down. WebView destroyed. State persisted to storage.

**Persistent lifecycle mode.** Apps with `"lifecycle": "persistent"` in their manifest skip the SUSPENDED and UNMOUNTED transitions. They remain ACTIVE from boot until explicitly disabled. Managed by `keep-alive-manager.ts`. This mode requires the `agent:run_persistent` capability.

### 6.4 Error Handling

- If an app crashes, the kernel catches the error in `error-boundary.ts` and renders a fallback UI: "This app encountered an issue. [Restart] [Disable]"
- App crashes never affect other apps or the kernel. Each app runs in its own WebView.
- Crash logs are stored locally and optionally shared with the app's author (with user consent).
- If an app crashes 3 times in 5 minutes, the kernel auto-suspends it and notifies the user.

---

## 7. The Map Shell

The map is not a feature. It is the app. Everything else is a micro app rendering into a slot on the map.

### 7.1 Shell Components

```
mobile/src/shell/
├── MapCanvas.tsx                  # Full-screen map (MapLibre GL Native)
├── SemanticZoomController.tsx     # Manages zoom level → which slots are visible
├── ZoomLevelRouter.tsx            # Determines which map-layer apps are active at each zoom
├── SlotRenderer.tsx               # The critical component: mounts micro app UIs into slots
├── RadialContextMenu.tsx          # Long-press → fans out radial-action slot registrations
├── PeekBar.tsx                    # Bottom bar: feed indicator, VoiceOrb, civic pulse
├── PanelManager.tsx               # Stack of rising panels from bottom
├── CanvasManager.tsx              # Full-screen workspace host
├── WidgetLayer.tsx                # Persistent widget overlay
├── AppSwitcher.tsx                # Map ↔ canvas navigation strip
├── MapColorPulse.tsx              # Peripheral awareness: civic activity → color shifts
├── TimeOfDayShader.tsx            # Map tile warmth adjustment
└── layers/
    └── LayerCompositor.tsx        # Composites all active map-layer micro apps
```

### 7.2 How SlotRenderer Works

```typescript
function SlotRenderer() {
  const registeredApps = useAppRegistry();

  const mapLayerApps = registeredApps.filter(a => a.hasSlot("map-layer"));
  mapLayerApps.forEach(app => {
    const sandbox = createSandbox(app);
    sandbox.on("cmd:add_map_layer", (geojson) => {
      map.addSource(app.id, geojson);
    });
  });

  const panelApps = registeredApps.filter(a => a.hasSlot("panel"));
  panelApps.forEach(app => {
    panelManager.registerTrigger(app.slot.trigger, () => {
      const sandbox = createSandbox(app);
      panelManager.push(<SandboxedWebView app={app} />);
    });
  });

  const radialApps = registeredApps.filter(a => a.hasSlot("radial-action"));
  radialMenu.setActions(radialApps.map(app => ({
    label: app.slot.label,
    icon: app.slot.icon,
    onPress: () => app.sandbox.emit("radial:activated"),
  })));
}
```

### 7.3 Semantic Zoom Behavior

| Zoom Level | What's Visible | Which Slots Activate |
|-----------|----------------|---------------------|
| **CITY** | Civic heatmap, community boundaries | Only `map-layer` apps tagged `scale: city` |
| **NEIGHBORHOOD** | Voice rooms, event clusters, governance indicators | `map-layer` apps tagged `scale: neighborhood` |
| **STREET** | Individual civic tasks, issue reports, friend avatars | All `map-layer` apps, `radial-action` apps |
| **DETAIL** | Everything tappable, panel triggers active | All slots active |

### 7.4 Shell Design Decisions

- `PanelManager` manages a stack. Only one panel is "primary" at a time, but panels can stack. Swiping down dismisses the top panel. Map always visible above.
- `PeekBar` shows three things always: (1) feed indicator, (2) VoiceOrb for search, (3) civic pulse dot. Each pulls up the corresponding micro app's panel.
- `MapColorPulse` is calm technology: areas with active civic discussions glow warmer. Areas with recent consensus shimmer once. Slow, continuous color shifts only.
- The map style is warm. Custom MapLibre styling: desaturate blues, warm greens, cream/off-white roads, warm charcoal labels.

### 7.5 Canvas Manager

`CanvasManager.tsx` hosts full-screen, non-spatial micro apps. When a canvas app is opened, the map slides away (spring animation, `gentle-settle` preset) and the canvas takes over the viewport. The map is still alive underneath — widgets remain visible if they have `widget:always_visible`, and the user returns to the map via a persistent back gesture.

```typescript
function CanvasManager() {
  const [activeCanvas, setActiveCanvas] = useState<string | null>(null);
  const canvasApps = useAppRegistry().filter(a => a.hasSlot("canvas"));

  function openCanvas(appId: string) {
    bus.emit("canvas:focus", { appId });
    setActiveCanvas(appId);
  }

  function closeCanvas() {
    bus.emit("canvas:blur", { appId: activeCanvas });
    setActiveCanvas(null);
  }

  if (!activeCanvas) return null;
  return (
    <SandboxedWebView
      app={canvasApps.find(a => a.id === activeCanvas)}
      fullScreen={true}
      onBackGesture={closeCanvas}
    />
  );
}
```

**Design decisions:**

- Canvas apps have higher resource limits: 100MB memory, 32ms/frame (30fps minimum), 50MB storage.
- Only one canvas app is active at a time. Opening a second canvas suspends the first.
- Canvas apps with `canvas:background_persist` keep their WebView alive when the user returns to the map, so returning to the canvas is instant.
- The `AppSwitcher.tsx` shows all open canvas apps as a horizontal strip of thumbnails above the PeekBar.

### 7.6 Widget Layer

`WidgetLayer.tsx` renders persistent, glanceable mini-UIs that float above the map.

```typescript
function WidgetLayer() {
  const widgetApps = useAppRegistry().filter(a => a.hasSlot("widget"));

  return (
    <View style={styles.widgetOverlay} pointerEvents="box-none">
      {widgetApps.map(app => (
        <WidgetContainer
          key={app.id}
          app={app}
          position={app.slot.position}
          size={app.slot.size}       // "small" (80x80) | "medium" (160x80) | "large" (160x160)
          onTap={() => bus.emit("widget:tap", { widgetId: app.id })}
        >
          <MiniSandboxedWebView app={app} maxMemoryMb={10} maxCpuMsPerFrame={8} />
        </WidgetContainer>
      ))}
    </View>
  );
}
```

**Design decisions:**

- Widgets render in constrained mini-WebViews: 10MB memory, 8ms/frame, no network by default.
- Widget positions are user-draggable and persisted.
- Widgets can optionally dock in a "widget tray" (collapsed bar at the top of the map).
- Maximum 6 visible widgets at a time (performance constraint).

### 7.7 App Switcher

`AppSwitcher.tsx` provides navigation between the map home and any active canvas apps:

- Renders as a subtle strip of thumbnails above the PeekBar when canvas apps are open
- Leftmost thumbnail is always the map (home)
- Tapping a thumbnail switches to that canvas or back to the map
- Long-press shows app name, "Close," and "Info" options
- When no canvas apps are open, the switcher is hidden

### 7.8 External Web App Integration

Third-party apps like Spotify, banking apps, and university portals become micro apps inside Agora by wrapping their mobile web version in a sandboxed WebView with an injected bridge adapter.

**Architecture:**

```
mobile/src/kernel/app-runtime/
├── external-app-host.ts          # WebView host for external web apps
├── bridge-injector.ts            # Injects Agora bridge JS into external WebViews
├── navigation-guard.ts           # Blocks navigation outside declared domain
└── external-app-sandbox.ts       # Capability enforcement for external apps

mobile/src/sdk/
└── agora-external-bridge.js      # Bridge script injected into external WebViews
```

**Three tiers of external app integration:**

| Tier | Effort | What you get | Example |
|------|--------|-------------|---------|
| **Tier 1: Dumb wrapper** | 10 minutes | External app loads in a canvas. No bus integration. Just a governed, sandboxed window. | Banking app, government portals |
| **Tier 2: Bridge adapter** | 2–4 hours | Bridge script injected, DOM events translated to bus messages. Widget shows glanceable state. | Spotify (now-playing widget) |
| **Tier 3: Deep integration** | 1–2 days | Custom bridge with full bidirectional control. OAuth bridged. Data flows into unified schemas. | Gmail/Slack connectors, Calendar services |

---

## 8. First-Party Micro Apps

Every feature from the original plan, restructured as micro apps using the same manifest, sandbox, and message bus that user-created apps use.

### 8.1 Community & Civic Apps

**agora.feed.bridging** — `headless` + `feed-algorithm` slots
The bridging-based ranking engine. Subscribes to `content:new_post` and `votes:cast`. Groups voters into clusters by >70% agreement, then scores each post by the fraction of clusters with >30% agreement (bridging score). Emits `feed:content_ranked`, `feed:stopping_cue`, and `feed:annotation`. A community can replace it with a different ranking algorithm by swapping which feed-algorithm app is primary.

**agora.feed.renderer** — `panel` slot
The feed display. Subscribes to `feed:content_ranked`. Renders the finite scrolling list with stopping cue. Contains ThreadedConversation and CreativePost (no likes, response-only). Emits `votes:cast`. Intentionally separated from the algorithm for independent evolution.

**agora.social.feed** — `panel` + `feed-renderer` slots
A lightweight chronological social feed for the friends-first launch. Optional "friends nearby" boost. Uses existing Web Components. No infinite scroll. Subscribes to `content:new_post` and emits `feed:content_ranked` — same bus contract as `agora.feed.bridging`, enabling hot-swapping when communities grow past ~100 members.

**agora.social.places** — `map-layer` + `panel` slots
Local knowledge map. Friends pin recommendations: best coffee, taco trucks, study spots, free stuff, hidden gems. Each pin has name, category, short note, optional photo, author. Category filters: Food, Study, Outdoors, Events, Free Stuff, Hidden Gems. Pre-seeded with 30–50 pins in SLO.

**agora.deliberation.polis** — `panel` slot
Pol.is voting engine. Statement display, Agree/Disagree/Pass, PCA visualization, consensus highlighting. Can be composed alongside spatial rooms for a "Digital Town Hall."

**agora.spatial.rooms** — `map-layer` + `panel` slots
Spatial audio rooms with proximity chat. Pulsing circles on the map; avatar canvas with WebRTC proximity audio in a panel. Full volume within 1 tile, linear falloff 1-5 tiles, silence beyond 5.

**agora.moderation.jury** — `panel` + `moderation-rule` slots
Digital jury system (Kleros model). Random juror selection weighted by reputation, blind independent voting, Schelling point incentive. Supports appeals.

**agora.moderation.ai-mediator** — `compose-hook` slot
AI toxicity detection. Intercepts outgoing messages. When toxicity > 80%, shows a modal with rephrased version alongside original. Never blocks — user always has agency. Community-governable via PolicyKit.

**agora.civic.tasks** — `map-layer` + `panel` + `radial-action` slots
Civic drop points, cleanup events, mapping tasks. Renders pins on map, detail panel on tap, "Create Civic Task" in radial menu. Awards reputation badges on completion. Includes Mapillary integration.

**agora.events** — `map-layer` + `panel` slots
Local event discovery. Events as warm glowing pins on map. Max 5km visibility radius. No attendee counts — "A few people are going" / "Many neighbors are joining." Type-aware: persistent pins for place-rooted/hybrid communities, temporary meetup pins for interest-rooted.

**agora.mesh.ui** — `panel` + `radial-action` slots
Mesh networking UI. High-contrast, simplified mode. Shows peer count, estimated range. Text-only composer for mesh messages.

**agora.profile.customizer** — `panel` slot
Creative profile page with HTML/CSS-lite customization (MySpace spirit). AI-generated CSS token overrides from natural language ("90s zine," "cottagecore"). Avatar editor with illustrated/screen-printed style, earned accessories through participation.

### 8.2 AI & Search Apps

**agora.ai.chat** — `canvas` + `widget` slots
The AI assistant as a first-class micro app. Canvas provides the full conversation. Widget shows a floating VoiceOrb on the map (tap to ask anything). Model-agnostic: three user presets — speed (flash tier) / balanced (default tier + escalation rules) / quality (premium tier). Includes the "✦ Deeper answer" button for manual escalation.

**agora.ai.create** — `canvas` slot
The "Speak It Into Being" app creation flow. Conversational UI where users describe what they want. Streams spec builder output, shows structured spec for approval, generates code, and runs an instant preview. The entire flow targets < 2 minutes for a simple app.

**agora.search.router** — `panel` slot
SearchCanvas panel rising from map via VoiceOrb. PromptPreview (editable enhanced prompt), ResultStream (token-by-token spring fade-in), ModelAttribution (transparent routing disclosure).

### 8.3 Inbox Apps

**agora.inbox.core** — `panel` + `notification-handler` + `headless` slots
The inbox intelligence layer. Attention scoring, morning briefing integration, action extraction, entity resolution. Messages show: platform badge, sender name, one-line AI summary, attention score as color intensity, extracted action buttons inline. FocusModeToggle strips to high-attention only.

**agora.inbox.compose** — `panel` + `compose-hook` slots
Universal composer. Pick recipient, AI suggests optimal platform. Adapts formatting. Runs compose-hooks on outgoing messages. Pre-drafts replies using user's writing style (learned and stored locally).

**agora.inbox.connector.gmail** — `data-connector` slot
Gmail connector. OAuth, Google Pub/Sub push, full CRUD. Normalizes to UnifiedMessage schema.

**agora.inbox.connector.slack** — `data-connector` slot
Slack connector. Events API, channel + thread + DM normalization.

**agora.inbox.connector.[outlook|teams|discord|canvas|github]** — `data-connector` slots
Additional connectors. All implement the same `data-connector` contract: `connect(oauth_token)`, `sync_inbound(since)`, `send_outbound(message)`, `normalize(raw)`.

### 8.4 Messaging App

**agora.messaging.core** — `panel` + `canvas` + `notification-handler` slots
Native Agora messaging. DMs, group chats, and community channels. E2E encryption via the kernel's keypair system. Supports threaded replies, limited reactions (intentional friction), and voice notes. Canvas provides full-screen chat; panel provides quick-reply overlay. Renders on the map as proximity indicators.

### 8.5 Productivity Apps

**agora.notes** — `canvas` slot
Markdown-based note-taking/document editor. Agora Web Component rendering. CRDT collaborative editing. Can embed bus references from other micro apps. Personal by default; shareable to community scope.

**agora.projects** — `canvas` + `widget` slots
Kanban-style project management board. Cards with assignees, deadlines, status. Widget shows a task counter. Cards link to messaging channels, notes, and calendar events via bus references.

**agora.calendar** — `canvas` + `map-layer` + `widget` slots
Timeline view in canvas mode, spatial event view on the map. Widget shows "next event" countdown. Integrates with the morning briefing agent.

### 8.6 External App Wrappers

**agora.external.spotify** — `external-web-app` + `canvas` + `widget` slots
Spotify web player. Tier 2: bridge adapter extracts now-playing info for a map widget and emits `music:now_playing` to the bus.

**agora.external.banking** — `external-web-app` + `canvas` slot
Generic banking wrapper template. Tier 1: loads the user's bank web portal. No data extraction. Users can duplicate this manifest and point it at their own bank's URL.

**agora.external.browser** — `external-web-app` + `canvas` slot
General-purpose web browser canvas. Tier 1 with broad domain allowance. The "escape hatch." Subject to community governance.

### 8.7 Agent Apps

The intelligence layer is built as a constellation of specialized AI agents on the message bus. Each agent is a headless micro app with the same manifest contract, sandbox isolation, and capability gating as every other app.

**The Orchestrator-Specialist Pattern:** One central orchestrator subscribes to all bus channels it has capabilities for and performs real-time triage. It dispatches tasks to specialist agents via `agent:task_dispatch`. Specialists wake on interrupt, process the task, emit the result, and go back to sleep. Most events are classified as "ignore" — specialists remain dormant the majority of the time.

**Three-layer working memory pattern:**

- **Layer 1 (raw buffer):** In-memory ring buffer of the last ~200 bus events. No LLM involvement. ~30 minutes of history.
- **Layer 2 (compressed summaries):** Every hour, `compress_events.py` runs on the raw buffer. The ~500-token structured summary is what enters the LLM context.
- **Layer 3 (persistent patterns):** Scoped SQLite table storing long-term user patterns. Loaded on demand.

**agora.agent.orchestrator** — `headless` slot, `persistent` lifecycle
The central triage agent. Subscribes to `content:new_post`, `messaging:received`, `user:location_changed`, `user:session_started`. Maintains three-layer working memory. On `user:session_started`: dispatches to morning briefing. On `user:location_changed`: calls `proximity_filter.py`. Makes LLM calls only for judgment decisions, passing Layer 2 summaries + the specific event as context (~1–2K tokens per decision).

**agora.agent.attention-scorer** — `headless` slot, `standard` lifecycle
Scores incoming messages on a 0–1 urgency scale. Current implementation: heuristic scoring with four sub-functions (`senderFrequencyScore`, `keywordScore`, `timeOfDayScore`, `platformScore`). Future upgrade: fine-tuned DistilBERT model (~65MB ONNX, <5ms inference). Forkable — a protest group might tune urgency for safety messages.

**agora.agent.action-extractor** — `headless` slot, `standard` lifecycle
Extracts structured actions from high-attention messages: "schedule meeting with Alex on Thursday." Emits `inbox:action_extracted` with one-click action buttons.

**agora.agent.morning-briefing** — `headless` slot, `standard` lifecycle
Daily briefing. Triggered on `user:session_started`. Calls `gather_briefing_data.py` (structured JSON under 1K tokens), then LLM generates a warm 2–4 sentence summary. Total LLM context per briefing: ~2K tokens. Emits on `inbox:briefing_ready`.

**agora.agent.civic-proximity** — `headless` slot, `standard` lifecycle
Watches user location and social/civic data. Repurposed for social context at small scale: "Your friend Alex is at the library," "Free couch 2 blocks from you." Same infrastructure later powers civic task matching. Pure event reactions — no ML needed.

**agora.agent.deliberation-observer** — `headless` slot, `standard` lifecycle
Watches active Pol.is sessions. Tracks consensus approach on statements the user cares about. Emits notifications and `feed:annotation` context cards. Pure pattern matching on vote data.

**agora.agent.bridge-spotter** — `headless` slot, `standard` lifecycle
Detects content achieving unusually high bridging scores. Emits `feed:annotation` and `reputation:bridge_detected`. Makes bridging visible and celebrated.

**Design decisions for the agent constellation:**

- The orchestrator is the only persistent agent. Specialists use standard lifecycle — wake on dispatch. 7+ agents does not mean 7× resource cost.
- Agent-emitted notifications use `agent:emit_notification`, separate from `notification:send`.
- Agents escalate to cloud LLMs through the search router by emitting `search:query` with `source: "agent"`.
- Users can create custom agents through the app creation pipeline.
- All agent behavior is auditable — every action is a bus event with a typed payload.
- The `agent` field in the manifest is metadata, not enforcement. The kernel doesn't treat "agent apps" differently. `persistent` lifecycle and `background` CPU priority are what matter.
- **Raw data never enters the LLM context.** Deterministic Python scripts handle data gathering and filtering. The LLM only sees structured summaries.

**Specialist registry & defer-loading:** Each specialist declares `name`, `description`, `handles` (event types), `wake_on` (bus channels). At 200 users with 3 specialists, all definitions fit in the orchestrator's prompt (~300 tokens). `defer_loading` flag enables future-proofing for when specialist count exceeds 5.

### 8.8 Platform Meta Apps

**agora.meta.feedback** — `panel` slot
Shake-to-report-bug. Screenshot capture, device info collection, reproduction steps template. Routes directly to the developer.

---

## 9. The App Creation Pipeline

This is how users create micro apps. It maps to the three layers of user co-creation: Personal Canvas, Community Workshop, Agora Commons.

### 9.1 The Agora SDK

```
mobile/src/sdk/agora-sdk.ts
```

The Agora SDK is the ONLY import available inside micro app sandboxes:

```typescript
interface AgoraSDK {
  on(channel: string, handler: (payload: any) => void): void;
  off(channel: string, handler: (payload: any) => void): void;
  emit(channel: string, payload: any): void;

  storage: {
    get(key: string): Promise<any>;
    set(key: string, value: any): Promise<void>;
    delete(key: string): Promise<void>;
    list(prefix?: string): Promise<string[]>;
  };

  ui: {
    render(html: string): void;
    update(selector: string, html: string): void;
    onEvent(eventName: string, handler: (data: any) => void): void;
  };

  requestCapability(cap: CapabilityKey): Promise<boolean>;

  context: {
    userId: string;
    communityId: string | null;
    location: { lat: number; lng: number } | null;
    zoomLevel: ZoomLevel;
    timeOfDay: "morning" | "afternoon" | "evening" | "night";
  };
}
```

### 9.2 The Skill System

Skills are structured knowledge packages that guide the AI when generating apps.

```
skills/
├── expense-splitting/
│   ├── SKILL.md              # YAML frontmatter + instructions
│   ├── scripts/
│   │   └── validate_manifest.py
│   └── examples/
│       └── example-manifest.json
├── location-finder/
├── event-rsvp/
├── group-decision/
├── free-stuff-board/
├── countdown-timer/
├── ride-share/
└── study-group/
```

**SkillSchema Pydantic model (frontmatter fields):** `name`, `description`, `slot_types` (target slot types), `capability_hints`, `allowed_capabilities` (whitelist), `denied_capabilities` (blacklist), `community_scope` (personal/community/platform).

**Three-tier progressive loading:**

- **Tier 1: Metadata scan** (~100 tokens/skill, injected into system prompt). Budget: at 100 skills = ~10K tokens.
- **Tier 2: Full body load** (on-demand via tool call). The full skill instructions (< 500 lines / ~2–4K tokens) are injected when the LLM selects a skill.
- **Tier 3: Validation scripts** (subprocess, output-only enters LLM context). The script code never enters the LLM context — only its output does.

**Design decisions:** Tier 1 keeps prompt costs constant regardless of skill count. Tier 2 provides deep domain knowledge only when needed. Tier 3 enables deterministic validation without wasting tokens on validation logic.

### 9.3 App Generation Flow

```
backend/app-engine/
├── skill_schema.py
├── skill_loader.py           # Three-tier progressive loader
├── spec_builder.py           # Agentic loop: intent → spec (skill-aware)
├── app_generator.py          # Spec → manifest + SDK code + UI
├── safety_review.py          # AI code review (runs as Tier 3 script)
├── manifest_validator.py
├── versioning.py             # Content-addressed immutable versions
├── community_publishing.py
└── app_store.py
```

**The creation flow (7-step agentic loop with self-correction):**

1. **Receive user intent** (natural language description).
2. **Inject Tier 1 skill metadata** into system prompt. The LLM sees all skill names + descriptions.
3. **LLM selects relevant skills** by calling a `load_skill` tool (returns Tier 2 full body).
4. **LLM generates app spec** informed by skill instructions. Structured JSON: `{name, description, slot_types, capabilities, bus_channels, data_model, ui_components, behavior}`.
5. **Run Tier 3 validation scripts** from selected skills. Feed PASS/FAIL + errors back to the LLM.
6. **Self-correct** if validation fails. Repeat up to 3 correction attempts.
7. **Emit final spec** when validation passes.

**Available tools for the spec builder LLM:**
- `load_skill(name)` → returns full skill body (Tier 2).
- `validate_spec(skill_name, spec_json)` → runs Tier 3 validation script.
- `list_bus_channels()` → returns available message bus channels and payload types.
- `list_capabilities()` → returns available capability keys and descriptions.

### 9.4 Safety Review

Runs inside the agentic loop as a Tier 3 script:
- Input: generated manifest JSON + app code.
- Checks: capability over-requesting, suspicious bus subscriptions (subscribing to `agent:*` or `identity:*` without justification), resource limit violations, XSS patterns in UI code, data exfiltration patterns (network calls to undeclared domains).
- Output: "PASS" or structured error list.

### 9.5 Community DNA Strands

A community's entire configuration is a forkable manifest:

```json
{
  "community_id": "slo-tenants-union",
  "name": "SLO Tenants United",
  "type": "hybrid",
  "scale": "neighborhood",
  "forked_from": "la-tenants-union@2.1.0",
  "geofence": {
    "center": { "lat": 35.2828, "lng": -120.6596 },
    "radius_meters": 15000
  },
  "interest_tags": ["housing", "tenant-rights", "mutual-aid"],
  "purpose_statement": "Organizing for fair housing in San Luis Obispo County.",
  "gate": {
    "geographic": true,
    "participatory": true,
    "vouch": false,
    "entry_ritual": "intro_post_housing_situation"
  },
  "soft_cap": 500,
  "apps": [
    { "app_id": "agora.feed.bridging", "enabled": true, "priority": "primary" },
    { "app_id": "agora.feed.renderer", "enabled": true },
    { "app_id": "agora.deliberation.polis", "enabled": true },
    { "app_id": "agora.moderation.jury", "enabled": true },
    { "app_id": "agora.civic.tasks", "enabled": true },
    { "app_id": "agora.mesh.ui", "enabled": true },
    { "app_id": "agora.spatial.rooms", "enabled": true },
    { "app_id": "agora.events", "enabled": true },
    { "app_id": "community.stu.rent-tracker", "enabled": true },
    { "app_id": "community.stu.legal-resources", "enabled": true }
  ],
  "governance_overrides": {
    "moderation_threshold": "60_percent_supermajority",
    "app_approval": "3_member_minimum",
    "ban_policy": "jury_required"
  },
  "features": {
    "map_layer": "persistent",
    "community_signal": "geographic",
    "mesh_networking": true,
    "civic_caching": true
  }
}
```

Forking a community = copying this file and modifying it. The evolutionary tree of community governance is literally a git-like history of these configurations.

---

## 10. Backend Services

### 10.1 Simplified Query Router

The router uses a curated model roster, Sonnet-class default, and rule-based escalation instead of the per-message ML classifier originally designed. The full ML-driven router is deferred to Phase 7+ when usage data justifies it.

```
backend/router/
├── roster.py                    # The curated model roster
├── escalation.py                # Trigger rules for routing above default
├── gateway.py                   # LiteLLM gateway
├── streaming_handler.py         # Consistent streaming interface to client
├── cost_tracker.py              # Per-request cost logging, budget alerts
├── feedback.py                  # Thumbs up/down + "✦ Deeper answer" tracking
├── voice/
│   ├── stt_adapter.py
│   ├── disfluency_cleaner.py
│   ├── prompt_enhancer.py
│   └── context_injector.py
└── observability/
    ├── route_logger.py          # Every decision: model, tier, trigger, latency, cost
    └── analytics.py             # Tier distribution, escalation rate, user satisfaction
```

**Design principles:**

1. **Sonnet-class default.** A model at the Sonnet 4.5 / GPT-4o tier handles 80-90% of queries well. Default to this tier.
2. **Escalate on known signals, not predicted complexity.** Hand-curated triggers for known high-stakes domains.
3. **"✦ Deeper answer" button.** The user is the best complexity classifier. One tap re-runs on a stronger model.
4. **Curated roster, not open registry.** 3-5 tested models. New models enter through an eval gauntlet.
5. **Log everything, optimize later.** Every routing decision, escalation, and thumbs-down is logged.

**Curated model roster:**

```python
class ModelTier(Enum):
    FLASH = "flash"          # Fast, cheap — trivial tasks, agent background work
    DEFAULT = "default"      # Sonnet-class — the workhorse, 80-90% of queries
    PREMIUM = "premium"      # Opus-class — complex reasoning, high-stakes domains

@dataclass
class RosterModel:
    id: str                  # LiteLLM model string
    tier: ModelTier
    context_window: int
    supports_tools: bool
    supports_vision: bool
    input_cost_per_1m: float
    output_cost_per_1m: float
    notes: str
```

Launch roster: Haiku 4.5 (flash), Sonnet 4 (default), Opus 4 (premium). Fallback chains per tier.

**Escalation rules:**

```python
@dataclass
class EscalationRule:
    name: str
    check: Callable[[str, ConversationContext], bool]
    reason: str

def select_tier(message: str, context: ConversationContext) -> ModelTier:
    for rule in ESCALATION_RULES:
        if rule.check(message, context):
            return ModelTier.PREMIUM
    return context.user_preset_tier  # speed/balanced/quality → flash/default/premium
```

Triggers include: explicit depth requests, high-stakes domains (legal, medical, financial), code generation signals, long conversation drift, and the "✦ Deeper answer" button.

**Growth path:** Simplified (Phase 3) → data-driven (Phase 7, analyze escalation logs) → full ML (Phase 9+, trained on thousands of real routing decisions).

### 10.2 AI Tool Registry

```
backend/router/tool_registry.py
backend/router/tool_search.py
backend/tools/
```

**ToolDefinition Pydantic model:** `name`, `description`, `input_schema` (JSON Schema, OpenAI format), `defer_loading`, `handler` (dotted path).

**Core tools (always loaded):** `web_search`, `search_places`, `get_friend_activity`.
**Deferred tools (discovered via tool_search):** `search_apps`, `query_bus_history`, `get_community_info`.

**tool_search meta-tool:** BM25/FTS5 search over tool `name` + `description` fields. Always injected alongside core tools. When the model calls `tool_search`, the result tools are added to the `tools` parameter for the next LLM call. Cap at 2 tool-expansion rounds per turn.

### 10.3 Community Engine

```
backend/community-engine/
├── bridging_algorithm.py
├── governance_adapter.py         # PolicyKit integration
├── deliberation_adapter.py       # Pol.is integration
├── community_config.py           # DNA Strand management
└── federation.py                 # Cross-community coordination
```

### 10.4 Inbox Engine

```
backend/inbox-engine/
├── message_ingestion.py
├── entity_resolution.py
├── project_inference.py
└── connector_framework.py
```

### 10.5 App Engine

```
backend/app-engine/
├── skill_schema.py
├── skill_loader.py
├── spec_builder.py
├── app_generator.py
├── safety_review.py
├── manifest_validator.py
├── versioning.py
├── community_publishing.py
└── app_store.py
```

### 10.6 Agent Engine

```
backend/agents/
├── orchestrator.py
├── morning_briefing.py
├── civic_proximity.py
├── specialist_registry.py

backend/agents/scripts/
├── compress_events.py        # Deterministic event summarization (no LLM)
├── gather_briefing_data.py   # Multi-source data collection for briefing (no LLM)
└── proximity_filter.py       # Geo-distance filtering + opt-in check (no LLM)
```

**Three-layer working memory architecture:**

- Layer 1: Raw event buffer (in-memory, last ~200 events). No LLM.
- Layer 2: Compressed summaries (~500 tokens each). LLM sees only these.
- Layer 3: Persistent user patterns (SQLite). Loaded on demand.

**Event handling at 200 users:**

- Watches: `content:new_post`, `messaging:received`, `user:location_changed`, `user:session_started`.
- On `user:session_started`: dispatch to morning briefing.
- On `user:location_changed`: call `proximity_filter.py`, notify if matches.
- On `content:new_post`: buffer to Layer 1, check notification triggers.

---

## 11. Community Taxonomy

Communities in Agora exist on a spectrum from place-rooted to interest-rooted. The platform treats them differently — not by excluding any type, but by understanding what each type is *for*. Every community has a gravitational pull toward place.

### 11.1 Place-Rooted Communities

**Definition:** Communities anchored to a geographic area. Members share physical space.

**Examples:** "Eastside Neighborhood," "Downtown SLO," "Building 4200 Residents"

Place-rooted communities produce bridging social capital almost automatically — neighbors are diverse by default. They get the full power of every spatial feature: map layers, civic tasks, mesh networking, Community Signals, civic caching.

**Membership gate:** Geographic verification — periodic geofence presence, civic cache check-ins, or vouching.

**Size constraints:** Block/building (15–50, intimate), Neighborhood (150–500, Dunbar's number), City/district (federations of neighborhoods).

**Feature surface:** Full access to all Agora features.

### 11.2 Interest-Rooted Communities

**Definition:** Communities organized around shared passion, identity, hobby, or worldview.

**Examples:** "SLO Rocket League," "Sourdough Bakers," "Trail Runners"

Interest communities on Agora have a gravitational pull toward place. They are regionally scoped by default — small enough for a LAN party, not a global subreddit. The interest gives people a reason to connect. The place gives that connection the possibility of becoming real.

**Membership gate:** Participation-based friction. Introduction post, quiz, contribution requirement, or vouching. Governed by PolicyKit.

**Size constraints:** Core community 50–300 members. Soft cap triggers fission nudge to split into regional chapters.

**Feature surface:** Most features, with spatial features adapted — map layer is event-driven (not persistent), Community Signal scoped to member network, mesh networking disabled, civic caching disabled.

### 11.3 Hybrid Communities

**Definition:** Purpose + place — shared interest bound to a specific location.

**Examples:** "SLO Tenant Union," "Eastside Climate Action," "Campus LGBTQ+ Alliance"

Hybrid communities get the full power of every Agora feature because they satisfy both conditions: shared purpose for deep engagement, and geographic co-location for physical action.

**Membership gate:** Both geographic and participatory.

**Feature surface:** Complete. Every feature Agora offers.

### 11.4 Community Type in the Kernel

```typescript
type CommunityType = "place-rooted" | "interest-rooted" | "hybrid";
type CommunityScale = "intimate" | "neighborhood" | "federated";

type MembershipGate = {
  geographic: boolean;
  participatory: boolean;
  vouch: boolean;
  custom_policykit_rule?: string;
};

type CommunityMeta = {
  community_id: string;
  name: string;
  type: CommunityType;
  scale: CommunityScale;
  geofence?: { center: { lat: number; lng: number }; radius_meters: number; polygon?: Array<{ lat: number; lng: number }> };
  interest_tags?: string[];
  purpose_statement?: string;
  gate: MembershipGate;
  member_count: number;
  soft_cap: number;
  federation_parent?: string;
  features: CommunityFeatureSet;
};

type CommunityFeatureSet = {
  map_layer: "persistent" | "event-driven" | "disabled";
  community_signal: "geographic" | "member-network" | "disabled";
  mesh_networking: boolean;
  civic_caching: boolean;
  spatial_audio: boolean;
  deliberation: boolean;
  micro_app_creation: boolean;
  digital_juries: boolean;
};
```

| Feature | Place-Rooted | Interest-Rooted | Hybrid |
|---------|:---:|:---:|:---:|
| Persistent map layer | ✅ | ❌ (event-driven) | ✅ |
| Community Signal (geographic) | ✅ | ❌ | ✅ |
| Community Signal (member-network) | ✅ | ✅ | ✅ |
| Mesh networking | ✅ | ❌ | ✅ |
| Civic caching | ✅ | ❌ | ✅ |
| Spatial audio rooms | ✅ | ✅ | ✅ |
| Bridging-based feed | ✅ | ✅ | ✅ |
| Pol.is deliberation | ✅ | ✅ | ✅ |
| Digital juries | ✅ | ✅ | ✅ |
| Micro app creation | ✅ | ✅ | ✅ |
| Civic-proximity agent | ✅ | ❌ | ✅ |

**Community creation flow (`agora.community.creator`):** Guided experience — choose type, define gate, set geofence/region, select from template DNA Strands.

**Entry ritual (`agora.community.gate`):** Executes the membership gate defined in the DNA Strand.

**Fission flow:** When a community crosses its soft cap, the AI guide suggests splitting into chapters with a one-click flow that creates child communities and sets up federation links.

### 11.5 Federation

A federation is not a community — it is a protocol for communities to coordinate:

- Shared Pol.is instance for cross-community deliberation
- Shared Community Signal channel for propagating alerts
- Shared event calendar
- Delegate model with PolicyKit-governed elections
- Chapter management — split, merge, link communities

Federation is implemented as `agora.federation.assembly`, a canvas micro app that subscribes to message buses of multiple communities through `federation:cross_community_read` capability. The kernel enforces read-only access without explicit PolicyKit consent.

---

## 12. Development Phases & Sequencing

### What's Already Built (Phases 0–2) — COMPLETE

- ✅ Full design token system (colors, typography, spacing, elevation, motion, haptics)
- ✅ All primitive components + Web Components export
- ✅ MapCanvas with warm MapLibre styling, SemanticZoomController, PanelManager, PeekBar, RadialContextMenu
- ✅ Fraunces + Inter fonts, GrainOverlay
- ✅ Capability system (registry, validator, prompt UI, store)
- ✅ Message bus (pub/sub, channel registry, capability gate, priority lanes)
- ✅ App runtime sandbox (WebView sandbox, lifecycle controller, resource monitor)
- ✅ Slot manager, SlotRenderer, manifest parser/validator
- ✅ Scoped storage (personal, community, app-private)
- ✅ Agora SDK interface
- ✅ `agora.civic.tasks` (map-layer + panel + radial-action, 8 integration tests)
- ✅ `agora.feed.bridging` (headless, vote-diversity heuristic)
- ✅ `agora.feed.renderer` (panel, finite scroll, Web Components)
- ✅ `agora.agent.attention-scorer` (heuristic, 119 tests across 12 suites)
- ✅ Error boundary, persistent lifecycle mode, agent metadata, priority lanes

---

### Phase 3: The Daily Driver

**Goal:** Make Agora the app your friends open every morning. Three pillars: AI assistant, local map, and messaging.

#### 3A: AI Chat & Simplified Router

- [ ] **Define the curated model roster** in `roster.py` — 3-5 models across flash/default/premium tiers, with fallback chains
- [ ] **Build `escalation.py`** — rule-based tier selection: keyword triggers for high-stakes domains, explicit user depth requests, structural complexity signals
- [ ] **Build `gateway.py`** — LiteLLM gateway with streaming, circuit breakers, and provider fallback
- [ ] **Build `cost_tracker.py`** — per-request and per-user cost logging, daily budget enforcement
- [ ] **Build `feedback.py`** — thumbs up/down collection + "✦ Deeper answer" escalation event logging
- [ ] **Build `route_logger.py`** — log every decision: message hash, tier, trigger, model, latency, cost
- [ ] **Wire up streaming response rendering** with spring fade-in
- [ ] **Build `agora.ai.chat` as a canvas + widget micro app** — full AI chat with VoiceOrb widget, three user presets
- [ ] **Build the "✦ Deeper answer" button** — re-runs on premium tier, replaces response, logs escalation
- [ ] **Build voice pipeline** — STT (Whisper) → disfluency cleaner → context injector → prompt enhancer
- [ ] **Build the eval gauntlet** — 10-15 test prompts for candidate model evaluation
- [ ] **Add `source` field** to `search:query` for agent escalation prep

#### 3B: Social Feed

- [ ] **Build `agora.social.feed`** as a panel + feed-renderer micro app — chronological, optional friends-nearby boost, same bus contract as bridging feed
- [ ] **Support post types** — text, photo, location pin, event, question/poll
- [ ] **Build reactions** — not likes. "I'm in," "Tell me more," or short reply. Visible only to poster and reactor.

#### 3C: Native Messaging

- [ ] **Build `agora.messaging.core`** as a canvas + panel + notification-handler micro app — DMs, group chats, community channels
- [ ] **E2E encryption** via kernel keypair system
- [ ] **Threaded replies, limited reactions, voice notes**
- [ ] **Map integration** — proximity indicators
- [ ] **Push notifications** — capability-gated, user-revocable

#### 3D: Local Knowledge Map

- [ ] **Build `agora.social.places`** as a map-layer + panel micro app — friend-curated pins with categories
- [ ] **Pre-seed with 30–50 pins** in SLO
- [ ] **Upvoting/commenting** on pins, consensus-based visual boost
- [ ] **Category filters** — Food, Study, Outdoors, Events, Free Stuff, Hidden Gems

---

### Phase 3A: AI Tool Registry

**Goal:** Give the AI chat access to Agora-specific tools without bloating every prompt.

- [ ] **Define `ToolDefinition` Pydantic model** — name, description, input_schema, defer_loading, handler
- [ ] **Register the 5 launch tools** — web_search, search_places, get_friend_activity (core); search_apps, query_bus_history, get_community_info (deferred)
- [ ] **Implement tool handler stubs** — async functions returning realistic mock data
- [ ] **Build `tool_search.py` meta-tool** — BM25/FTS5 search over registry, always injected alongside core tools
- [ ] **Modify the agentic loop** for tool expansion — tool_search results become available tools in next turn. Cap at 2 expansion rounds.
- [ ] **Integration test** — end-to-end tool discovery flow

---

### Phase 4: The Creation Moment

**Goal:** Open the "Speak It Into Being" app creation pipeline. Users describe what they want; a skill-aware agentic loop generates a working micro app.

- [ ] **Define `SkillSchema` Pydantic model** — frontmatter fields: name, description, slot_types, capability_hints, allowed/denied capabilities, community_scope
- [ ] **Build `skill_loader.py`** with three-tier loading — metadata scan, full body load, validation scripts
- [ ] **Build `spec_builder.py`** agentic loop — 7-step flow with self-correction, skill-aware
- [ ] **Build `safety_review.py`** as a Tier 3 script inside the agentic loop
- [ ] **Build `app_generator.py`** — spec → manifest + SDK code + Web Component UI
- [ ] **Write the first 4 skills** — expense-splitting, event-rsvp, group-decision, location-finder
- [ ] **Build `agora.ai.create`** as a canvas micro app — conversational builder, < 2 minute flow
- [ ] **Build the instant preview sandbox** — test before save
- [ ] **Build personal deploy (Layer 1)** and community sharing (Layer 2, lightweight)
- [ ] **Build AppRemixer** — fork + modify existing apps
- [ ] **Build AppLibrary** — browse and search community apps
- [ ] **Build skill contribution flow** — extract skills from successful creations

---

### Phase 5: The Living Platform

**Goal:** The platform sustains itself. Friends build tools, invite networks, and the agent constellation provides daily value.

#### 5A: Profile Customization

- [ ] **Build `agora.profile.customizer`** — AI-generated CSS token overrides, manual tweaking
- [ ] **Avatar editor** — illustrated style, earned accessories

#### 5B: Agent Constellation

- [ ] **Build `compress_events.py`** — deterministic event summarization (no LLM)
- [ ] **Build `gather_briefing_data.py`** — multi-source data collection (no LLM)
- [ ] **Build `proximity_filter.py`** — geo-distance filtering (no LLM)
- [ ] **Build `orchestrator.py`** — persistent triage agent with three-layer working memory
- [ ] **Build `specialist_registry.py`** — mirrors tool_registry pattern
- [ ] **Build `morning_briefing.py`** — warm 2-4 sentence daily summary
- [ ] **Build `civic_proximity.py`** — social proximity notifications
- [ ] **Integration test** — orchestrator → specialist dispatch flow

#### 5C: Canvas & Widget Infrastructure

- [ ] **Build `CanvasManager.tsx`** — full-screen workspace host
- [ ] **Build `WidgetLayer.tsx`** — persistent widget overlay
- [ ] **Build `AppSwitcher.tsx`** — map ↔ canvas navigation
- [ ] **Add canvas and widget slot types** to `slot-registry.ts`

#### 5D: Feedback Infrastructure

- [ ] **Build `agora.meta.feedback`** — shake-to-report-bug
- [ ] **In-app changelog** — "What's new" panel
- [ ] **Simple analytics** — DAU, feature usage, crash rate. Aggregated only.

---

### Phase 6: Community Infrastructure

**Trigger:** Any single community crosses ~150 members OR more than 3 distinct communities exist.

#### 6A: Governance Scaffolding

- [ ] **Build PolicyKit adapter** for modular community governance
- [ ] **Surface governance UI** — constitution editor, rule proposals, voting
- [ ] **Build digital jury system** — Kleros-model random selection, blind voting
- [ ] **Build moderation pipeline** — AI flag → community review → jury escalation

#### 6B: Bridging & Deliberation

- [ ] **Activate `agora.feed.bridging`** for communities with ~100+ members
- [ ] **Build `agora.deliberation.polis`** — statement voting, PCA visualization
- [ ] **Build `agora.agent.bridge-spotter`** — make bridging visible

#### 6C: Civic & Social Features

- [ ] **Build `agora.spatial.rooms`** — 2D avatar canvas, WebRTC proximity audio
- [ ] **Build `agora.events`** — type-aware, location-pinned events
- [ ] **Build civic task integration** with real local data

#### 6D: Identity & Auth Hardening

- [ ] **Implement real OAuth** (replace stubs)
- [ ] **Implement Proof of Humanity** for bot prevention
- [ ] **Keypair management** for E2E encryption
- [ ] **Community membership verification** — geographic and participatory

#### 6E: Community Taxonomy Implementation

- [ ] **Extend kernel** with community type, scale, gate, feature set types
- [ ] **Build type-aware feature gating** in capability system
- [ ] **Build `agora.community.creator`** — guided community creation flow
- [ ] **Build `agora.community.gate`** — entry ritual execution
- [ ] **Build community fission flow** — chapter splitting at soft cap
- [ ] **Extend Community DNA Strands** with type metadata

---

### Phase 7: Intelligence Layer

**Goal:** Upgrade the agent constellation to full power.

- [ ] **Upgrade `agora.agent.attention-scorer`** from heuristic to ML (DistilBERT, ~65MB ONNX)
- [ ] **Build `agora.agent.action-extractor`** — structured action objects from messages
- [ ] **Build `agora.agent.deliberation-observer`** — Pol.is consensus tracking, notifications
- [ ] **Extend morning briefing** to cover civic + productivity context
- [ ] **Extend `spec_builder.py`** to generate agent manifests from natural language
- [ ] **Extend `safety_review.py`** with agent-specific checks
- [ ] **Build tailored agent bundles** for community types
- [ ] **Begin data-driven router optimization** — analyze escalation logs, train lightweight classifier

---

### Phase 8: Inbox & External Integration

#### 8A: Unified Inbox

- [ ] **Build `agora.inbox.core`** — attention scoring, classification, action extraction
- [ ] **Build `agora.inbox.compose`** — universal composer with AI drafts
- [ ] **Build Gmail and Slack connectors**
- [ ] **Build entity resolution** and project inference
- [ ] **Additional connectors** — Outlook, Teams, Discord, Canvas, GitHub

#### 8B: External Web App Integration

- [ ] **Build external-app-host, bridge-injector, navigation-guard**
- [ ] **Build `agora.external.browser`** — general-purpose web browser canvas
- [ ] **Build Tier 1 banking wrapper** — prove wrapping in <10 minutes
- [ ] **Build Tier 2 Spotify wrapper** — bridge adapter + now-playing widget
- [ ] **Extend `spec_builder.py`** to generate bridge adapters

#### 8C: Productivity Apps

- [ ] **Build `agora.notes`** — Markdown editor with CRDT collaborative editing
- [ ] **Build `agora.projects`** — kanban board + task counter widget
- [ ] **Build `agora.calendar`** — timeline canvas + spatial event map-layer + widget
- [ ] **Build app bundle templates** for community types

---

### Phase 9: Mesh, Polish & Scale

- [ ] **Implement kernel mesh transport** (BLE)
- [ ] **Build `agora.mesh.ui`**
- [ ] **Train distilled voice-to-prompt model** (T5-small → ONNX → CoreML/NNAPI)
- [ ] **Train on-device message classifier** (DistilBERT → ONNX)
- [ ] **Full accessibility audit** (WCAG AA, screen reader, reduce motion, font scaling)
- [ ] **Performance audit** (60fps, battery, memory per sandbox)
- [ ] **Sound design polish** (15–20 earcon library)
- [ ] **Security audit** of sandbox boundaries and capability enforcement
- [ ] **Full ML-driven router** — trained on real routing decisions, benchmark-calibrated

---

### Phase 10: The Cooperative Transition

**Trigger:** User base has grown beyond personal network. Multiple cities.

- [ ] **Introduce subscription model** — Neighbor (free) + Member ($5–7/mo) + AI top-ups
- [ ] **Publish first transparency report** — open finances
- [ ] **Establish cooperative governance** — Class A user members, Class B worker members
- [ ] **Build cooperative voting infrastructure**
- [ ] **First Civic Creator Residency**
- [ ] **Community-funded AI pools**
- [ ] **Begin Exit to Community trajectory**

---

### Phase 11: Ecosystem Growth

- [ ] **External App Directory** of pre-configured wrappers
- [ ] **Bridge Adapter Marketplace**
- [ ] **Build lifestyle apps** — `agora.habits`, `agora.journal`, `agora.finance`
- [ ] **Community app bundles** in the app store
- [ ] **First apps enter Agora Commons** through democratic vote
- [ ] **Multi-city expansion playbook**
- [ ] **Selective AR moments** — geofenced civic data overlays

---

### Phase 12: Federation

- [ ] **Build `agora.federation.assembly`** canvas micro app
- [ ] **Build `federation:cross_community_read` capability**
- [ ] **Build delegate election flow** (PolicyKit-governed)
- [ ] **Build cross-community Signal propagation**
- [ ] **Build chapter management** — split, merge, link communities

---

## 13. The Transition Map: When Features Activate

| Feature | < 50 users | 50–200 users | 200–500 users | 500+ users |
|---------|-----------|--------------|---------------|------------|
| AI Chat & Search | ✅ Full | ✅ Full | ✅ Full | ✅ Full |
| Social Feed (chronological) | ✅ Primary | ✅ Primary | Available | Available |
| Bridging Feed Algorithm | Hidden | Hidden | ✅ Activates | ✅ Primary |
| Messaging (DMs, groups) | ✅ Full | ✅ Full | ✅ Full | ✅ Full |
| Local Knowledge Map | ✅ Full | ✅ Full | ✅ Full | ✅ Full |
| App Creation ("Speak It Into Being") | ✅ Full | ✅ Full | ✅ Full | ✅ Full |
| Profile Customization | ✅ Full | ✅ Full | ✅ Full | ✅ Full |
| Morning Briefing Agent | Simple | ✅ Full | ✅ Full | ✅ Full |
| Civic-Proximity Agent | Social only | Social | ✅ Civic + Social | ✅ Full |
| Widgets & Canvas | ✅ Full | ✅ Full | ✅ Full | ✅ Full |
| Moderation (informal) | You know them | You know them | Transitioning | Formal |
| PolicyKit Governance | Hidden | Hidden | Available | ✅ Active |
| Digital Juries | Hidden | Hidden | Hidden | ✅ Active |
| Pol.is Deliberation | Hidden | Hidden | Available | ✅ Active |
| Cooperative Voting | Hidden | Hidden | Hidden | ✅ Active |
| Spatial Audio Rooms | Available | ✅ Active | ✅ Active | ✅ Active |
| Civic Tasks (gamified) | Available | Available | ✅ Active | ✅ Active |
| Mesh Networking | Hidden | Hidden | Available | ✅ Active |
| Unified Inbox (Gmail, Slack) | Hidden | Hidden | Available | ✅ Active |
| External App Wrapping | Hidden | Hidden | ✅ Active | ✅ Active |
| Subscription Pricing | Free | Free | Introduced | ✅ Active |
| Cooperative Governance | N/A | N/A | N/A | ✅ Active |

"Hidden" means the kernel supports it but the UI doesn't surface it. "Available" means users can find and enable it. "Active" means it's part of the default experience.

---

## The Onboarding Flow

### First Open (30 seconds)

1. **Splash:** The mascot waves. "Welcome to Agora. [Friend's name] invited you."
2. **Name + avatar:** Quick setup. AI-generated avatar suggestion from a selfie or keyword. Skip option.
3. **Map appears:** Centered on SLO. Pins already visible from `agora.social.places`. Friend avatars dotted on the map.
4. **No governance explanation. No cooperative bylaws.** Just: "Here's what's happening around you."

### First Minute (discovery)

5. **Tap a pin:** See a friend's recommendation. "Alex says: best cold brew in town."
6. **PeekBar pulses gently:** "3 new posts from friends." Swipe up to see the social feed.
7. **VoiceOrb glows:** "Ask me anything." First AI interaction — local context aware.

### First Day (value delivery)

8. **Push notification:** Morning briefing. "Good morning. Your roommate posted about free pizza in the quad. 2 friends are at the library."
9. **See a friend's micro app on the map:** "Sarah built a Study Group Finder. Tap to try it."
10. **Curiosity trigger:** "Want to build something for the map? Tell me what you need."

### First Week (habit formation)

11. User has checked the map 3–4 times. Sent messages. Maybe created an app. They tell a friend.
12. That friend downloads Agora. The cycle continues.

---

## Appendix: The Rebuild-Without-Breaking Guarantee

This architecture is designed so that any feature can be replaced at any time without touching any other feature:

- **Swap the social feed for bridging?** Disable `agora.social.feed`, enable `agora.feed.bridging`. The renderer subscribes to the same `feed:content_ranked` channel either way.
- **A friend builds a better places app?** It registers for `map-layer` and `panel` slots. If the community likes it, it replaces `agora.social.places`. Zero kernel changes.
- **A community wants formal governance?** Enable `agora.moderation.jury` and wire up PolicyKit. The kernel already supports it — the UI was just hidden.
- **A user creates a custom morning briefing?** Fork `agora.agent.morning-briefing`, adjust the prompt, deploy to personal scope.
- **Strangers join and need formal moderation?** Activate the full moderation stack. The kernel was always ready.
- **Want Notion instead of agora.notes?** Wrap Notion's web app as a Tier 1 external canvas. Or build a Tier 2 bridge that syncs Notion pages. Or fork agora.notes. Three paths, zero kernel changes.
- **A co-op wants their own project board?** Fork `agora.projects`, add custom fields, deploy to community scope. The widget still shows "tasks due today" because it reads from the same bus channel.
- **A student group wants Canvas LMS inside Agora?** Create a Tier 1 external wrapper. Add a Tier 2 bridge for assignment deadlines. The morning briefing agent picks them up automatically.
- **A tenant union needs urgency scoring tuned for legal deadlines?** Fork `agora.agent.attention-scorer`, retrain with legal domain data. The inbox panel reads `inbox:attention_scored` regardless of which agent emitted it.

The contract is the manifest. The sandbox is the enforcement. The message bus is the glue. The kernel is the ground. The friends are the foundation.

---

*This document is the single, complete reference for the Agora project — a cooperatively owned, map-based mobile platform where every feature is a sandboxed micro app on a shared kernel. It synthesizes the full architecture, community taxonomy, simplified router design, skill-based app creation pipeline, agent constellation with programmatic context management, and friends-first launch strategy into one standalone document. The destination is a member-owned cooperative, civic platform. The path starts with 200 people who trust each other.*
