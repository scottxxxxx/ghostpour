# CloudZap → GhostPour Renaming Strategy

> **Created:** March 23, 2026
> **Status:** Domain secured, rename not yet started
> **Domain:** ghostpour.com (TBD — being secured)

---

## Why Rename

"CloudZap" doesn't describe what the product does. It sounds like a cloud infrastructure tool, a CDN, or a utility that "zaps" cloud resources. Nothing about it says "LLM gateway," "AI routing," "subscription management," or "invisible proxy." The name was a placeholder that stuck.

---

## The New Name: GhostPour

### The Elevator Pitch

You walk into a bar. A drink appears in front of you. It's exactly what you should be drinking at your price point. You never saw the bartender. You never saw the bottle. You never saw the pour. It just showed up, and it's good.

That's GhostPour. Your app sends a question. GhostPour picks the right AI model for that user's subscription tier, handles the auth, tracks every token, enforces their quota, and sends back the answer. The user sees your app's response. They never see the pour.

Free tier gets the well. Pro gets the call. Ultra gets the top shelf. Same glass, same bar, different bottle — and nobody sees which one came off the rail.

*Your app is the bar. We're the ghost behind it.*

### Non-Technical Explanation

GhostPour is the middleman between your app and the AI that powers it.

Think of it like a bar. Your app is the bartender — the face the customer sees. Behind the wall, there are a dozen different bottles of liquor from different brands, all at different prices. The customer doesn't want to pick a bottle. They don't want to know what anything costs per ounce. They just want to pay their monthly tab and get a good drink when they ask for one.

GhostPour is the person behind the wall who hears the order, knows the customer is on the $4.99 plan, grabs the right bottle, pours the right amount, and slides it through the window. The customer gets their drink. The bartender gets the credit. GhostPour tracks how many drinks were poured, makes sure nobody goes over their limit, and swaps in a different bottle if one runs out — and nobody on the other side of the bar notices a thing.

Without something like GhostPour, the app developer would have to sign contracts with every AI company, manage all the API keys, build their own billing system, figure out rate limiting, track costs per user — basically run their own liquor distribution business just to serve drinks. GhostPour handles all of that so the developer can focus on making the app great and the user can just enjoy the experience.

---

## Why GhostPour Wins

### As a name
It sounds cool before you even know what it does. It sounds like a brand. It sounds like something you'd see on a sticker on someone's laptop and ask about.

### As a story
The bar metaphor is universal. Everyone has ordered a drink. Everyone understands "the house picks for you." You can explain GhostPour to a college freshman, an investor, your mom, and a senior engineer, and they all get it on the first try with a different level of depth.

### As marketing
The ghost imagery gives you everything:

**Logo concepts:**
- Ghost holding a bottle
- Ghost pouring into a glass
- Ghost silhouette with a stream of liquid
- Translucent/ethereal treatment of any of the above

**Color palette:**
- Translucent, ethereal tones
- Dark backgrounds (bar aesthetic)
- Accent color for "the pour" (amber, electric blue, or brand color)

**Taglines:**
- "The pour you never see."
- "Your app gets the credit. We get it done."
- "Same glass. Different bottle. Nobody knows."
- "AI on tap. Invisibly served."
- "You order. We pour. They never know."
- "The invisible hand behind every AI response."

### As a pitch
"Why is it called GhostPour?" is a question people will actually ask, and the answer is a 30-second story they'll retell. That's the kind of name that markets itself.

---

## The Bar Vocabulary — Natural Feature Naming

The bar metaphor extends naturally to every product concept:

| Product Concept | Bar Term | Description |
|----------------|----------|-------------|
| Subscription tiers | **Well / Call / Top Shelf** | Free=well, Pro=call, Ultra=top shelf |
| Model routing | **The Pour** | Picking the right bottle for the order |
| Rate limiting | **Cut Off** | "Sorry, you've had enough for tonight" |
| Usage tracking | **The Tab** | Running tab per customer |
| Allocation limit | **Last Call** | Monthly allocation approaching zero |
| Overage credits | **After Hours** | Keep drinking past closing (paid extra) |
| API keys (BYOK) | **BYOB** | Bring your own bottle |
| On-device fallback | **Designated Driver** | Always there, always sober, always free |
| Auto model selection | **House Pour** | Trust the house to pick |
| Context Quilt | **The Regular** | "The usual" — the bartender remembers you |
| Admin dashboard | **Back Office** | Where the owner checks the numbers |
| Provider adapters | **Distributors** | The companies that supply the bottles |
| Health check endpoint | **Open/Closed Sign** | Is the bar open? |

---

## Runner Up: ShimLayer

**ShimLayer** — *The thinnest piece that makes everything fit.*

A shim is a thin piece of material that makes two incompatible things fit together perfectly. ShimLayer is the thin invisible layer between your app and AI providers. One API call in, the right AI model out, metered and billed.

**Why it lost to GhostPour:** ShimLayer is accurate but it's an engineer explaining something to another engineer. The metaphor dies outside a room of developers. Nobody at a dinner party has ever said "you know what a shim is?" and gotten an excited response. GhostPour works for every audience.

ShimLayer remains available as a fallback domain if needed.

---

## Naming Process

The following names were considered and rejected (all domains were registered/unavailable):

Undertow, Riptide, SurfRelay, Drift, Swell, Tideline, Backplane, Switchboard, Conduit, Waypoint, Aqueduct, Crossbar, Turnstile, DeepCurrent, ModelMux, PromptWire, ShoreLine, Channel, Meridian, Undercurrent, Inkwell, Quillbox, Parchment, Spyglass, Periscope, Sidecar, Copilot, Tandem, Rearview, Whisperbox, Feedline, Hotline, Dropline, Tagalong, Ridealong, Backseat, Shotgun, Coattails, Tailwind, Slipstream, Drafting, Peloton, Pacer, Cadence, Tempo, Metronome, Baton, Handoff, Relay, Dispatch, Outpost, Watchtower, Lighthouse, Beacon, Foghorn, Lantern, Flare, Signal, Semaphore, Morse, Cipher, Codex, Rosetta, Skeleton, Passkey, Locksmith, Keystone, Capstone, Lodestone, Magnet, Compass, Astrolabe, Sextant, Almanac, Tidewatch, Driftwood, Sandbar, Jetty, Wharf, Pier, Mooring, Anchor, Buoy, Helm, Rudder, Keel, Ballast, Bilge, Galley, Manifest, Cargo, Payload, Freight, Parcel, Depot, Clearinghouse, ContextBridge, ContextConduit, ContextGate, ContextRelay, and ~60 others.

**Design criteria that led to GhostPour:**
1. Must not click immediately — should provoke "why is it called that?"
2. Must click perfectly after a 30-second explanation
3. Must be explainable to non-technical audiences
4. Must extend naturally to product vocabulary (features, tiers, concepts)
5. Must have strong visual/brand identity potential
6. Must be memorable enough to retell

---

## Rename Checklist (When Ready)

### Code & Infrastructure
- [ ] Rename GitHub repo (`cloudzap` → `ghostpour`)
- [ ] Update all `CZ_` env var prefixes → `GP_` (or keep `CZ_` for backwards compat, decide)
- [ ] Update Docker image/container names
- [ ] Update `cz.shouldersurf.com` → new subdomain (e.g., `gp.shouldersurf.com` or `api.ghostpour.com`)
- [ ] Update Nginx Proxy Manager routing
- [ ] Update GHCR package names
- [ ] Update GitHub Actions workflows

### iOS App (Shoulder Surf)
- [ ] Update `CloudZapProvider.swift` → rename class/file
- [ ] Update `CloudZapAuthManager.swift` → rename class/file
- [ ] Update all `"cloudzap"` string references in `LLMService.swift`, `LLMProviders.json`
- [ ] Update `ShoulderSurf.entitlements` if bundle ID references change
- [ ] Update Settings UI strings ("CloudZap" → "GhostPour")
- [ ] Update CLAUDE.md references

### Documentation
- [ ] Update CloudZap CLAUDE.md
- [ ] Update Shoulder Surf CLAUDE.md
- [ ] Update Subscription_Tiers.md
- [ ] Update Context_Slot_System.md
- [ ] Update planning docs references
- [ ] Update memory files

### External
- [ ] Secure ghostpour.com domain
- [ ] Update GitHub repo description
- [ ] Update any external references (planning docs, handoff docs)
