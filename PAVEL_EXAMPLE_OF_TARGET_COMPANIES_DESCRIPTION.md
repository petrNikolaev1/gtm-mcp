# iGaming Providers — Gathering Brief

## Who We Are (Seller Context)

**Mifort** — IT outsourcing from Tallinn. Web, Mobile, ML. $25/hr seniors.
We already have 3 iGaming anti-fraud cases (affiliate fraud monitoring, ML bonus abuse detection, fake high-value player detection). Stack: Python, Node.js, React, Kafka, FastAPI, PostgreSQL, Redis, AWS.

**What we sell to iGaming:** dedicated dev teams for platform development, anti-fraud/ML systems, back-office tools, dashboards, integrations. Not consulting — actual engineering.

---

## Segment: iGaming Providers

### Who They Are

Companies that BUILD the technology layer of online gambling. They don't operate casinos themselves — they sell platforms, games, tools, and infrastructure TO casino operators.

**Sub-segments (all valid targets):**

| Sub-segment | What they build | Why they need us |
|-------------|----------------|-----------------|
| **Platform providers** | Turnkey casino/sportsbook platforms (PAM, player management, CMS, bonus engines) | Constantly shipping features, need frontend/backend/mobile teams |
| **Game studios / Game providers** | Slot games, crash games, live casino, RNG engines | Need web developers (HTML5/Canvas/WebGL), backend for game servers |
| **Aggregators / Game distributors** | APIs that connect 100+ game studios to operators | Integration-heavy, need backend engineers for API gateway, data pipelines |
| **Sports data & odds providers** | Live odds feeds, trading tools, risk management for sportsbooks | Real-time systems, need backend (Node/Go/Python) + frontend dashboards |
| **Payment providers (iGaming-specific)** | Deposit/withdrawal processing, crypto payments, KYC for gambling | High-load transaction systems, compliance tooling, need senior backend |
| **Anti-fraud & compliance** | Player verification, responsible gambling tools, AML screening | ML models, real-time scoring — exactly what our cases cover |
| **Affiliate & marketing platforms** | Affiliate tracking, CPA/RevShare management, campaign analytics | Dashboard-heavy products, need React + data engineering |
| **BI & analytics for iGaming** | Player analytics, retention tools, CRM for operators | Data pipelines, ML models, dashboards |

### ICP Criteria

- **Has own product** (platform, game engine, API, SaaS tool) — they DEVELOP software
- **Size: 20-500 employees** — big enough to have budget, small enough to outsource
- **Growing** — hiring, raising rounds, expanding to new markets
- **Geo: Global** (but HQ in Europe, Malta, Isle of Man, Gibraltar, Cyprus, Curacao, UK, Nordics, Eastern Europe, Israel, Asia — iGaming hubs)
- **Uses modern stack** — not legacy COBOL shops. JavaScript, Python, Go, Java, .NET

### Signals of a Good Target

- Job postings for developers (they're scaling their tech team)
- "We're hiring" on the website
- Recently raised funding
- Expanding to new regulated markets (US, Brazil, LatAm, Africa)
- Partner/integration pages listing 50+ operators
- API documentation publicly available
- Multiple products in portfolio (platform + games + payments)

---

## Top 5 Examples (Apollo Filter Seeds)

These are the SEXIEST companies in the segment. Use them to reverse-engineer Apollo filters.

| # | Company | Domain | Why they're perfect |
|---|---------|--------|-------------------|
| 1 | **SoftSwiss** | softswiss.com | Turnkey casino platform + game aggregator. 700+ brands use them. Malta/Belarus. Constantly hiring devs. The archetype. |
| 2 | **Pragmatic Play** | pragmaticplay.com | Top-3 game studio globally. Slots, live casino, bingo. Gibraltar/Malta. Massive engineering team, always scaling. |
| 3 | **BetConstruct** | betconstruct.com | Full-stack iGaming platform: sportsbook, casino, poker, fantasy. Armenia/UK. 3000+ partners. |
| 4 | **Digitain** | digitain.com | Sportsbook & platform provider. Armenia. Growing fast in LatAm/Africa. Tech-heavy, need developers. |
| 5 | **Evoplay** | evoplay.games | Game studio (150+ games). Ukraine/Cyprus. Modern HTML5 stack, heavy on animation/frontend. |

**Why these 5:** They span platform providers, game studios, and full-stack providers across the main iGaming geographies (Malta, Gibraltar, Armenia, Cyprus, Ukraine). Apollo should find the cluster of similar companies around them.

---

## Shit List (EXCLUDE These)

### Casino Operators (NOT providers)

Companies that RUN online casinos/sportsbooks for end players. They USE the platforms — they don't BUILD them. Wrong audience.

**How to spot:** "Play now", "Bet now", deposit bonuses on homepage, gambling license for player-facing operations, Trustpilot reviews from players.

**Examples to exclude:**
- Bet365, 888 Holdings, Flutter/FanDuel, DraftKings — massive operators
- LeoVegas, Betsson, Kindred, Casumo — mid-size operators
- Any site where you can actually place a bet or spin a slot

**Exception:** Some companies are BOTH operator AND provider (e.g., Playtech operates AND sells platform). If they have a clear B2B product arm that sells to other operators — they're a target.

### Affiliate / SEO Sites

Websites that review casinos, compare bonuses, and earn affiliate commission. They write content, not code.

**How to spot:** "Best casinos 2026", "Top 10 slots", casino reviews, bonus comparison tables, affiliate disclaimers in footer.

**Examples:** AskGamblers, Casino.org, Gambling.com Group, Catena Media, Better Collective

### Land-Based / Lottery Only

Companies focused on physical slot machines, VLTs, lottery terminals. Legacy hardware, no overlap with web/mobile dev.

**How to spot:** "Land-based solutions", physical machine photos, lottery terminals, no mention of online/digital.

**Examples:** IGT (mostly land-based), Scientific Games (lottery division), Aristocrat (land-based arm), NOVOMATIC (land-based hardware)

**Exception:** If they have an ONLINE division (many do), the online arm IS a target.

### Regulators & Industry Bodies

Government gambling commissions, responsible gambling charities, industry associations.

**Examples:** Malta Gaming Authority, UK Gambling Commission, GambleAware, IAGA

### Consulting / Legal / Compliance-Only Firms

Law firms, compliance consultants, licensing advisors that don't build technology.

**How to spot:** "We help you get a gambling license", "regulatory consulting", no product/platform.

**Examples:** Nsoft legal, Harris Hagan, iGaming Academy

### IT Outsourcing Competitors

Other software development companies that also sell dev teams to iGaming. These are our DIRECT competitors.

**How to spot:** "We build for iGaming clients", "dedicated teams", "outsourcing", dev shop portfolio with iGaming cases.

**Examples:** Any IT outsourcing company (Sigma, ELEKS, Intellias, etc.) that happens to serve iGaming clients. If they SELL development services (not an iGaming product) — they're a competitor, not a client.

### Crypto / Web3 Projects

Pure crypto casinos or blockchain gambling projects without a B2B tech offering.

**How to spot:** "Decentralized casino", token-based gambling, NFT games with no B2B platform.

**Exception:** Crypto PAYMENT providers for iGaming (e.g., CoinsPaid) ARE targets — they sell B2B infrastructure.

---

## Quick Decision Tree

```
Does the company BUILD iGaming technology and sell it B2B?
  YES → Is it an IT outsourcing company (sells dev teams)?
    YES → COMPETITOR, exclude
    NO → TARGET
  NO → Does it OPERATE a casino/sportsbook for end players?
    YES (only) → OPERATOR, exclude
    YES (but also sells B2B platform) → TARGET (B2B arm)
    NO → Is it affiliate/media/regulator/legal?
      YES → EXCLUDE
      NO → Probably not iGaming, exclude
```

---

## Apollo Filter Hints

When the gathering pipeline auto-discovers filters, guide it toward:

**Keywords that work:** "igaming", "igaming platform", "casino platform", "game provider", "sportsbook platform", "gambling software", "betting platform", "casino software", "game aggregator", "gaming solutions", "B2B gaming", "white label casino"

**Keywords to AVOID (pull in operators):** "online casino", "online betting", "sports betting" (too broad — pulls in operators)

**Industries:** Computer Software, Information Technology, Gambling & Casinos, Internet

**Employee count:** 11-1000 (iGaming providers range from small studios to mid-size platforms)

**HQ locations (iGaming hubs):** Malta, Gibraltar, Isle of Man, Cyprus, Curacao, United Kingdom, Estonia, Latvia, Sweden, Israel, Armenia, Ukraine, Philippines, Costa Rica
