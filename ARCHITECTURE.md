# StartupLens v7 — Architecture Plan

## What this tool does

StartupLens is an AI-powered **investment decision tool** for equity crowdfunding investors. It evaluates not just whether a startup is good, but whether it's a good **investment at the offered price** — and it knows when it doesn't have enough data to tell you.

It covers the full investment workflow:

1. **Sourcing** — monitors platforms and SEC filings for new deals matching your criteria
2. **Screening** — Quick Score mode (5 fields, 2 minutes) to filter the pipeline
3. **Deep evaluation** — full scoring with 15+ alternative data signals, AI text analysis, competitive landscape generation, valuation analysis, probabilistic return modelling, and a structured pre-mortem
4. **Decision gating** — formal abstention model that blocks recommendations when data quality, model confidence, or calibration health are insufficient
5. **Decision support** — exportable investment memo format for sharing with co-investors or advisors
6. **Portfolio management** — tracks investments, anti-portfolio (passes), follow-on decisions, selection-bias funnel, and outcome feedback to calibrate the model over time

### How it works

- **Stage-country models (primary):** Separate XGBoost models for US_Seed, US_EarlyGrowth, UK_Seed, UK_EarlyGrowth — each trained on SEC Form C filings, Companies House outcomes, and published academic datasets. Each segment has a survival model and a short-horizon progress model (18-24 month milestone probability).
- **Model A (refinement):** Derived from 20+ years of US/UK tech S-1 filings — informing directional benchmarks by business model
- **Claude text analysis (core scoring component):** LLM evaluation of pitch narrative, competitive landscape, founder depth, and pre-mortem analysis — validated by research as the single most predictive feature (Maarouf & Feuerriegel 2024)
- **Alternative data enrichment (15+ free signals):** Job postings, Google Trends, app store data, GitHub activity, government grants, FCA permissions, press coverage, and more
- **Decision layer:** Shared scoring normalised across stage-country cohorts, with hard abstention gates, kill criteria, and calibration monitoring

### Non-negotiables

These invariants apply to every component and every phase:

1. **No data leakage.** Features must be generated strictly using information available at the decision timestamp. The as-of feature store enforces this with provenance metadata.
2. **Include failures explicitly.** Training data must contain failed companies, not just survivors. Survivorship bias is the single biggest methodological risk.
3. **Explainability for every recommendation.** Every score must show top feature drivers. No black-box outputs.
4. **Model pricing and terms explicitly.** Valuation, dilution, and deal terms are required scoring inputs, not optional metadata.
5. **Decision reliability over model complexity.** A well-calibrated simple model beats an overfit complex one.
6. **No-invest is a valid, frequent outcome.** The system is optimised for avoiding bad investments, not for deploying capital.
7. **Abstention is first-class.** "I don't have enough data" is a recommendation, not a failure mode.

---

## System Diagram

```
+-------------------------------------------------------------------------+
|                       DEAL SOURCING (Phase 6)                           |
|                                                                         |
|  +-------------------+  +--------------------+  +--------------------+  |
|  | SEC EDGAR Form C  |  | Platform RSS /     |  | Alerts             |  |
|  | new filing monitor|  | new listing monitor|  | (email / Telegram) |  |
|  | (daily cron)      |  | (Crowdcube, etc.)  |  |                    |  |
|  +---------+---------+  +---------+----------+  +---------+----------+  |
|            +------------------------+------------------------+          |
|                                     v                                   |
|                          Quick-filter by sector,                        |
|                          stage, EIS, team signals                       |
+-----------------------------------+------------------------------------|
                                    |
                                    v
+------------------------------------------------------------------------|
|                 ENTITY RESOLUTION LAYER                                 |
|                                                                         |
|  +-------------------+  +--------------------+  +--------------------+  |
|  | CIK (SEC)         |  | Companies House #  |  | Legal name aliases |  |
|  +---------+---------+  +---------+----------+  | + domain matching  |  |
|            +------------------------+------------+---------+----------+  |
|                                     v                                   |
|               Canonical entity with confidence score                    |
|               GATE: block scoring if match < threshold                  |
+-----------------------------------+------------------------------------|
                                    |
+-----------------------------------v-------------------------------------+
|                 DATA PIPELINES (Python) + AS-OF FEATURE STORE           |
|                                                                         |
|  +--------------+  +---------------+  +-------------+  +--------------+ |
|  | SEC EDGAR    |  | Companies     |  | Academic    |  | KingsCrowd   | |
|  | Form C       |  | House API     |  | Datasets    |  | Public       | |
|  | (8,500+      |  | (UK outcome   |  | (Walthoff-  |  | Reports      | |
|  |  Reg CF      |  |  verification |  |  Borm,      |  | (exit/fail   | |
|  |  offerings)  |  |  + charges    |  |  Signori,   |  |  rates by    | |
|  |              |  |  + directors) |  |  Kleinert)  |  |  revenue     | |
|  +------+-------+  +------+--------+  +------+------+  |  tier)       | |
|         |                 |                  |          +------+-------+ |
|         +-----------------+------------------+------------------+       |
|                           v                                             |
|     +----------------------------------------------------------+       |
|     | AS-OF FEATURE STORE                                      |       |
|     | Every feature tagged with:                               |       |
|     |   - as_of_date (decision timestamp)                      |       |
|     |   - source provenance                                    |       |
|     |   - label_quality_tier (1=verified, 2=estimated, 3=weak) |       |
|     |   - missingness map                                      |       |
|     +----------------------------+-----------------------------+       |
|                                  |                                      |
|     +----------------------------v-----------------------------+       |
|     | STAGE-COUNTRY MODEL FAMILIES                             |       |
|     |                                                          |       |
|     |  US_Seed  |  US_EarlyGrowth  |  UK_Seed  |  UK_EarlyGr  |       |
|     |                                                          |       |
|     |  Each family contains:                                   |       |
|     |  1. Survival model (XGBoost -> outcome distribution)     |       |
|     |  2. Progress model (18-24mo milestone probability)       |       |
|     |  3. Calibration layer (out-of-time reliability check)    |       |
|     +----------------------------+-----------------------------+       |
|                                  |                                      |
|  +-----------+  +--------+-------+  +---------------+                   |
|  | SEC EDGAR |  | Stock Prices   |  | Companies     |                   |
|  | S-1 Parser|  | yfinance +     |  | House iXBRL   |                   |
|  | (~4,000   |  | Twelve Data    |  | + AIM Docs    |                   |
|  |  filings) |  |                |  |               |                   |
|  +-----+-----+  +-------+-------+  +-------+-------+                   |
|        +----------------+------------------+                            |
|                         v                                               |
|              +---------------------+                                    |
|              |  MODEL A ENGINE     |                                    |
|              |  Correlation +      |                                    |
|              |  benchmarks by      |                                    |
|              |  business model     |                                    |
|              |  -> refine rubric   |                                    |
|              +---------+-----------+                                    |
+--------------------------+---------------------------------------------|
                           |
                 +---------v----------+
                 |     SUPABASE       |
                 |    (PostgreSQL)    |
                 +---------+----------+
                           |
+------------------------------------------------------------------------|
|                 WEB APP (Next.js on Vercel)                             |
|                                                                         |
|  +-------------------+  +---------------------+  +-------------------+ |
|  |  Two-Tier Input   |  |  API Routes         |  |  Results          | |
|  |                   |  |                     |  |                   | |
|  |  Quick Score      |  |  +----------------+ |  |  Quick: pass/fail | |
|  |  (5 fields,       |  |  | Alt Data       | |  |  Deep: full       | |
|  |   2 min screen)   |  |  | Enrichment     | |  |  dashboard +      | |
|  |                   |  |  | (15+ APIs)     | |  |  valuation +      | |
|  |  Deep Score       +->|  +-------+--------+ +->|  return distrib + | |
|  |  (full form,      |  |         |           |  |  pre-mortem +     | |
|  |   15-20 min)      |  |  +------v---------+ |  |  competitive      | |
|  |                   |  |  | Decision       | |  |  landscape +      | |
|  +-------------------+  |  | Engine         | |  |  IC memo export   | |
|                         |  | (scoring +     | |  |                   | |
|  +-------------------+  |  |  abstention    | |  +-------------------+ |
|  |  Portfolio &      |  |  |  gates +       | |                        |
|  |  Anti-Portfolio   |  |  |  kill criteria)| |  +-------------------+ |
|  |                   |  |  +------+---------+ |  |  Monitoring       | |
|  |  Investments      |  |         |           |  |                   | |
|  |  Passes (tracked) |  |  +------v---------+ |  |  Calibration      | |
|  |  Follow-on eval   |  |  | Claude API     | |  |  drift detection  | |
|  |  Selection-bias   |  |  | (3 stages)     | |  |  Score drift      | |
|  |  funnel           |  |  |                 | |  |  Data freshness   | |
|  |  Outcomes         |  |  | 1. Text score  | |  |  Kill-switch      | |
|  |  Rubric accuracy  |  |  | 2. Due diligence| |  |                   | |
|  |  Sector/stage     |  |  | 3. Pre-mortem  | |  +-------------------+ |
|  |  concentration    |  |  +----------------+ |                        |
|  +-------------------+  +---------------------+                        |
+------------------------------------------------------------------------+

                    ALTERNATIVE DATA APIs (free)
    +------------------------------------------------------+
    |  Google Trends - Adzuna Jobs - GitHub API             |
    |  GDELT Press - Reddit (PRAW) - Trustpilot            |
    |  App Store/Play scrapers - npm/PyPI stats            |
    |  FCA Register - Innovate UK grants                   |
    |  Contracts Finder - Companies House charges          |
    |  Director disqualifications - ProductHunt            |
    |  Wayback Machine - SimilarWeb DigitalRank            |
    +------------------------------------------------------+
```

---

## Build Phases

### Phase 1 — Data Foundation + Entity Resolution + Feature Store (~3 weeks)

This phase delivers the data infrastructure that everything else depends on. No model training happens until data quality is verified.

#### 1a. Data Collection

**Source 1: SEC EDGAR Form C Filings (US — primary structured source)**
- All US Reg CF offerings since May 2016 file Form C with the SEC
- SEC publishes quarterly datasets at `sec.gov/data-research/sec-markets-data/crowdfunding-offerings-data-sets`
- 8,500+ offerings, ~3,900 with reported proceeds
- Fields: issuer name, state, industry, employee count, revenue (most recent fiscal year), total assets, total debt, offering amount, securities offered, use of proceeds
- Format: XML-extracted to quarterly ZIP downloads
- Cost: free
- Supplement with FAU Equity Crowdfunding Tracker (free public resource) for success rate calibration

**Source 2: Companies House API (UK — outcome verification)**
- For every historically funded UK crowdfunding company (Crowdcube, Seedrs), check current status via Companies House
- `GET /company/{number}` -> `company_status`: active, dissolved, liquidation, administration, converted-closed
- Cross-reference against Beauhurst's published figure: ~74% still trading, ~5% exited, ~21% failed
- Also pull: filing history (accounts overdue = distress signal), charges register (secured debt), director changes
- Rate limit: 600 req/5 min, free
- Target: verify status of 2,000+ historically funded companies

**Source 3: Academic Datasets**
- Walthoff-Borm et al. (2020): 2,171 Crowdcube/Seedrs campaigns 2012-2017 with success/failure outcomes. Check Harvard Dataverse for replication data.
- Signori & Vismara (2018): 212 Crowdcube companies 2011-2015 with multinomial outcomes (survive/fail/follow-on/acquisition)
- Kleinert & Volkmann (2021): 88 Companisto campaigns with Cox regression survival data
- KingsCrowd published data: 6,375+ tracked companies, 77 exits (21 IPOs, 49 M&A), 186 failures (160 shutdowns, 15 asset sales, 8 bankruptcies). Failure rates by revenue tier available in free reports.

**Source 4: Manual Research**
- Identify 300 historically funded Crowdcube companies from published case studies and platform announcements
- Verify current status via Companies House
- Record: company name, Companies House number, sector, raise date, raise amount, pre-money valuation, equity offered, EIS/SEIS status, outcome (trading/failed/exited/acquired)

#### 1b. Entity Resolution Layer

Companies appear across multiple data sources with inconsistent identifiers. Before any feature can be computed, entities must be canonically linked.

**Matching strategy (deterministic first, probabilistic second):**

1. **Exact match** on SEC CIK or Companies House number (when available across sources)
2. **Deterministic match** on legal name + country + founding year
3. **Probabilistic match** on fuzzy name similarity + domain matching + sector + geography

**Each link gets:**
- A confidence score (0-100)
- A match method tag (exact_id, deterministic, probabilistic)
- A review status (auto_confirmed, needs_review, rejected)

**Gate:** Scoring is blocked when entity-match confidence is below 70. Deals with unresolved entity identity are routed to manual review, not scored with potentially wrong data.

**Implementation:**
- Entity resolution runs as a pipeline step between ingestion and feature extraction
- Python: `dedupe` library for probabilistic matching, custom deterministic rules
- All links stored in `entity_links` table with full provenance

#### 1c. As-Of Feature Store

Every feature must be timestamped to prevent data leakage. The feature store enforces that only information available at the decision date is used.

**For each feature row:**
- `entity_id` — canonical company identifier from entity resolution
- `as_of_date` — the timestamp at which this feature was knowable
- `feature_family` — category (campaign, company, team, financial, traction, terms, regulatory, market_regime)
- `feature_name` — specific feature key
- `feature_value` — the value (numeric, stored as JSONB for flexibility)
- `source` — which data source produced this value
- `label_quality_tier` — how trustworthy this data point is (see 1d)
- `missingness_map` — which fields in this feature family are missing

**Feature families:**

*Campaign features:*
- Funding target amount, amount raised, overfunding ratio
- Equity offered %, pre-money valuation
- Investor count, funding velocity (days to target)
- EIS/SEIS eligible, platform

*Company features:*
- Company age at raise (months), employee count
- Revenue at raise (and pre-revenue flag), revenue growth rate
- Total prior funding, prior VC/angel backing
- Sector, revenue model type, country

*Team features:*
- Founder count, domain experience (years)
- Prior exits, accelerator alumni (+ which)

*Financial features:*
- Total assets, total debt, debt-to-asset ratio
- Cash position, burn rate estimate (when derivable)

*Terms/pricing features:*
- Entry valuation vs sector/stage benchmarks
- Ownership at entry %, preference stack, pro-rata rights
- Instrument type (equity, SAFE, convertible note, ASA)
- SAFE/convertible terms: valuation cap, discount rate, MFN clause
- Liquidation preference multiple and seniority position
- Pro-rata rights (exists, amount, exercise deadline)
- Option pool size and post-money vs pre-money treatment

*Traction proxy features:*
- Google Trends momentum, web traffic rank change
- Job posting count change, app store rating trajectory
- GitHub star velocity, press coverage growth

*Regulatory/risk features:*
- Company status, director disqualifications, charges register
- FCA authorisation status, sanctions screening

*Market regime features:*
- Interest rate environment (rising/stable/falling)
- Equity market regime (bull/neutral/bear — based on benchmark index 12mo return)
- ECF market volume (total Reg CF filings in prior quarter)

*Evidence quality features:*
- Data source count, field completeness ratio
- Manual override count, feature age (staleness)

#### 1d. Label Quality Tiers

Not all outcome data is equally reliable. The model must weight training labels by quality.

| Tier | Definition | Example | Treatment |
|------|-----------|---------|-----------|
| **Tier 1** | Verified realised outcome with concrete evidence | Companies House "dissolved" + filing date; confirmed acquisition with price | Full weight in training |
| **Tier 2** | Estimated outcome from indirect signals | "Still trading" on Companies House but no recent filing; news article mentioning shutdown without formal dissolution | 0.7x weight in training |
| **Tier 3** | Insufficient evidence to determine outcome | No Companies House match; company too recent for outcome (<24 months) | Excluded from training. Tracked in monitoring for future reclassification |

**Label assignment rules:**
- UK companies: Companies House status + filing recency + charges register = Tier 1 or 2
- US companies: SEC filing status + news search + Wayback Machine check = Tier 1 or 2
- Academic dataset labels: accepted as Tier 1 (peer-reviewed methodology)
- Manual research: Tier 1 if verified against Companies House/SEC; Tier 2 otherwise

---

### Phase 2 — Stage-Country Models + Calibration (~3 weeks)

#### 2a. Stage-Country Model Segmentation

A UK seed-stage fintech raising on Crowdcube exists in a fundamentally different environment than a US seed-stage SaaS raising on Republic. A single model averages across these differences and underperforms for every specific cohort.

**Model families (MVP):**

| Family | Stage | Geography | Primary training data |
|--------|-------|-----------|----------------------|
| `UK_Seed` | Pre-seed / Seed | United Kingdom | Companies House outcomes + Crowdcube/Seedrs academic datasets |
| `UK_EarlyGrowth` | Series A/B | United Kingdom | Companies House outcomes + AIM admission docs |
| `US_Seed` | Pre-seed / Seed | United States | SEC Form C outcomes + KingsCrowd data |
| `US_EarlyGrowth` | Series A/B | United States | SEC Form C (larger raises) + S-1 early-stage benchmarks |

**Stage classification rules:**
- Seed: total prior funding < $5M OR raise amount < $2M OR explicitly labelled seed/pre-seed
- Early Growth: total prior funding $5-50M OR raise amount $2-20M OR Series A/B label

**Minimum training sample per family:** 200 labelled outcomes (Tier 1+2 combined). If a family has fewer than 200 labels, it falls back to a pooled model with a stage-country indicator feature (and confidence is downgraded). Additionally, calibration metrics (ECE) are unreliable below 100 test-set outcomes — model families with fewer than 100 test-set labels use wider confidence bands and cannot trigger the kill-switch (see Phase 7).

#### 2b. Model Architecture (per family)

Each stage-country family trains two models:

**Model 1: Survival Model (primary)**
- Method: XGBoost (gradient boosted trees)
- Target: multinomial outcome — trading (3+ years), exited (acquisition/IPO), failed (dissolved/liquidation/administration)
- Output: probability distribution over outcomes (P(survive), P(exit), P(fail))
- Why XGBoost: captures non-linear thresholds, Goldilocks effects, interaction effects. Elitzur et al. (2024) on 108,223 campaigns: boosted trees consistently outperformed logistic regression.

**Model 2: Short-Horizon Progress Model**
- Method: XGBoost (binary classifier)
- Target: did the company achieve a verifiable milestone within 18-24 months? Defined strictly as: (a) follow-on funding round at equal or higher valuation, OR (b) revenue >= 2x revenue at raise date — both verified against filing data (SEC/Companies House) or platform updates. Soft milestones (key hire, product launch, regulatory approval) are excluded — they are not reliably verifiable from public data and introduce label noise.
- Output: P(progress) — probability of hitting a milestone
- Value: gives you something actionable to check at 18 months rather than waiting 5+ years for a survival outcome

**Validation: Time-based split (per family)**
- Train: campaigns from 2016-2020
- Validate: campaigns from 2021-2022
- Test: campaigns from 2023-2025
- This prevents data leakage and properly simulates real-world use

**Class imbalance handling:**
- Failure rate is ~7-21% depending on dataset. Use cost-sensitive learning (weight the loss function by class frequency) rather than SMOTE — more stable for small samples.

**Output per family:**
- Feature importance rankings (SHAP values)
- Validation AUC, precision, recall, F1
- Calibration plot (predicted probability vs observed frequency)
- Expected realistic AUC: 0.67-0.75 (based on published benchmarks)
- Log: "Model [family], generated [date], trained on [n] outcomes (Tier 1: [n], Tier 2: [n]), time-based validation 2016-2020 / 2021-2022, survival AUC: [x], progress AUC: [x]"

#### 2c. Rubric Generation from Model + Academic Research

The XGBoost models produce feature importance weights per family. These are combined with academic findings to produce the scoring rubric. Where the model's findings align with published research, confidence is high. Where they diverge, flag for investigation.

**Academic-backed weight adjustments (override model if conflict):**

| Factor | Rubric treatment | Academic basis |
|--------|-----------------|---------------|
| Qualified institutional co-investor | Near-binary positive. If present, substantially boost score. | Signori & Vismara 2018: zero failures in this group |
| Investor count (high) | Negative signal for survival | Signori & Vismara 2018: dispersed ownership weakens monitoring |
| Patents/IP | Remove from rubric or zero weight | Ahlers et al. 2015: not significant for investor returns |
| EIS/SEIS eligibility | Meaningful positive weight | Signori & Vismara 2018: significant positive effect on survival |
| Prior VC/angel backing | Strong positive | Multiple studies: strongest campaign and survival predictor |
| Accelerator alumni | Positive | Walthoff-Borm 2020: significant for campaign success |
| Founder count (2-3) | Positive vs solo | Coakley et al. 2022: solo founders have lower success AND higher failure |
| Pre-revenue status | Negative (higher failure tier) | KingsCrowd: 7.1% failure for pre-revenue vs 2.0% for $10M+ |
| Campaign press/awards mentions | Neutral or slight negative | Kleinert 2021: external certification updates associated with higher failure |
| Director age | Not weighted (contradictory signals) | Negative in Vismara 2020 but confounded by sector effects |

#### 2d. Calibration Layer

A model that says "70% chance of survival" should be right ~70% of the time. Calibration ensures probabilities are decision-reliable, not just discriminative.

**Calibration method:**
- After XGBoost training, apply Platt scaling (logistic calibration) on the validation set as the default method
- **Method selection rule:** Use Platt scaling unless the reliability diagram shows clear non-monotonic miscalibration AND the calibration set has > 500 samples. Only then switch to isotonic regression (which overfits badly below ~300 samples).
- Measure calibration using Expected Calibration Error (ECE) on the held-out test set
- Target ECE < 0.05 (5% average miscalibration)

**Calibration health monitoring (post-deployment):**
- Track predicted vs observed frequencies on a rolling basis as new outcomes arrive
- **Minimum sample gate:** ECE is only actionable when computed on >= 100 labelled outcomes in the test window. Below this, report ECE with a "low-confidence estimate" flag and do not trigger automated alerts or kill-switch. Use bootstrap confidence intervals on ECE when sample size is 50-100.
- If ECE exceeds 0.10 for any model family (with >= 100 outcomes), trigger recalibration alert
- If ECE exceeds 0.15 (with >= 100 outcomes), activate kill-switch (see Phase 7)

**Model release gate (Phase 2 — structured model only):**
- A model family cannot go live unless:
  - Test set AUC > 0.60 (minimum discriminative power)
  - Test set ECE < 0.08 (minimum calibration quality)
  - Portfolio simulation under investor constraints beats random-selection baseline

**Combined model release gate (Phase 3 — after Claude integration):**
- The full pipeline (structured model + Claude text scores) must separately pass:
  - Combined AUC >= structured-only AUC (text analysis must not degrade discrimination)
  - Portfolio simulation MOIC with text scores >= portfolio MOIC without (text weight is justified)
  - If combined AUC < structured-only AUC, reduce text weight from 20% until combined >= structured, or investigate prompt quality

---

### Phase 3 — Web App + Claude Integration + Decision Engine (~3 weeks)

Build simultaneously with Phase 2 model training. The web app should be functional with the scoring engine as soon as models pass release gates.

#### 3a. Two-Tier Evaluation System

**Quick Score (screening mode — 5 fields, 2 minutes)**

For initial pipeline filtering. Most deals get killed here.

Required inputs: company name, website URL, sector, approximate revenue, paste pitch text.

What happens:
- Entity resolution attempts to match against known entities
- Claude Stage 1 text analysis runs (see 3b below)
- Tier 1 alt data APIs fire automatically (Google Trends, GDELT, SimilarWeb)
- Selects appropriate stage-country model based on inputs
- Produces a rough score with wide confidence bands (+/-25 points)
- Checks abstention gates (see 3d) — if data quality is too low, returns "Insufficient data" instead of a score
- Binary recommendation: **Investigate further** or **Pass**
- If Pass, option to log to anti-portfolio with reason

**Deep Score (full evaluation — full form, 15-20 minutes)**

For deals that pass Quick Score. Full form input, all Claude stages, all alt data, competitive landscape, valuation analysis, return modelling, pre-mortem.

#### 3b. Claude Integration (Three Stages)

Research finding: the way a startup describes itself is the **single most important predictive feature**, above all structured variables (Maarouf & Feuerriegel 2024, SHAP analysis). This is not a "nice to have" narrative layer — it's a core quantitative input.

**Prompt drift mitigation:**
- All prompts are version-controlled in the `rubric_versions` table
- Claude text scores are periodically calibrated against a fixed set of 20 reference pitches (10 known-good, 10 known-bad) to detect scoring drift
- If mean score on reference set drifts >10 points from baseline, alert for prompt review
- Structured JSON output format constrains hallucination surface area
- Text analysis is one component (20% weight) within a weighted rubric, not a standalone decision

**Stage 1: Structured Text Scoring (runs in both Quick and Deep)**

Model: Claude Sonnet 4.5

```
SYSTEM: You are evaluating the quality and credibility of a startup's
pitch text. Score each dimension 0-100 based on the text provided.
Be calibrated: most pitches score 40-60. Scores above 75 indicate
genuinely exceptional quality. Scores below 30 indicate serious
concerns.

USER:
PITCH TEXT:
{user-pasted pitch description from Crowdcube/platform listing}

COMPANY CONTEXT:
{structured JSON: sector, revenue, stage, team size}

Score these dimensions:

1. CLARITY (0-100): Is the value proposition clear and specific?
   Or vague and buzzword-heavy?

2. CLAIMS_PLAUSIBILITY (0-100): Are market size claims, growth
   projections, and competitive claims believable? Or inflated
   and unsupported?

3. PROBLEM_SPECIFICITY (0-100): Is the problem well-defined with
   evidence of real customer pain? Or generic and assumed?

4. DIFFERENTIATION_DEPTH (0-100): Is the competitive advantage
   specific, defensible, and hard to replicate? Or superficial?

5. FOUNDER_DOMAIN_SIGNAL (0-100): Does the language demonstrate
   deep domain expertise? Or generic business-speak?

6. RISK_HONESTY (0-100): Does the pitch acknowledge real risks
   and challenges? Or is it unrealistically optimistic?

7. BUSINESS_MODEL_CLARITY (0-100): Is how the company makes money
   clear and logical? Or vague?

Return JSON only:
{
  "clarity": <int>,
  "claims_plausibility": <int>,
  "problem_specificity": <int>,
  "differentiation_depth": <int>,
  "founder_domain_signal": <int>,
  "risk_honesty": <int>,
  "business_model_clarity": <int>,
  "text_quality_score": <int>,
  "red_flags": [<string>, ...],
  "reasoning": "<2-3 sentences>"
}
```

**Stage 2: Due Diligence Narrative + Competitive Landscape + Founder Depth (Deep Score only)**

Model: Claude Sonnet 4.5

```
You are writing a due diligence brief for an investor evaluating
a startup on {platform_name}.

COMPANY DATA:
{all structured inputs + alt data signals + text scores from Stage 1}

QUANTITATIVE SCORES:
{category breakdown}

FOUNDER Q&A TEXT (if provided):
{pasted Q&A from platform discussion section}

FOUNDER CONTENT (if provided):
{pasted LinkedIn summary, blog posts, or talk descriptions}

SCORING CONTEXT:
Equity crowdfunding companies are empirically 8.5x more likely to
fail than matched non-crowdfunded companies (Walthoff-Borm 2018).
The base rate is against this investment. Your job is to identify
whether this specific company is an exception to that base rate.

Write these sections:

1. COMPETITIVE LANDSCAPE (3-5 sentences): Based on the company
   description and sector, identify the most likely competitors.
   For each, estimate their stage and funding level from public
   knowledge. Assess whether the startup's claimed differentiation
   is real, defensible, and durable -- or whether a funded
   competitor could replicate it. Do not accept the startup's
   claims at face value.

2. MOAT ASSESSMENT (2-3 sentences): What type of competitive
   advantage exists? (network effects, switching costs, regulatory,
   brand, IP, none). Be specific about WHY it's defensible or not.

3. MARKET TIMING (2-3 sentences): Is the market ready? Too early,
   right time, or too late? What external forces support or
   undermine timing?

4. TEAM ASSESSMENT (3-5 sentences): Assess the team beyond the
   basic metrics. If Q&A text is provided, evaluate how founders
   respond to hard questions -- are they defensive, dismissive,
   or thoughtful? Do they acknowledge uncertainty or pretend
   everything is perfect? If founder content is provided, assess
   the depth of domain thinking. Flag gaps in the team (e.g.,
   technical founders with no commercial experience, or vice versa).

5. BIGGEST RISK (1-2 sentences): The single most likely failure
   mode for this company.

6. BULL CASE (1-2 sentences): The specific scenario where this
   returns 10x+. Be concrete.

7. BASE RATE OVERRIDE: Does this company have characteristics that
   justify overriding the sceptical base rate? (yes/no + 1 sentence)

8. QUALITATIVE MODIFIER: Integer from -15 to +15.

Be direct. Do not hedge. If information is insufficient, say so.
```

**Stage 3: Structured Pre-Mortem (Deep Score only)**

Model: Claude Sonnet 4.5

```
COMPANY DATA:
{all structured inputs + alt data + scores}

CONTEXT: You are writing a pre-mortem analysis. Assume this company
has failed 3 years from now.

Write a 150-250 word narrative explaining the most likely sequence
of events that led to failure. Be specific:

- Which risks materialised and in what order?
- What decisions did the team make (or fail to make) that
  accelerated the decline?
- At what point did the company become unrecoverable?
- Could this failure mode have been predicted from the data
  available today?

Do NOT list generic risks. Construct a plausible, specific
narrative. Connect the company's actual weaknesses (burn rate,
team gaps, competitive position, market timing) into a causal
chain.

Then answer:
- PREVENTABLE? (yes/no): Could strong execution have avoided this?
- PROBABILITY: (low/medium/high): How likely is this specific
  failure narrative?
- EARLY WARNING SIGNS: What should the investor watch for in the
  first 12 months that would indicate this failure mode is
  materialising?

Return as JSON:
{
  "narrative": "<pre-mortem text>",
  "preventable": <boolean>,
  "probability": "<low|medium|high>",
  "early_warning_signs": [<string>, ...],
  "failure_mode_category": "<cash_runway|competition|execution|
    market_timing|regulatory|team_breakdown|product_market_fit>"
}
```

**Cost per Deep evaluation:** ~$0.08-0.15 (three Sonnet calls).
**Cost per Quick evaluation:** ~$0.02-0.04 (one Sonnet call).

#### 3c. Valuation & Probabilistic Return Analysis (Deep Score only)

This is the investment-level analysis layer. A great company at a bad price is a bad investment.

**Entry valuation analysis:**
- Compare pre-money valuation to revenue multiple benchmarks:
  - Seed SaaS: 10-30x revenue typical, >50x aggressive
  - Seed marketplace: 5-15x GMV take-rate revenue
  - Pre-revenue: valued by comparable raises in sector/stage
- Score: valuation relative to sector/stage median (0-100, with 50 = median)
- Flag: "Priced at 40x revenue. Sector median for this stage is 15x. Valuation is aggressive."

**Dilution modelling:**
- Input: current equity offered %, pre-money valuation, instrument type, terms
- **Equity rounds:** straightforward dilution based on future round assumptions
  - Seed -> Series A -> Series B -> exit = ~60-75% total dilution
  - Seed -> Series A -> exit = ~40-55% total dilution
- **SAFEs/convertible notes:** model conversion scenarios
  - If valuation cap: convert at min(cap, next-round valuation) — cap protects your price
  - If discount only: convert at (1 - discount%) x next-round price
  - If MFN: convert at best terms of any subsequent SAFE before the priced round
  - Model the scenario where conversion round is a down round (cap becomes irrelevant, discount matters more)
  - Flag: multiple SAFE/convertible layers above you in the stack increase dilution risk
- **Liquidation preference stack:** model the waterfall
  - If preferred shares with 1x non-participating preference (most common ECF): investors get back 1x before common, then common shares split remainder
  - If participating preferred: investors get 1x PLUS pro-rata share of remainder (much worse for you as a crowdfunding investor who typically gets common/ordinary shares)
  - If multiple preference layers from prior rounds: model how much exit value is consumed before your shares see any return
- Calculate investor ownership at exit scenarios
- Output: "Your 2% ownership at entry becomes ~0.5-0.8% at exit after dilution"

**Probabilistic return distribution (replaces heuristic scenarios):**

Instead of fixed bear/base/bull labels, model a return distribution based on the stage-country model's outcome probabilities:

- Use the survival model's P(fail), P(survive), P(exit) to weight exit scenarios
- **Exit-multiple distributions per outcome class:**
  - **Failed:** model as a mixture — P(total loss) = 0.85 (dissolved, no recovery), P(partial recovery at 0.05-0.3x) = 0.15 (asset sales, acqui-hires). Fit from training data if >= 30 failure outcomes with known recovery amounts; otherwise use these defaults from KingsCrowd data (160 shutdowns vs 15 asset sales).
  - **Surviving (trading, no exit):** secondary sale at 0.3-1.2x last round valuation (log-normal, median 0.6x), reflecting illiquidity discount. If no secondary market exists, value at 0x (locked capital).
  - **Exited (acquisition/IPO):** fit a log-normal distribution from training data exit multiples. If training data has < 30 exits, use published benchmarks — Correlation Ventures power law: ~65% return <1x, ~25% return 1-5x, ~7% return 5-10x, ~3% return >10x. Parameterise as log-normal(mu=0.5, sigma=1.8) adjusted by stage-country family.
- For each exit scenario, apply dilution model and entry valuation to compute investor MOIC
- Monte Carlo simulation (1,000 draws): sample outcome class from survival model probabilities, then sample exit multiple from the class-specific distribution, apply dilution
- Output: **P10 / P50 / P90 return multiples** (10th, 50th, 90th percentile) from the simulated distribution
- Factor in EIS/SEIS/QSBS tax relief impact on effective return at each percentile
- Compute expected value (probability-weighted mean return)

**Tax relief modelling (UK — EIS/SEIS):**
- EIS: 30% income tax relief on investment, CGT exemption on gains, loss relief on failures
- SEIS: 50% income tax relief, CGT exemption, CGT reinvestment relief
- Applied automatically when EIS/SEIS eligibility is indicated

**Tax relief modelling (US — QSBS):**
- Section 1202 QSBS: if held >= 5 years and company is a qualified small business (C-corp, <$50M gross assets at issuance), exclude up to $10M or 10x basis from federal capital gains tax
- Applied when: US company, C-corp structure, gross assets < $50M (estimated from financials), equity instrument (not SAFE — SAFEs may not qualify until conversion)
- Flag when QSBS qualification is uncertain: "QSBS eligibility depends on C-corp status and asset threshold at issuance — verify with the company"

Example output:
```
Return Distribution (after dilution, before tax relief):
  P10 (downside):   0.0x  (total loss — 62% probability of failure)
  P50 (median):     0.3x  (partial loss — survival without meaningful exit)
  P90 (upside):     4.8x  (acquisition at 8x last round)
  Expected value:   1.2x

UK (with EIS 30% income tax relief):
  Effective P10:    0.3x  (loss relief recovers 30% of investment)
  Effective P50:    0.6x
  Effective P90:    5.1x
  Expected value:   1.5x

US (with QSBS, if qualified and held 5yr):
  Effective P10:    0.0x  (no loss relief equivalent)
  Effective P50:    0.3x
  Effective P90:    4.8x  (gains excluded from federal CGT)
  Expected value:   1.2x  (pre-state-tax)
```

**Exit path assessment:**
- What are the realistic exit routes? (acquisition, IPO, secondary, buyback)
- Are there plausible acquirers in the market?
- How long until a potential exit? (typical: 5-8 years for crowdfunding investments)
- Is the company on a trajectory that leads to any exit, or is it a lifestyle business?

#### 3d. Decision Engine: Abstention Gates + Kill Criteria

Abstention is first-class. A deal cannot receive a positive recommendation unless all gates pass. This prevents the most dangerous output: a confident-looking number built on bad data.

**Abstention gates (all must pass for a recommendation):**

| Gate | Threshold | Failure action |
|------|-----------|---------------|
| Data completeness | >= 40% of fields populated (Quick), >= 60% (Deep) | Route to **Abstain** — "Insufficient data to score" |
| Entity-match confidence | >= 70 | Route to **Manual Review** — "Entity identity unresolved" |
| Model confidence | Survival model normalised entropy < 0.70 (raw entropy / ln(3); i.e., raw entropy < 0.77) | Route to **Abstain** — "Model cannot distinguish this company from base rate" |
| Prediction uncertainty | P90 - P10 return spread < 20x | Route to **Abstain** — "Return range too wide for meaningful recommendation" |
| Calibration health | Current model family ECE < 0.10 | Route to **Abstain** — "Model calibration degraded, recommendations suspended" |
| Evidence quality | >= 3 independent data sources confirm key claims | Route to **Abstain** — "Key claims unverifiable from available sources" |

**Hard kill criteria (override score regardless of value):**

| Criterion | Source | Action |
|-----------|--------|--------|
| Director on disqualification register | Companies House API | **Reject** — compliance hard fail |
| Company under administration/liquidation | Companies House API | **Reject** — already failing |
| Sanctions match on director/beneficial owner | Sanctions screening | **Reject** — compliance hard fail |
| Accounts overdue > 12 months | Companies House filing history | **Flag** — severe distress signal, downgrade confidence to Low |
| Cap table gives >50% to non-founders pre-revenue | Manual input / deal terms | **Flag** — governance risk, apply -20 point modifier |

**Recommendation classes:**

| Class | Meaning | Criteria |
|-------|---------|----------|
| **Invest** | All gates pass, score >= 65, no kill criteria triggered | Strongest positive signal |
| **Deep Diligence** | All gates pass, score 50-65 OR one gate marginal | Worth investigating further |
| **Watch** | Score 40-50, or interesting profile with data gaps | Track for future evaluation |
| **Pass** | Score < 40, or kill criteria triggered | Log to anti-portfolio with reason |
| **Abstain** | One or more gates failed | Explicitly: "I don't have enough data to tell you" |

#### 3e. Portfolio Policy Constraints

The decision engine enforces investor-level constraints before any recommendation. These are configurable in the `investor_policy` table but default to conservative settings.

**Default policy:**

| Constraint | Default value | Enforcement |
|-----------|--------------|-------------|
| Max investments per year | 2 | If already at cap, all "Invest" recommendations downgrade to "Watch — annual cap reached" |
| Check size | £10,000 (fixed) | Displayed in return calculations; not variable per deal |
| Max per sector per year | 1 | If already invested in this sector this year, flag: "Sector concentration — already invested in [sector] this year" |
| No forced deployment | Enabled | Zero investments in a year is explicitly valid. The tool never pressures you to invest. |
| Compliance hard blocks | Enabled | Kill criteria cannot be overridden regardless of policy |

**How it works at evaluation time:**
- After scoring and gate checks, but before displaying the final recommendation, the policy engine checks portfolio state
- If the annual cap is reached, the recommendation changes but the score is preserved (you can still see how good the deal is — you just can't act on it this year without overriding the cap)
- If you override a policy constraint, the override is logged in the recommendation_log with your reason

**Why this matters:** Without hard constraints, a run of high-scoring deals can lead to overcommitting capital in a single vintage or sector. The policy engine is the discipline layer that the scoring engine can't provide on its own.

#### 3f. Scoring Engine

**Rubric Structure (7 categories):**

```
OVERALL SCORE (0-100) +/- confidence range

  Sceptical baseline: ECF companies are 8.5x more likely to fail
  than matched non-ECF firms. Score starts at 35 (below average)
  and adjusts upward only when evidence supports it.

  Score is cohort-normalised within the appropriate stage-country
  model family (e.g., a 70 for UK_Seed means top ~30% of UK seed
  deals, not top ~30% of all deals globally).

+-- TEXT & NARRATIVE QUALITY (20%)
|   +-- Clarity score (Claude Stage 1)
|   +-- Claims plausibility (Claude Stage 1)
|   +-- Problem specificity (Claude Stage 1)
|   +-- Differentiation depth (Claude Stage 1)
|   +-- Founder domain signal (Claude Stage 1)
|   +-- Risk honesty (Claude Stage 1)
|   +-- Business model clarity (Claude Stage 1)
|
+-- TRACTION & GROWTH (18%)
|   +-- Revenue exists (yes/no -- binary gate)
|   +-- Revenue growth rate (YoY or MoM)
|   +-- Customer/user count + growth
|   +-- Google Trends direction (auto)
|   +-- Web traffic rank + trend (auto)
|   +-- App store rating + reviews (auto, if applicable)
|   +-- GitHub star velocity (auto, if applicable)
|   +-- npm/PyPI download growth (auto, if applicable)
|
+-- DEAL TERMS & VALUATION (15%)
|   +-- Entry valuation vs sector/stage revenue multiple benchmarks
|   +-- Dilution-adjusted return potential (see 3c)
|   +-- Exit path plausibility (acquirer exists? IPO trajectory?)
|   +-- Equity offered % (>25% is negative)
|   +-- EIS/SEIS tax relief impact on effective return
|   +-- Share class protections
|   +-- Platform nominee structure risk
|
+-- TEAM (15%)
|   +-- Founder count (2-3 optimal; solo penalised)
|   +-- Relevant domain experience (years)
|   +-- Prior startup exits (boolean -- strong positive)
|   +-- Accelerator alumni (boolean + tier)
|   +-- Founder Q&A quality (Claude, if provided)
|   +-- Founder content depth (Claude, if provided)
|   +-- LinkedIn headcount trend (manual)
|   +-- Glassdoor rating (manual, if available)
|   +-- Active hiring signal (Adzuna job count, auto)
|
+-- FINANCIAL HEALTH (12%)
|   +-- Gross margin %
|   +-- Burn multiple (net burn / net new revenue)
|   +-- Cash runway (months)
|   +-- Capital efficiency (revenue / total raised)
|   +-- Debt-to-asset ratio
|   +-- Revenue model type (recurring > transactional > project)
|
+-- INVESTMENT SIGNAL (10%)
|   +-- Qualified institutional co-investor (near-binary -- see below)
|   +-- Prior VC/angel backing (strong positive)
|   +-- Overfunding ratio (moderate positive)
|   +-- Funding velocity (moderate positive)
|   +-- Investor count (NEGATIVE above threshold)
|   +-- Round progression (clean step-ups vs bridges)
|
+-- MARKET (10%)
    +-- Competitive landscape quality (Claude Stage 2 -- auto-generated)
    +-- TAM plausibility (Claude assessment)
    +-- Market timing / tailwinds (Claude assessment)
    +-- Press coverage trend (GDELT, auto)
    +-- Innovate UK / EU grants received (manual)
    +-- Government contracts won (manual)
```

**Special rules:**

1. **Qualified institutional co-investor override:** If a qualified institutional investor (named VC fund or established angel syndicate) has co-invested, apply a +15 point bonus to the overall score. Academic basis: zero failures in this group (Signori & Vismara 2018).

2. **Pre-revenue penalty:** If the company has no revenue, cap the Traction & Growth category at 30/100 regardless of other signals. Pre-revenue companies have a 7.1% failure rate vs 2.0% for $10M+ revenue (KingsCrowd data).

3. **Sceptical baseline:** The score starts at 35, not 50. Evidence must push it upward. This reflects the empirical 8.5x failure rate of ECF companies vs matched non-ECF firms.

4. **Qualitative modifier:** Claude's Stage 2 assessment produces a modifier of -15 to +15, applied after the quantitative score.

5. **Valuation gate:** If the Deal Terms & Valuation category scores below 25/100 (extreme overvaluation), flag the evaluation with: *"Company quality may be acceptable but the entry price makes this a poor investment at current terms. Consider negotiating or waiting for a down round."*

**Confidence calculation:**

| Data completeness | Confidence | Score range |
|-------------------|-----------|-------------|
| >80% of fields + pitch text + 3+ auto signals | High | +/-8 points |
| 50-80% of fields + pitch text | Moderate | +/-15 points |
| 50-80% of fields, no pitch text | Moderate-Low | +/-20 points |
| <50% of fields | Low | +/-25 points |

Additionally, if the company's feature profile is a statistical outlier (far from any training data cluster), confidence is downgraded one level regardless of completeness, with an explicit note: *"This company's profile is unusual -- few historical comparables exist in the training data."*

#### 3g. Alternative Data Enrichment

When the user submits a company name + website URL, the API routes automatically fetch signals from free sources.

**Tier 1 — Auto-fetched for every evaluation (Quick + Deep):**

| Signal | API | Auth | Rate limit | What we extract |
|--------|-----|------|-----------|----------------|
| Search interest trend (12mo) | Google Trends (pytrends) | None | ~1,400/session | Relative interest 0-100, direction, % change |
| Website rank + trend | SimilarWeb DigitalRank | Free API key | 100/month | Global rank, rank change direction |
| Press coverage volume + tone | GDELT Doc 2.0 API | None | Unlimited | Article count (30/90 day), avg tone (-100 to +100) |
| Job posting count + roles | Adzuna API | Free API key | 1,000/day | Open role count, role categories, seniority |
| Company status + charges | Companies House API | Free API key | 600/5min | Active/dissolved, charges, filing timeliness |
| Director disqualifications | Companies House API | Free API key | (same) | Binary red flag check on all directors |

**Tier 2 — Auto-fetched when applicable (Deep Score, conditional):**

| Signal | Condition | API | What we extract |
|--------|-----------|-----|----------------|
| App store rating + reviews | Mobile app | google-play-scraper / app-store-scraper | Rating, review count, growth, sentiment |
| GitHub stars + commit velocity | GitHub URL provided | GitHub REST API (5K/hr) | Stars, velocity, contributors, commit frequency |
| npm/PyPI downloads | Dev tool | npmjs.org / pypistats.org | Weekly download count, growth trend |
| Trustpilot score | Consumer-facing | Trustpilot API | TrustScore, review count, response rate |
| ProductHunt launch | PH launched | ProductHunt GraphQL API | Upvotes, comments, featured status |
| FCA permissions | Fintech | FCA Register API (free) | Permissions, authorisation status, history |
| Reddit mentions | Any | PRAW (Reddit API) | Post count (30/90 day), subreddit presence |
| Stack Overflow questions | Dev tool | Stack Exchange API (10K/day) | Question count, growth |

**Tier 3 — Manual enrichment (user checks and enters):**

| Signal | Where to check | Form field |
|--------|---------------|------------|
| LinkedIn employee count (current) | LinkedIn company page | Number input |
| LinkedIn employee count (6mo ago) | LinkedIn company page | Number input |
| Glassdoor rating | glassdoor.co.uk | Number input (1-5) |
| Innovate UK grants | UKRI funded projects CSV | Checkbox + amount |
| Government contracts won | Contracts Finder | Checkbox + count |

**Data licensing and retention:**
Not all "free" APIs permit all uses. Key restrictions to respect:
- **SimilarWeb DigitalRank:** free tier permits personal/non-commercial use only. If the tool is opened to others, requires paid plan.
- **Trustpilot API:** rate-limited, requires attribution. Do not cache reviews beyond 24 hours.
- **Google Play / App Store scrapers:** unofficial libraries, no formal API. May break without notice. Do not redistribute scraped data.
- **PRAW (Reddit):** Reddit API terms require attribution and prohibit commercial use above free tier limits.
- **Companies House / FCA / SEC EDGAR:** public government data, no usage restrictions.
- **General rule:** cache API responses for max 24 hours per evaluation. Do not build a persistent mirror of any third-party dataset. Store only the extracted signal values (e.g., "Trustpilot 4.2/5, 89 reviews"), not raw API responses.

**API failure and graceful degradation strategy:**

Not all alt data signals are equal. When an API fails, the evaluation must continue — not block.

| Signal | Required for | Failure behaviour |
|--------|-------------|------------------|
| Companies House status | Quick + Deep | **Hard requirement** (UK only). If unavailable, route to Abstain — cannot verify company is active. |
| Google Trends | Quick + Deep | Optional. If unavailable, skip and note in missing_data_fields. No score penalty. |
| GDELT press coverage | Quick + Deep | Optional. Skip and note. |
| Adzuna jobs | Quick + Deep | Optional. Skip and note. |
| SimilarWeb rank | Quick + Deep | Optional. Skip and note. |
| Director disqualifications | Quick + Deep | **Hard requirement** (UK only). If unavailable, route to Manual Review — cannot clear kill criteria. |
| App store / GitHub / npm | Deep (conditional) | Optional. Skip and redistribute weight to available traction signals. |
| Trustpilot / Reddit / PH | Deep (conditional) | Optional. Skip and note. |
| FCA permissions | Deep (fintech) | **Soft requirement** for fintech. If unavailable, flag: "FCA status unverified" and apply -5 to regulatory score. |

**Failure handling rules:**
- **Timeout:** 10 seconds per API call. If exceeded, treat as unavailable.
- **Retry:** one retry with exponential backoff (2s delay) for transient errors (HTTP 429, 500, 503). No retry for 401/403/404.
- **Circuit breaker:** if an API fails 3 consecutive evaluations, disable it for 24 hours and log an alert. Re-enable automatically after 24 hours.
- **Scoring adjustment:** when optional signals are missing, redistribute their rubric weight proportionally among available signals in the same category. Do not leave weight unallocated — this would systematically deflate scores when APIs are down.

#### 3h. Additional Input Fields (Deep Score Form)

Beyond the standard form sections (Company, Financials, Deal Terms, Investment Signals, Traction, Team, Market, Pitch Text), the Deep Score form adds:

**Founder Depth (optional, improves Team score accuracy):**
- Founder Q&A text — paste the investor Q&A discussion from the listing page. Claude analyses how founders respond to hard questions.
- Founder content — paste LinkedIn summary, blog posts, or conference talk descriptions. Claude assesses depth of domain thinking and intellectual honesty.
- Founder career trajectory — free text description of career path (e.g., "8 years at Deloitte, rose from analyst to team lead managing FCA submissions for tier-1 banks")

**Competitive Intelligence (optional, improves Market score):**
- Known competitors — free text list of competitors you've identified
- Note: even without this input, Claude Stage 2 auto-generates a competitive landscape from the pitch text and sector

---

### Phase 4 — Model A: S-1 / IPO Historical Analysis (~2.5 weeks)

Runs after stage-country models are functional. Refines rubric weights with hard data on what business model benchmarks predict long-term success.

#### 4a. SEC EDGAR S-1 Pipeline

- Download quarterly `form.idx` bulk files (2000-2025)
- Filter to tech SIC codes: 7370-7379, 3571-3577, 3661-3674, 8742
- Estimated yield: ~3,000-4,500 original S-1 filings
- For each filing:
  - Download HTML document
  - Extract financial tables using Claude Haiku 4.5 (~$0.01/filing, ~$40 total)
  - Extract from same filing: funding history (capitalization section), founding date, employee count
  - Spot-check 50 filings manually. If accuracy >95%, proceed.
- Extracted metrics per company: revenue (2-3 years), revenue growth YoY, gross margin, operating expenses, net income/loss, cash position, total funding raised, employee count, time from founding to IPO
- Libraries: `edgartools`, `sec-parser`, `beautifulsoup4`, `pandas`

#### 4b. UK IPO Pipeline

- Cross-reference Companies House tech SIC codes with LSE/AIM listing records
- Fetch pre-IPO iXBRL filings where available (large companies only -- `ixbrlparse`)
- For small companies with balance-sheet-only filings: track equity and asset changes as growth proxies
- Download ~100 most relevant AIM Admission Documents (PDF) -> extract with Claude Sonnet 4.5 (~$10)
- Accept smaller, noisier UK dataset vs US

#### 4c. Stock Price Data (Post-IPO Performance)

- `yfinance` for US tickers, `yfinance` `.L` suffix + Twelve Data for UK/AIM
- For each company calculate:
  - **Benchmark-relative returns (alpha):**
    - US benchmark: S&P 500 Information Technology Index
    - UK benchmark: FTSE AIM All-Share
    - Periods: 1yr, 3yr, 5yr
  - Max drawdown within first 3 years
  - **Macro regime label:** bull (market up >15% prior 12mo), neutral (+/-15%), bear (down >15%)

#### 4d. Analysis

- **Time-based validation:** Train on 2000-2017, validate on 2018-2020, test on 2021-2025
- Spearman rank correlation: each pre-IPO metric vs benchmark-relative 3yr alpha
- Random forest feature importance for non-linear relationships
- Control for macro regime -- run analysis within each regime separately
- Output: **business-model-specific benchmarks** that feed into valuation and scoring thresholds

#### 4e. Rubric Refinement -> v2

- Model A findings refine the **scoring thresholds** within each category
- Category weights stay anchored to stage-country models (crowdfunding-relevant)
- Valuation benchmarks by sector/stage feed into the Deal Terms & Valuation category
- Log: "Rubric v2, generated [date], stage-country models: [n] crowdfunding outcomes, Model A: [n] S-1 companies, validation AUC: [x], business-model benchmarks for [n] sectors"

---

### Phase 5 — Portfolio Tracker, Anti-Portfolio, Selection-Bias Tracking & Paper Trading (~2 weeks)

#### 5a. Paper Trading Period (mandatory before live deployment)

Before using StartupLens for real investment decisions, run in shadow mode:

- Evaluate 20-30 deals across UK and US platforms using Quick + Deep Score
- Log all recommendations without acting on them
- Track outcomes at 6, 12, and 18 months
- Compare model recommendations against:
  - **Baseline 1: Random selection** — pick random deals from the same platform/period
  - **Baseline 2: Simple heuristic** — "has revenue + institutional co-investor + EIS eligible"
  - **Baseline 3: Sector momentum** — invest in the hottest sector on the platform

**Go-live gate (predefined numeric thresholds — not negotiable):**

| KPI | Threshold | What failure means |
|-----|-----------|-------------------|
| Top-decile hit rate | >= 50% of top-scored deals still trading or exited at 18mo | Model's best picks aren't reliably good. Investigate feature engineering. |
| Downside capture | "Invest" deals have < 0.6x the failure rate of random baseline | Model isn't avoiding losers well enough. Tighten abstention gates. |
| "Invest" vs baseline progress | "Invest" deals have higher 18-month progress rate than all 3 baselines | Core discrimination isn't working. Full model review required. |
| "Pass" vs "Invest" failure | "Pass" deals have higher failure rate than "Invest" deals | Model can't distinguish good from bad. Do not deploy. |
| Sector bias | No single sector > 50% of "Invest" recommendations | Model may be overfit to one sector. Add sector-agnostic features. |
| Calibration at go-live | ECE < 0.08 on paper trading period outcomes | Probabilities unreliable for decision-making. Recalibrate. |
| Abstention rate | 10-40% of evaluated deals | Below 10%: gates too loose. Above 40%: tool unusable. |

All thresholds must pass simultaneously. If any fail, the model stays in shadow mode and parameters are tuned. The gate is not moved to accommodate the model.

#### 5b. Investment Tracker
- When you invest based on an evaluation, log: company, date, amount, evaluation score, model family, rubric version
- Link to the saved evaluation for reference

#### 5c. Anti-Portfolio Tracker
- When you pass on a deal, log: company, date, Quick/Deep score, reason for passing
- Quarterly check: what happened to companies you passed on?
  - Did they raise a follow-on round? At what valuation?
  - Are they still trading? Growing? Failed?
- Over 50+ tracked passes, build a statistical picture of your pass error rate:
  - False negatives (passed on winners): which signals did the rubric miss?
  - True negatives (correctly avoided losers): which signals were right?
- This is more valuable than tracking investments alone, because most decisions are "no"

#### 5d. Selection-Bias Tracking

You can only calibrate what you can measure. The selection-bias funnel tracks the full pipeline to reveal blind spots:

| Funnel stage | What it captures | How it's populated |
|-------------|-----------------|-------------------|
| **Existed** | All deals on platforms you monitor during the period | Deal sourcing alerts (Phase 6) + manual platform browsing logs |
| **Seen** | Deals you were aware of (opened the listing) | Manual "I looked at this" button, or auto-logged from sourcing alerts you clicked |
| **Assessed** | Deals you ran through Quick or Deep Score | Automatic from evaluation creation |
| **Invested** | Deals you invested in | Investment tracker |
| **Passed** | Deals you explicitly declined | Anti-portfolio tracker |

**What this reveals:**
- **Seen vs Existed:** Are you missing deals from certain platforms, sectors, or geographies? Is your sourcing filter too narrow?
- **Assessed vs Seen:** Are you filtering too aggressively before scoring? Selection bias in what you evaluate will silently bias the feedback loop.
- **Invested vs Assessed:** Is the model's "Invest" rate plausible? (Should be <10% of assessed deals)
- **Outcome by funnel stage:** Over time, compare outcomes of invested vs passed vs never-seen deals

This is how you detect whether you're systematically missing good deals in sectors you don't look at — something anti-portfolio tracking alone can't reveal.

#### 5e. Follow-On Decision Framework
- When a portfolio company does a follow-on round, support a re-evaluation:
  - What has changed since the initial evaluation?
  - Has the company hit the milestones the bull case depended on? (Cross-reference against short-horizon progress model prediction)
  - Is the new valuation justified by progress, or is it inflation?
  - Given what you now know, would you invest at this price if it were a new deal?
- Lighter version of the full evaluation — many inputs carry over from the original
- Produces a follow-on recommendation: Increase / Maintain / Do not follow on

#### 5f. Outcome Monitoring
- Quarterly check: is the company still trading? Companies House status for UK, SEC EDGAR for US.
- Track: follow-on raises, revenue updates, team changes
- Record: current status, outcome multiple (when known)
- Feed outcomes back to label store with Tier 1/2 classification

#### 5g. Portfolio-Level View
Dashboard showing:
- **Sector concentration:** bar chart of investments by sector with warning if >40% in one sector
- **Stage diversification:** breakdown by stage at entry
- **Vintage distribution:** investments by quarter/year with macro regime overlay
- **Score distribution:** histogram of scores at entry — are you investing in high-conviction deals or spreading across mediocre ones?
- **Performance tracking:** invested capital, current estimated value, MOIC, IRR (when exits occur)
- **Rubric accuracy:** average score of successful vs failed investments — is the rubric discriminating?
- **Selection-bias funnel:** visual funnel showing existed -> seen -> assessed -> invested/passed conversion rates

#### 5h. Feedback Loop
- After 20+ tracked investments with known outcomes, compare predicted scores against actual results
- Identify systematic biases
- Manual rubric adjustment based on personal data
- Automated alert: if the average score of failed investments overlaps with the average score of successful ones, the rubric has lost discriminatory power

---

### Phase 6 — Deal Sourcing & Monitoring (~1 week)

Proactive deal discovery rather than passive evaluation.

#### 6a. SEC EDGAR Form C Monitor
- GitHub Actions cron job (daily)
- Check for new Form C filings via EDGAR EFTS search API
- Filter by sector keywords matching your target verticals
- For each new filing: extract issuer name, sector, offering amount, revenue, employee count
- If it matches your criteria (sector, stage, minimum revenue): send alert
- Log to selection-bias funnel as "existed"

#### 6b. Platform New Listing Monitor
- Check public listing pages / RSS feeds on Crowdcube, Republic, Wefunder, StartEngine
- Not full scraping — just detect new listing titles, sectors, and raise targets from public-facing pages
- If a new listing matches your sector/stage criteria: send alert with link
- Respect platform ToS — monitor only publicly visible metadata
- Log to selection-bias funnel as "existed"

#### 6c. Alert Delivery
- Email digest (daily or weekly) of new deals matching criteria
- Optional Telegram bot for real-time alerts
- Each alert includes: company name, platform, sector, raise target, and a link to evaluate in StartupLens

---

### Phase 7 — Monitoring & Governance (~1 week)

#### 7a. Data Quality Monitoring
- **Freshness checks:** alert if any data source hasn't been refreshed in > configured interval (Companies House: weekly, SEC: daily, alt data: per-evaluation)
- **Schema drift:** alert if SEC EDGAR Form C format changes (column count, field names)
- **Missingness tracking:** dashboard showing % of evaluations with missing fields by category, trending over time

#### 7b. Model Quality Monitoring
- **Calibration drift:** track ECE per model family on rolling basis as new outcomes arrive (quarterly recheck)
- **Score drift:** monitor mean and distribution of scores over time. Alert if mean score shifts > 10 points in a quarter (may indicate systematic changes in deal quality or model degradation)
- **Feature importance stability:** compare SHAP rankings quarterly. Alert if top-5 features change

#### 7c. Kill-Switch

If any of these conditions trigger, the system automatically downgrades all recommendations to "Watch" and displays a prominent banner:

| Trigger | Threshold | Minimum sample | Recovery |
|---------|-----------|---------------|----------|
| Calibration collapse | ECE > 0.15 for any model family | >= 100 test outcomes (below this, ECE is too noisy to trigger on) | Retrain + recalibrate + pass release gate |
| Data source failure | Primary data source unavailable > 7 days | N/A | Fix pipeline + verify data currency |
| Prompt drift | Reference pitch set mean score drifts > 15 points | N/A (uses fixed 20-pitch reference set) | Review + update prompt + recalibrate |
| Systematic error | 3+ consecutive "Invest" recommendations result in failure within 18 months | >= 5 resolved "Invest" recommendations | Full model review + parameter audit |

The kill-switch is a safety net, not a routine tool. Its existence is a commitment to decision reliability over model ego.

#### 7d. Recommendation Log
Every recommendation is logged with:
- Full inputs, scores, and feature values at decision time
- Model family and version used
- Which abstention gates passed/failed
- Override history (if user manually overrides a recommendation, log the reason)
- Eventual outcome (linked when known)

This log is the foundation for all post-hoc analysis. It enables you to ask: "For every deal where I overrode the model, what happened?"

**Override audit rule:**
- Every manual override (user changes recommendation class or ignores a policy constraint) must include a written reason at the time of override — the system blocks saving without one
- Quarterly review: for all overrides with known outcomes, compare override decisions against what the model recommended
- If overrides perform worse than model recommendations across 5+ resolved overrides, display a permanent banner: "Your overrides have historically underperformed the model. Consider trusting the system."
- Override audit results are displayed on the portfolio dashboard under "Model Health"

---

## Backtesting Plan

The backtest harness is built in Phase 0 (before model training), because it defines what "good" looks like before you start training. If you can't measure success before you build, you're guessing.

### Phase 0: Backtest Infrastructure (before model training)

Build before any model training begins.

**0a. Walk-forward splitter**
- Implement rolling time-window splits, not a single train/test partition:
  - Train 2016-2018, test 2019
  - Train 2016-2019, test 2020
  - Train 2016-2020, test 2021
  - Train 2016-2021, test 2022
  - Train 2016-2022, test 2023-2025
- This reveals whether the model is consistently good or got lucky in one period

**0b. Test set quarantine**
- Create the final test set (2023-2025 outcomes) before any model training begins
- Store in a separate table (`backtest_holdout`) that the training pipeline physically cannot access
- Run final evaluation exactly once per model candidate — no iterative peeking
- Log every test set access to prevent "just one more look" overfitting

**0c. Baseline generators**
- Implement three baselines that every model must beat:
  - **Random selection:** pick random deals from the same platform/period
  - **Simple heuristic:** "has revenue + institutional co-investor + EIS eligible"
  - **Sector momentum:** invest in the sector with the most successful exits in the prior 2 years
- Baselines use the same portfolio constraints (max 2/year, max 1/sector/year)

**0d. Portfolio simulator**
- Simulate realistic investment decisions under policy constraints
- Input: scored deal list for a vintage period + investor policy
- Process: rank by score, apply policy constraints, select top eligible deals
- Output: simulated portfolio with eventual outcomes
- Run for each walk-forward window

**0e. Metric computation with pass/fail thresholds**

Every backtest run is evaluated against predefined thresholds. The model does not ship unless all "must pass" thresholds are met.

| Metric | Threshold | Must pass? | What failure means |
|--------|-----------|-----------|-------------------|
| Survival model AUC (out-of-time, per family) | >= 0.65 | Yes | Model can't distinguish survivors from failures. Investigate feature engineering. |
| Calibration ECE (out-of-time, per family) | < 0.08 | Yes | Predicted probabilities unreliable. Apply Platt scaling or investigate distribution shift. |
| Portfolio simulation MOIC vs random baseline | > 1.3x random | Yes | Signal isn't worth the complexity. Model-selected portfolio must return 30%+ more than random. |
| Portfolio simulation failure rate vs random | < 0.7x random failure rate | Yes | Model must reduce failure exposure by 30%+ vs random. |
| Claude text score AUC on historical pitches | >= 0.60 (on 30+ samples) | Yes | Text analysis can't discriminate. Reduce text weight from 20% or investigate prompt quality. |
| Progress model AUC (out-of-time, per family) | >= 0.58 | No (advisory) | 18-month model adds no value. Drop it or simplify to a heuristic. |
| Simulated abstention rate | 10-40% of evaluated deals | No (advisory) | Below 10%: gates too loose. Above 40%: gates too strict, tool unusable. |
| Sector bias | No single sector > 50% of "Invest" recommendations | No (advisory) | Model may be overfit to one sector's success pattern. |

**0f. Historical alt data reconstruction**

Several alt data signals can be reconstructed historically, despite my earlier claim that "alt data can't be backtested." A DE would build as-of reconstruction pipelines for these:

| Signal | Historical source | Reconstructable? |
|--------|------------------|-----------------|
| Website existence + change frequency | Wayback Machine CDX API (timestamped snapshots) | Yes — full history |
| GitHub stars + commits | GitHub API (commit history is timestamped, star history via stargazers endpoint with timestamps) | Yes — full history |
| Companies House filings + charges + directors | Companies House API (all events are timestamped) | Yes — full history |
| Press coverage volume + tone | GDELT archive (queryable by date range) | Yes — back to 2015 |
| npm download counts | npm BigQuery dataset (daily downloads by package) | Yes — back to 2015 |
| PyPI downloads | pypistats.org (180-day rolling window) | Partial — recent only |
| Google Trends | Google Trends (can specify date ranges) | Yes — but rate limited |
| FCA authorisation history | FCA Register (historical permissions timestamped) | Yes — full history |
| App store ratings | Not available historically via free APIs | No |
| Job postings | Adzuna does not offer historical data | No |
| Trustpilot, Reddit, ProductHunt | No historical APIs | No |

For backtesting purposes, use the ~8 reconstructable signals to build as-of feature vectors for historical companies. The ~7 non-reconstructable signals are excluded from the backtest (and their weight in the rubric is redistributed proportionally among available signals).

**0g. Entity resolution validation**
- Sample 200 entities from the entity resolution pipeline
- Manually verify canonical matches (is SEC Company X actually the same as Companies House Company Y?)
- Measure precision (% of auto-confirmed matches that are correct) — target: >= 95%
- Measure recall (% of true matches the system found) — target: >= 80%
- If precision < 95%, raise the entity-match confidence threshold
- If recall < 80%, add more matching rules or lower the probabilistic threshold (with manual review for borderline cases)

**0h. Backtest provenance log**

Every backtest run is logged in the `backtest_runs` table (see Database Schema section for full schema, which includes additional fields: `alt_data_signals_included`, `pass_fail` per-metric breakdown, and `all_passed` flag).

### During Phase 2: Model Training Backtests

**Rolling walk-forward validation:**
- Run all 5 time windows (0a above) for each stage-country model family
- Check: is AUC consistent across windows, or does it drop in specific periods?
- If AUC drops sharply in one window (e.g., 2021 — COVID/ZIRP regime), the model is regime-sensitive. Check whether macro regime features improve robustness.

**Regime sensitivity analysis:**
- Run the full backtest with and without macro regime features
- Compare AUC and portfolio MOIC across different regime periods
- If macro features don't improve out-of-time performance, drop them (complexity without value)

**Feature ablation:**
- For each feature family (campaign, company, team, financial, traction, terms, regulatory, regime), train a model without that family and compare AUC
- If removing a family doesn't hurt AUC, the features are noise — remove them
- If removing a family drops AUC significantly, those features are load-bearing — investigate individual feature importance within the family

**Portfolio simulation:**
- Run the portfolio simulator (0d) for each walk-forward window
- Apply investor policy constraints (max 2/year, max 1/sector)
- Compare model-selected portfolio against 3 baselines
- Report: simulated MOIC, failure rate, abstention rate

### During Phase 3: Claude Integration Backtests

**Historical pitch recovery:**
- Use Wayback Machine to recover archived crowdfunding platform pages for 30-50 companies with known outcomes
- Also use SEC Form C "use of proceeds" text as a lower-quality substitute for pitch text
- Target: 30+ recoverable pitches (10 from known successes, 10 from known failures, 10 mixed)

**Claude text discrimination test:**
- Run Claude Stage 1 on all recovered pitches
- Measure: AUC of text_quality_score alone against outcome
- Check individual dimensions: which text scores correlate most with survival? (Expected: claims_plausibility and risk_honesty, based on academic findings)
- If AUC < 0.60, the text weight (20%) is too high — reduce or investigate prompt

**Reference set calibration:**
- Curate fixed set of 20 pitches (10 success, 10 failure)
- Run Claude Stage 1 three times on each pitch
- Measure: score consistency (std dev across runs), mean gap between success/failure groups
- This becomes the ongoing prompt drift baseline

**Integration test:**
- For the ~30 recovered pitches, combine structured model score + Claude text score
- Compare combined AUC against structured-only AUC
- If adding text analysis doesn't improve AUC, the integration approach needs rethinking (maybe the weighting is wrong, or the prompt needs iteration)

### During Phase 5: Paper Trading Backtests

This is the only test of the complete pipeline (all signals, all gates, all policy constraints).

**Protocol:**
- Evaluate 30+ live deals across UK and US platforms using Quick + Deep Score
- Log all recommendations — do not act on them
- At 18 months: check progress model predictions against actual milestones
- At 36 months (or earlier if outcome is known): check survival model predictions

**Go-live gate:** same numeric KPI thresholds as Phase 5a (top-decile hit rate, downside capture, discrimination, sector bias, calibration, abstention rate). All must pass simultaneously.

If the model fails the go-live gate, it stays in shadow mode and parameters are tuned. The gate is not moved to accommodate the model.

---

## Database Schema

```sql
-- ============================================================
-- ENTITY RESOLUTION
-- ============================================================

canonical_entities (
  id uuid PRIMARY KEY,
  primary_name text NOT NULL,
  country text NOT NULL,
  sector text,
  founding_date date,
  created_at timestamptz DEFAULT now(),
  updated_at timestamptz DEFAULT now()
)

entity_links (
  id uuid PRIMARY KEY,
  entity_id uuid REFERENCES canonical_entities NOT NULL,
  source text NOT NULL,             -- sec_edgar, companies_house, academic, manual
  source_identifier text NOT NULL,  -- CIK, company number, etc.
  source_name text,                 -- name as it appears in this source
  match_method text NOT NULL,       -- exact_id, deterministic, probabilistic
  confidence integer NOT NULL,      -- 0-100
  review_status text DEFAULT 'auto_confirmed',  -- auto_confirmed, needs_review, rejected
  created_at timestamptz DEFAULT now()
)

-- ============================================================
-- AS-OF FEATURE STORE
-- ============================================================

feature_store (
  id uuid PRIMARY KEY,
  entity_id uuid REFERENCES canonical_entities NOT NULL,
  as_of_date date NOT NULL,
  feature_family text NOT NULL,     -- campaign, company, team, financial, traction,
                                    --   terms, regulatory, market_regime, evidence
  feature_name text NOT NULL,
  feature_value jsonb NOT NULL,
  source text NOT NULL,
  label_quality_tier smallint NOT NULL,  -- 1=verified, 2=estimated, 3=weak
  created_at timestamptz DEFAULT now(),
  UNIQUE (entity_id, as_of_date, feature_family, feature_name)
)

-- ============================================================
-- TRAINING DATA
-- ============================================================

companies (
  id uuid PRIMARY KEY,
  entity_id uuid REFERENCES canonical_entities,
  name text NOT NULL,
  ticker text,
  country text NOT NULL,
  sector text,
  sic_code text,
  founding_date date,
  ipo_date date,
  ipo_exchange text,
  source text NOT NULL,
  source_id text,
  current_status text,
  status_verified_date date,
  created_at timestamptz DEFAULT now()
)

financial_data (
  id uuid PRIMARY KEY,
  company_id uuid REFERENCES companies,
  period_end_date date NOT NULL,
  period_type text,
  revenue numeric,
  revenue_growth_yoy numeric,
  gross_profit numeric,
  gross_margin numeric,
  operating_income numeric,
  net_income numeric,
  cash_and_equivalents numeric,
  total_assets numeric,
  total_liabilities numeric,
  total_debt numeric,
  employee_count integer,
  burn_rate_monthly numeric,
  customers integer,
  source_filing text
)

funding_rounds (
  id uuid PRIMARY KEY,
  company_id uuid REFERENCES companies,
  round_date date,
  round_type text,                  -- seed_equity, safe, convertible_note, series_a, etc.
  instrument_type text,             -- equity, safe, convertible_note, asa
  amount_raised numeric,
  pre_money_valuation numeric,
  post_money_valuation numeric,
  -- SAFE/convertible-specific terms
  valuation_cap numeric,            -- null for uncapped SAFEs (flag as high risk)
  discount_rate numeric,            -- e.g., 0.20 for 20% discount
  mfn_clause boolean,               -- most favoured nation
  interest_rate numeric,            -- convertible notes only
  maturity_date date,               -- convertible notes only
  -- Liquidation preference
  liquidation_preference_multiple numeric DEFAULT 1.0,  -- 1x, 2x, etc.
  liquidation_participation text,   -- non_participating, participating, capped_participating
  seniority_position integer,       -- 1 = most senior, higher = more junior
  -- Pro-rata rights
  pro_rata_rights boolean,
  pro_rata_amount numeric,
  -- Standard fields
  lead_investor text,
  qualified_institutional boolean,
  platform text,
  overfunding_ratio numeric,
  investor_count integer,
  funding_velocity_days integer,
  eis_seis_eligible boolean,
  qsbs_eligible boolean,            -- US Section 1202 qualification
  source text
)

stock_prices (
  company_id uuid REFERENCES companies,
  date date,
  close_price numeric,
  volume bigint,
  PRIMARY KEY (company_id, date)
)

ipo_outcomes (
  company_id uuid PRIMARY KEY REFERENCES companies,
  ipo_price numeric,
  ipo_market_cap numeric,
  macro_regime text,
  alpha_1yr numeric,
  alpha_3yr numeric,
  alpha_5yr numeric,
  return_1yr numeric,
  return_3yr numeric,
  return_5yr numeric,
  max_drawdown_3yr numeric,
  success_tier smallint
)

crowdfunding_outcomes (
  id uuid PRIMARY KEY,
  company_id uuid REFERENCES companies,
  platform text,
  campaign_date date,
  funding_target numeric,
  amount_raised numeric,
  overfunding_ratio numeric,
  equity_offered numeric,
  pre_money_valuation numeric,
  investor_count integer,
  funding_velocity_days integer,
  eis_seis_eligible boolean,
  qualified_institutional_coinvestor boolean,
  prior_vc_backing boolean,
  accelerator_alumni boolean,
  accelerator_name text,
  founder_count smallint,
  founder_domain_experience_years integer,
  founder_prior_exits boolean,
  had_revenue boolean,
  revenue_at_raise numeric,
  revenue_model text,
  company_age_at_raise_months integer,
  sector text,
  country text,
  stage_bucket text NOT NULL,       -- seed, early_growth
  outcome text NOT NULL,
  outcome_detail text,
  outcome_date date,
  years_to_outcome numeric,
  label_quality_tier smallint NOT NULL,  -- 1, 2, or 3
  data_source text
)

-- ============================================================
-- MODEL REGISTRY
-- ============================================================

model_versions (
  id serial PRIMARY KEY,
  family text NOT NULL,             -- UK_Seed, UK_EarlyGrowth, US_Seed, US_EarlyGrowth
  model_type text NOT NULL,         -- survival, progress
  version text NOT NULL,
  trained_at timestamptz NOT NULL,
  training_samples integer,
  tier1_samples integer,
  tier2_samples integer,
  validation_method text,
  validation_auc numeric,
  test_auc numeric,
  calibration_ece numeric,
  feature_importance jsonb,         -- SHAP rankings
  hyperparameters jsonb,
  artifact_path text,               -- path to serialised model
  release_status text DEFAULT 'candidate',  -- candidate, released, retired
  notes text
)

-- ============================================================
-- RUBRIC & SCORING
-- ============================================================

rubric_versions (
  id serial PRIMARY KEY,
  version text NOT NULL,
  generated_at timestamptz NOT NULL,
  model_versions jsonb,             -- [model_version_ids used]
  model_a_summary text,
  validation_method text,
  category_weights jsonb,
  scoring_thresholds jsonb,
  academic_overrides jsonb,
  prompt_versions jsonb,            -- {stage1: hash, stage2: hash, stage3: hash}
  notes text
)

evaluations (
  id uuid PRIMARY KEY,
  rubric_version_id integer REFERENCES rubric_versions,
  model_family text,                -- UK_Seed, US_EarlyGrowth, etc.
  model_version_id integer REFERENCES model_versions,
  evaluation_type text NOT NULL,    -- quick, deep, follow_on
  entity_id uuid REFERENCES canonical_entities,
  entity_match_confidence integer,
  company_name text NOT NULL,
  platform text,
  listing_url text,
  -- Input data
  manual_inputs jsonb NOT NULL,
  pitch_text text,
  founder_qa_text text,
  founder_content_text text,
  alt_data jsonb,
  alt_data_fetched_at timestamptz,
  -- Claude analysis (Stage 1)
  text_scores jsonb,
  -- Claude analysis (Stage 2 -- deep only)
  competitive_landscape text,
  qualitative_narrative text,
  qualitative_modifier integer,
  -- Claude analysis (Stage 3 -- deep only)
  pre_mortem jsonb,
  -- Return distribution (deep only)
  return_distribution jsonb,        -- {p10, p50, p90, expected_value,
                                    --  p10_with_eis, p50_with_eis, p90_with_eis,
                                    --  expected_value_with_eis}
  -- Valuation analysis (deep only)
  valuation_analysis jsonb,         -- {entry_multiple, sector_median,
                                    --  dilution_model, exit_path_assessment}
  -- Model outputs
  survival_probs jsonb,             -- {p_survive, p_exit, p_fail}
  progress_prob numeric,            -- 18-24 month milestone probability
  -- Scoring
  quantitative_score numeric NOT NULL,
  confidence_lower numeric NOT NULL,
  confidence_upper numeric NOT NULL,
  confidence_level text NOT NULL,
  category_scores jsonb NOT NULL,
  risk_flags jsonb,
  missing_data_fields jsonb,
  -- Decision engine
  abstention_gates jsonb,           -- {gate_name: {passed: bool, value: x, threshold: y}}
  kill_criteria_triggered jsonb,    -- [{criterion, source, action}] or null
  recommendation_class text NOT NULL,  -- invest, deep_diligence, watch, pass, abstain
  -- Quick Score recommendation (quick only)
  quick_recommendation text,
  -- Metadata
  created_at timestamptz DEFAULT now(),
  notes text
)

-- ============================================================
-- PORTFOLIO & ANTI-PORTFOLIO
-- ============================================================

investments (
  id uuid PRIMARY KEY,
  evaluation_id uuid REFERENCES evaluations,
  entity_id uuid REFERENCES canonical_entities,
  company_name text NOT NULL,
  platform text,
  invested_date date NOT NULL,
  amount_invested numeric NOT NULL,
  evaluation_score numeric,
  model_family text,
  rubric_version_id integer REFERENCES rubric_versions,
  -- Outcome tracking
  current_status text DEFAULT 'active',
  last_status_check date,
  outcome_date date,
  outcome_multiple numeric,
  follow_on_raises jsonb,
  outcome_notes text,
  created_at timestamptz DEFAULT now()
)

anti_portfolio (
  id uuid PRIMARY KEY,
  evaluation_id uuid REFERENCES evaluations,
  entity_id uuid REFERENCES canonical_entities,
  company_name text NOT NULL,
  platform text,
  passed_date date NOT NULL,
  evaluation_score numeric,
  evaluation_type text,
  pass_reason text NOT NULL,
  pass_notes text,
  rubric_version_id integer REFERENCES rubric_versions,
  -- Outcome tracking
  current_status text,
  last_status_check date,
  subsequent_raise_amount numeric,
  subsequent_raise_valuation numeric,
  outcome_notes text,
  created_at timestamptz DEFAULT now()
)

follow_on_evaluations (
  id uuid PRIMARY KEY,
  original_investment_id uuid REFERENCES investments,
  evaluation_id uuid REFERENCES evaluations,
  follow_on_round_date date,
  new_valuation numeric,
  new_round_amount numeric,
  milestones_hit jsonb,             -- [{milestone, met: boolean}, ...]
  progress_model_prediction numeric,  -- what did the model predict at investment?
  actual_progress boolean,          -- did the milestone actually happen?
  recommendation text,              -- increase, maintain, do_not_follow
  recommendation_reasoning text,
  created_at timestamptz DEFAULT now()
)

-- ============================================================
-- SELECTION-BIAS FUNNEL
-- ============================================================

deal_funnel (
  id uuid PRIMARY KEY,
  entity_id uuid REFERENCES canonical_entities,
  company_name text NOT NULL,
  platform text,
  sector text,
  country text,
  -- Funnel stages (timestamped)
  existed_at timestamptz,           -- when the deal appeared (sourcing alert or manual log)
  seen_at timestamptz,              -- when you opened/viewed the listing
  assessed_at timestamptz,          -- when you ran Quick or Deep Score
  evaluation_id uuid REFERENCES evaluations,
  decision text,                    -- invested, passed, watching, not_assessed
  decision_at timestamptz,
  -- Outcome (for non-assessed deals -- reveals what you missed)
  outcome text,                     -- unknown, trading, failed, raised_again, exited
  outcome_checked_at timestamptz,
  notes text,
  created_at timestamptz DEFAULT now()
)

-- ============================================================
-- DEAL SOURCING
-- ============================================================

deal_alerts (
  id uuid PRIMARY KEY,
  source text NOT NULL,
  company_name text NOT NULL,
  sector text,
  offering_amount numeric,
  revenue numeric,
  listing_url text,
  alert_date timestamptz DEFAULT now(),
  status text DEFAULT 'new',
  evaluation_id uuid REFERENCES evaluations,
  deal_funnel_id uuid REFERENCES deal_funnel,  -- auto-linked
  notes text
)

alert_criteria (
  id serial PRIMARY KEY,
  sectors jsonb NOT NULL,
  min_revenue numeric,
  max_offering_amount numeric,
  countries jsonb,
  eis_required boolean DEFAULT false,
  active boolean DEFAULT true
)

-- ============================================================
-- INVESTOR POLICY
-- ============================================================

investor_policy (
  id serial PRIMARY KEY,
  max_investments_per_year integer DEFAULT 2,
  check_size numeric DEFAULT 10000,
  check_currency text DEFAULT 'GBP',
  max_per_sector_per_year integer DEFAULT 1,
  no_forced_deployment boolean DEFAULT true,
  compliance_hard_blocks boolean DEFAULT true,
  active boolean DEFAULT true,
  updated_at timestamptz DEFAULT now()
)

-- ============================================================
-- MONITORING & GOVERNANCE
-- ============================================================

calibration_log (
  id serial PRIMARY KEY,
  model_version_id integer REFERENCES model_versions,
  checked_at timestamptz DEFAULT now(),
  sample_size integer,
  ece numeric,
  brier_score numeric,
  status text,                      -- healthy, warning, critical
  notes text
)

prompt_calibration_log (
  id serial PRIMARY KEY,
  rubric_version_id integer REFERENCES rubric_versions,
  checked_at timestamptz DEFAULT now(),
  reference_set_mean numeric,       -- mean score on 20 reference pitches
  baseline_mean numeric,            -- original mean when prompt was deployed
  drift numeric,                    -- absolute difference
  status text,                      -- healthy, warning, critical
  notes text
)

recommendation_log (
  id uuid PRIMARY KEY,
  evaluation_id uuid REFERENCES evaluations,
  recommendation_class text NOT NULL,
  policy_override text,             -- null if no override; e.g., "annual_cap_exceeded"
  policy_override_reason text,
  user_override text,               -- null if no override; otherwise the action taken
  override_reason text,
  eventual_outcome text,            -- filled in later when outcome is known
  outcome_recorded_at timestamptz,
  created_at timestamptz DEFAULT now()
)

-- ============================================================
-- BACKTESTING
-- ============================================================

backtest_holdout (
  id uuid PRIMARY KEY,
  entity_id uuid REFERENCES canonical_entities,
  company_id uuid REFERENCES companies,
  holdout_window text NOT NULL,     -- e.g., "2023-2025"
  created_at timestamptz DEFAULT now()
  -- This table is physically separated from training pipeline access.
  -- Records are created once before model training and never modified.
)

backtest_runs (
  id serial PRIMARY KEY,
  run_date timestamptz DEFAULT now(),
  model_family text,
  model_version_id integer REFERENCES model_versions,
  data_snapshot_date date,
  train_window text,
  test_window text,
  features_active jsonb,
  alt_data_signals_included jsonb,  -- which of the 8 reconstructable signals were used
  metrics jsonb,                    -- {auc, ece, portfolio_moic, failure_rate, ...}
  baselines jsonb,                  -- {random: {...}, heuristic: {...}, momentum: {...}}
  pass_fail jsonb,                  -- {metric_name: {value, threshold, passed}}
  all_passed boolean,
  notes text
)

-- ============================================================
-- INDEXES
-- ============================================================

-- Feature store: training queries pivot on entity + date
CREATE INDEX idx_feature_store_entity_date ON feature_store(entity_id, as_of_date);
CREATE INDEX idx_feature_store_family ON feature_store(feature_family, feature_name);

-- Evaluations: portfolio views, monitoring queries
CREATE INDEX idx_evaluations_entity ON evaluations(entity_id, created_at DESC);
CREATE INDEX idx_evaluations_model ON evaluations(model_family, recommendation_class);
CREATE INDEX idx_evaluations_type ON evaluations(evaluation_type, created_at DESC);

-- Entity resolution: lookup by source identifier
CREATE INDEX idx_entity_links_source ON entity_links(source, source_identifier);
CREATE INDEX idx_entity_links_entity ON entity_links(entity_id);

-- Deal funnel: sourcing analysis
CREATE INDEX idx_deal_funnel_platform ON deal_funnel(platform, existed_at DESC);
CREATE INDEX idx_deal_funnel_sector ON deal_funnel(sector, country);

-- Portfolio: outcome tracking
CREATE INDEX idx_investments_status ON investments(current_status, invested_date DESC);
CREATE INDEX idx_anti_portfolio_date ON anti_portfolio(passed_date DESC);

-- Monitoring: calibration and recommendation audit
CREATE INDEX idx_calibration_log_model ON calibration_log(model_version_id, checked_at DESC);
CREATE INDEX idx_recommendation_log_eval ON recommendation_log(evaluation_id);
CREATE INDEX idx_recommendation_log_override ON recommendation_log(user_override) WHERE user_override IS NOT NULL;

-- Training data: model training queries
CREATE INDEX idx_crowdfunding_outcomes_stage ON crowdfunding_outcomes(stage_bucket, country, label_quality_tier);
CREATE INDEX idx_companies_entity ON companies(entity_id);

-- Backtest: holdout lookup
CREATE INDEX idx_backtest_holdout_window ON backtest_holdout(holdout_window);

-- Training-ready materialized view (refreshed before each training run)
-- Pivots the EAV feature_store into a wide feature matrix for XGBoost
CREATE MATERIALIZED VIEW training_features_wide AS
SELECT
  entity_id,
  as_of_date,
  MAX(CASE WHEN feature_name = 'revenue_at_raise' THEN (feature_value->>'value')::numeric END) AS revenue_at_raise,
  MAX(CASE WHEN feature_name = 'employee_count' THEN (feature_value->>'value')::numeric END) AS employee_count,
  -- ... (one column per feature, generated programmatically from feature registry)
  MAX(label_quality_tier) AS worst_label_tier
FROM feature_store
WHERE label_quality_tier <= 2  -- exclude Tier 3
GROUP BY entity_id, as_of_date;

CREATE UNIQUE INDEX idx_training_wide_entity ON training_features_wide(entity_id, as_of_date);
```

---

## Results Dashboard

### Quick Score Result

```
+---------------------------------------------------------------+
|  STARTUPLENS -- QUICK SCORE                                   |
|  ===========================================                  |
|  Acme Fintech Ltd  -  Crowdcube  -  28 Feb 2026              |
|  Model: UK_Seed v1                                            |
|                                                               |
|  ROUGH SCORE: 62 +/- 25 (Low confidence)                     |
|                                                               |
|  Recommendation: INVESTIGATE FURTHER                          |
|  Gates: 5/6 passed (evidence quality: marginal)               |
|                                                               |
|  Text quality: 75/100 (above average)                         |
|  Google Trends: Rising (+35%)                                 |
|  Press coverage: 12 articles (90d), tone: +4.2                |
|  Companies House: Active, no flags                            |
|                                                               |
|  Key signal: Pitch demonstrates genuine domain expertise      |
|  in regulatory compliance. Claims are specific and            |
|  measurable.                                                  |
|                                                               |
|  Red flags from text:                                         |
|  - TAM claim of 12B not sourced                               |
|  - "No direct competitors" claim is likely false              |
|                                                               |
|  [Run Deep Score]  [Pass + Log to Anti-Portfolio]             |
+---------------------------------------------------------------+
```

### Deep Score Result

```
+---------------------------------------------------------------+
|  STARTUPLENS -- DEEP SCORE                                    |
|  ===========================================                  |
|  Acme Fintech Ltd  -  Crowdcube  -  28 Feb 2026              |
|  Model: UK_Seed v1  -  Rubric v2  -  Data: 82% complete      |
|                                                               |
|  +----------------------------------------------------------+|
|  |  OVERALL SCORE                                            ||
|  |                                                           ||
|  |  BASE RATE: ECF companies are 8.5x more likely to fail.  ||
|  |  This score identifies whether this company is an         ||
|  |  exception.                                               ||
|  |                                                           ||
|  |       57 ------------ 68 ------------ 79                  ||
|  |       low           score           high                  ||
|  |                  +/- 11 points                            ||
|  |             Confidence: HIGH                              ||
|  |                                                           ||
|  |  Cohort: UK_Seed (top ~32% of UK seed deals)             ||
|  |  Qualitative modifier: +3 (market timing)                 ||
|  |  Final adjusted score: 71                                 ||
|  |                                                           ||
|  |  Recommendation: DEEP DILIGENCE                           ||
|  |  All 6 abstention gates passed                            ||
|  +----------------------------------------------------------+|
|                                                               |
|  MODEL PREDICTIONS                                            |
|  +----------------------------------------------------------+|
|  | Survival model:                                           ||
|  |   P(survive 3yr): 58%  P(exit): 8%  P(fail): 34%         ||
|  |                                                           ||
|  | Progress model (18-month milestone):                      ||
|  |   P(follow-on raise or 2x revenue): 41%                  ||
|  |   Check back: Aug 2027                                    ||
|  +----------------------------------------------------------+|
|                                                               |
|  CATEGORY BREAKDOWN                                           |
|  +----------------------------------------------------------+|
|  | Text & Narrative     ################....  78  (wt 20%)   ||
|  | Traction & Growth    ############........  62  (wt 18%)   ||
|  | Deal Terms & Value   ##########..........  55  (wt 15%)   ||
|  |   Entry: 40x rev (sector median: 15x) -- AGGRESSIVE      ||
|  |   EIS relief improves effective return by ~30%            ||
|  |   Dilution to exit: ~65% (3 rounds estimated)             ||
|  | Team                 #############.......  67  (wt 15%)   ||
|  |   Q&A quality: Thoughtful, acknowledges uncertainty       ||
|  | Financial Health     ##########..........  52  (wt 12%)   ||
|  | Investment Signal    ################....  80  (wt 10%)   ||
|  |   Institutional co-investor: YES (+15 bonus)              ||
|  | Market               ##############......  72  (wt 10%)   ||
|  +----------------------------------------------------------+|
|                                                               |
|  RETURN DISTRIBUTION (probabilistic, after dilution)          |
|  +----------------------------------------------------------+|
|  |         P10        P50        P90        E[V]             ||
|  | Raw:    0.0x       0.3x       4.8x       1.2x            ||
|  | + EIS:  0.3x       0.6x       5.1x       1.5x            ||
|  |                                                           ||
|  | 62% probability of total loss (pre-EIS)                   ||
|  | 8% probability of 5x+ return                              ||
|  +----------------------------------------------------------+|
|                                                               |
|  COMPETITIVE LANDSCAPE (auto-generated by Claude)             |
|  +----------------------------------------------------------+|
|  | Identified 5 competitors:                                 ||
|  | - RegTech Co (Series B, $25M raised) -- direct overlap    ||
|  | - CompliAI (Series A, $8M) -- similar market, diff angle  ||
|  | - BigCo Compliance (incumbent, $2B rev) -- enterprise     ||
|  | - StartupX (Seed, $1.5M) -- early, same thesis            ||
|  | - OpenReg (open source) -- free alternative, limited      ||
|  |                                                           ||
|  | Assessment: Claimed UX differentiation is plausible but   ||
|  | not defensible. RegTech Co has 10x the engineering team.   ||
|  | Regulatory moat (FCA authorisation) is the real barrier.  ||
|  +----------------------------------------------------------+|
|                                                               |
|  ALT DATA SIGNALS (auto-fetched)                              |
|  +----------------------------------------------------------+|
|  | Google Trends (12mo):  Rising (+35%)                      ||
|  | SimilarWeb rank:       #245,000 (up from #310k)           ||
|  | Press coverage (90d):  12 articles, avg tone: +4.2        ||
|  | Open jobs (Adzuna):    6 roles (3 eng, 2 sales, 1 ops)   ||
|  | Trustpilot:            4.2/5 (89 reviews)                 ||
|  | FCA status:            Authorised (e-money issuer)        ||
|  | Companies House:       Active, 1 charge (BBB -- good)     ||
|  | Director checks:       Clean                              ||
|  +----------------------------------------------------------+|
|                                                               |
|  PITCH TEXT ANALYSIS                                          |
|  +----------------------------------------------------------+|
|  | Clarity: 82   Claims plausibility: 68   Risk honesty: 60 ||
|  | Problem: 75   Differentiation: 72       Biz model: 80    ||
|  | Domain signal: 85                                         ||
|  | Text quality score: 75 (above average)                    ||
|  +----------------------------------------------------------+|
|                                                               |
|  RISK FLAGS                                                   |
|  - Burn multiple 3.2x (healthy: <2x)                         |
|  - No prior founder exits                                     |
|  - 312 investors -- high dispersion weakens monitoring        |
|  - Entry valuation aggressive (40x vs 15x sector median)     |
|  - 14 months runway at current burn                           |
|                                                               |
|  ABSTENTION GATE STATUS                                       |
|  +----------------------------------------------------------+|
|  | Data completeness:     82% >= 60%           PASSED        ||
|  | Entity match:          95  >= 70             PASSED        ||
|  | Model confidence:      entropy 0.72 < 0.9   PASSED        ||
|  | Return spread:         P90/P10 = 4.8x < 50x PASSED        ||
|  | Calibration health:    ECE 0.04 < 0.10      PASSED        ||
|  | Evidence quality:      5 sources >= 3        PASSED        ||
|  +----------------------------------------------------------+|
|                                                               |
|  AI DUE DILIGENCE BRIEF                                       |
|  -----------------------------------------------------------  |
|  [Competitive Landscape section -- see above]                 |
|                                                               |
|  Moat: Moderate. FCA authorisation creates 12-18 month        |
|  regulatory barrier. Switching costs exist but data            |
|  portability reduces lock-in. No network effects.             |
|                                                               |
|  Timing: Strong. Open Banking accelerating, PSD3 increasing   |
|  compliance burden. Market entering growth phase.             |
|                                                               |
|  Team: Domain expertise genuine (8yr Deloitte compliance).    |
|  Q&A responses show intellectual honesty -- acknowledges      |
|  competitive risk from RegTech Co rather than dismissing it.  |
|  Gap: no prior startup experience; fundraising and hiring     |
|  are learned skills first-time founders struggle with.        |
|                                                               |
|  Biggest risk: Burns through runway before 50k MRR.           |
|                                                               |
|  Bull case: FCA mandates new compliance reporting for all     |
|  regulated SMEs, creating forced adoption.                    |
|                                                               |
|  Base rate override: Partially. Institutional co-investor     |
|  (Seedcamp) + FCA authorisation are strong exception signals. |
|                                                               |
|  PRE-MORTEM                                                    |
|  -----------------------------------------------------------  |
|  "Acme burned through its runway trying to compete on price   |
|  with RegTech Co, which raised a $25M Series B six months     |
|  after Acme's crowdfunding round. As first-time founders,     |
|  the team waited too long to cut burn and pivot to a niche    |
|  vertical. By month 18, they attempted a bridge round, but    |
|  the aggressive entry valuation made a flat round impossible  |
|  to swallow for existing investors. The company dissolved at  |
|  month 22 with 3 months of runway remaining, unable to close  |
|  new funding at any price."                                   |
|                                                               |
|  Preventable: Yes (earlier niche pivot + burn discipline)     |
|  Probability: Medium                                          |
|  Early warning signs:                                         |
|  - RegTech Co announces new product in Acme's segment         |
|  - Monthly burn increases rather than decreases post-raise    |
|  - No follow-on VC interest within 12 months                  |
|  -----------------------------------------------------------  |
|                                                               |
|  MISSING DATA (would improve confidence)                      |
|  - Net revenue retention % (critical for SaaS)                |
|  - Customer count (unit economics estimate)                   |
|  - LinkedIn headcount 6mo ago (growth signal)                 |
|                                                               |
|  [Save] [Add to Portfolio] [Log Pass to Anti-Portfolio]       |
|  [Follow-On Eval] [Export Investment Memo] [New Evaluation]   |
+---------------------------------------------------------------+
```

### Investment Memo Export

The Export button generates a structured document using Claude to reformat all evaluation data:

```
INVESTMENT MEMO -- [Company Name]
Date: [date]
Analyst: StartupLens [model family] v[rubric version]
Recommendation: [Invest / Deep Diligence / Watch / Pass / Abstain]
Score: [score +/- range] ([confidence level])
Model predictions: P(survive): [x]%, P(exit): [x]%, P(fail): [x]%
18-month progress probability: [x]%

1. COMPANY OVERVIEW
   [From company inputs + pitch text summary]

2. MARKET OPPORTUNITY
   [TAM analysis, timing assessment, regulatory context]

3. COMPETITIVE LANDSCAPE
   [Auto-generated competitive analysis from Claude Stage 2]

4. PRODUCT & TRACTION
   [Revenue, growth, alt data signals, app/GitHub metrics]

5. TEAM
   [Founder assessment, Q&A analysis, career trajectory]

6. FINANCIALS & UNIT ECONOMICS
   [Revenue, margins, burn, runway, capital efficiency]

7. DEAL TERMS & RETURN ANALYSIS
   [Entry valuation, dilution model, return distribution P10/P50/P90, EIS impact]

8. KEY RISKS
   [Risk flags + pre-mortem early warning signs]

9. PRE-MORTEM: HOW THIS FAILS
   [Full pre-mortem narrative]

10. BULL CASE: HOW THIS RETURNS 10X
    [Specific bull case scenario]

11. DECISION GATES
    [Abstention gate status + kill criteria check]

12. RECOMMENDATION & CONVICTION LEVEL
    [Score, confidence, recommendation class, qualitative modifier, final assessment]

---
Generated by StartupLens | Model: [family] v[x] | Rubric v[x] | [date]
```

### Portfolio Dashboard

```
+---------------------------------------------------------------+
|  PORTFOLIO OVERVIEW                                           |
|  ===========================================                  |
|                                                               |
|  SUMMARY                                                      |
|  Total invested: 12,500                                       |
|  Active positions: 7                                          |
|  Written off: 1                                               |
|  Exited: 0                                                    |
|  Anti-portfolio tracked: 23                                   |
|                                                               |
|  CONCENTRATION WARNINGS                                       |
|  ! 57% of capital in fintech (>40% threshold)                 |
|  ! All investments are seed-stage                             |
|  ! 5 of 7 investments made in last 6 months                   |
|                                                               |
|  RUBRIC ACCURACY                                              |
|  Avg score of active investments: 68                          |
|  Score of written-off investment: 42                          |
|  Anti-portfolio: 3 of 23 passes subsequently raised           |
|    follow-on (false negative rate: 13%)                       |
|                                                               |
|  18-MONTH PROGRESS CHECK                                      |
|  Model predicted 41% milestone rate for portfolio             |
|  Actual: 3 of 5 eligible hit milestone (60%)                  |
|  (Too early to draw conclusions -- need 20+ data points)      |
|                                                               |
|  SECTOR BREAKDOWN          SCORE DISTRIBUTION                 |
|  Fintech:    4 (57%)       40-50: 1 (written off)             |
|  Healthtech: 1 (14%)       50-60: 1                           |
|  SaaS:       1 (14%)       60-70: 3                           |
|  Consumer:   1 (14%)       70-80: 2                           |
|                                                               |
|  SELECTION-BIAS FUNNEL (last 6 months)                        |
|  Existed: 142 deals | Seen: 38 | Assessed: 14 | Invested: 2  |
|  ! Only 27% of deals seen -- consider broadening sources      |
|  ! 0 healthtech deals assessed (blind spot?)                  |
|                                                               |
|  MODEL HEALTH                                                 |
|  UK_Seed:  ECE 0.04 (healthy)  Last checked: 15 Feb 2026     |
|  US_Seed:  ECE 0.06 (healthy)  Last checked: 15 Feb 2026     |
|  Prompt drift: +2.1 points from baseline (healthy)            |
|                                                               |
|  FOLLOW-ON DECISIONS PENDING                                  |
|  - Company B: Series A round announced, 3x valuation step-up  |
|    Original progress prediction: 55% -> Actual: hit milestone |
|    [Run Follow-On Evaluation]                                 |
+---------------------------------------------------------------+
```

---

## Tech Stack

| Component | Technology | Hosting | Cost |
|-----------|-----------|---------|------|
| Frontend + API routes | Next.js 15 (App Router, TypeScript) | Vercel free tier | 0/mo |
| Database | PostgreSQL | Supabase free tier (500MB) | 0/mo |
| UI | shadcn/ui + Tailwind CSS + Recharts | Bundled | 0 |
| ML models | XGBoost (Python, exported as JSON per family) | Python FastAPI microservice (Railway free tier or Render) called from Next.js API routes | 0 |
| Entity resolution | Python (`dedupe` library + custom rules) | Runs in data pipeline | 0 |
| Feature store | PostgreSQL (feature_store table) | Supabase | 0 |
| Data pipeline | Python 3.12 (pandas, httpx, xgboost, shap) | Local / GitHub Actions | 0/mo |
| Deal sourcing cron | Python script | GitHub Actions (daily) | 0/mo |
| S-1 extraction | edgartools + Claude Haiku 4.5 | Anthropic API | ~40 one-time |
| UK filing parsing | ixbrlparse + Claude Sonnet 4.5 | Anthropic API | ~10 one-time |
| Evaluation AI (3 stages) | Claude Sonnet 4.5 | Anthropic API | ~5-8/mo |
| Model calibration | scikit-learn (Platt scaling / isotonic) | Runs in training pipeline | 0 |
| US stock data | yfinance | Free | 0 |
| UK stock data | yfinance (.L) + Twelve Data | Free tier | 0 |
| Alt data: trends | Google Trends (pytrends) | Free | 0 |
| Alt data: press | GDELT Doc 2.0 API | Free | 0 |
| Alt data: jobs | Adzuna API | Free (1K/day) | 0 |
| Alt data: reviews | Trustpilot API | Free | 0 |
| Alt data: GitHub | GitHub REST API | Free (5K/hr) | 0 |
| Alt data: apps | google-play-scraper, app-store-scraper | Free (npm) | 0 |
| Alt data: packages | npmjs.org, pypistats.org | Free | 0 |
| Alt data: social | PRAW (Reddit), ProductHunt GraphQL | Free | 0 |
| Alt data: regulatory | FCA Register API, Companies House API | Free | 0 |
| Alt data: grants | Innovate UK CSV, CORDIS download | Free | 0 |
| Alt data: contracts | Contracts Finder API | Free | 0 |
| Alt data: website | Wayback CDX, SimilarWeb DigitalRank | Free | 0 |
| ML inference service | FastAPI (Python) | Railway free tier or Render free tier | 0/mo |
| **Total ongoing** | | | **~5-8/mo** |
| **One-time data build** | | | **~50** |

**Storage estimate (Supabase):**
- Feature store: ~10,000 entities x 50 features x 5 snapshots = 2.5M rows (~500MB at ~200 bytes/row with JSONB)
- Evaluations + portfolio: ~5,000 rows (~50MB)
- Training data + outcomes: ~15,000 rows (~30MB)
- **Total estimated: ~600MB — exceeds Supabase free tier (500MB)**
- **Plan:** start on free tier during Phase 1 data collection. Upgrade to Supabase Pro ($25/mo) when feature store exceeds 400MB. Alternatively, archive old feature snapshots (keep only latest 2 per entity) to stay under 500MB longer.

---

## Out of Scope (v1)

- **Late-stage models** — add UK_LateGrowth and US_LateGrowth families after MVP stability
- **Tax scenario toggles** — EIS/SEIS/QSBS as explicit what-if scenarios with side-by-side comparison (currently each is factored into the return distribution; expand to interactive multi-scenario explorer)
- **Active learning loop** — automated retraining triggered by outcome feedback (currently: manual retrain after 20+ investments with outcomes)
- **Challenger models** — train alternative model architectures (LightGBM, neural net) and compare against production model for periodic performance competition
- **Historical comparables matching** — deferred until Model A is validated
- **Automated platform scraping** — all platforms prohibit it; manual input + SEC EDGAR for structured US data
- **Paid data sources** (Beauhurst, PitchBook, KingsCrowd Edge) — not justified at personal scale
- **Pipeline orchestration** (Dagster/Prefect) — scripts kept modular, not needed yet
- **Multi-user auth** — personal tool; add later if opened to others
- **Real-time data feeds** — batch pipeline, refreshed quarterly
- **Twitter/X monitoring** — $200/month minimum, not worth it
- **CCJ checks** — 6-10 GBP/search, do manually for serious candidates

---

## What changed: v6 -> v7

| Area | v6 | v7 |
|------|----|----|
| Return modelling | "Weight exit scenarios" (unspecified distributions) | **Explicit exit-multiple distributions** per outcome class — log-normal fits from training data or Correlation Ventures benchmarks, Monte Carlo simulation (1,000 draws), partial recovery modelling for failures |
| Abstention gates | Entropy < 0.9 (raw), return spread < 50x | **Tightened:** normalised entropy < 0.70 (30% more decisive than uniform), return spread < 20x |
| Progress model | Vague "meaningful milestone" (6 possible definitions) | **Strict binary:** follow-on raise at >= valuation OR revenue >= 2x, verified against filings only |
| Model release gates | Single gate for structured + text combined | **Two-stage gates:** Phase 2 gate (structured only), Phase 3 gate (combined must not degrade structured AUC) |
| Calibration method | "Platt or isotonic" (no selection rule) | **Selection rule:** Platt by default; isotonic only if non-monotonic miscalibration AND > 500 calibration samples |
| Go-live gate | 3 qualitative criteria | **7 numeric KPI thresholds** — top-decile hit rate, downside capture, discrimination, sector bias, calibration, abstention rate |
| API reliability | Not addressed | **Full graceful degradation strategy** — required vs optional signals, 10s timeout, circuit breaker (3 failures = 24hr disable), weight redistribution |
| Override governance | Fields in schema but no rules | **Override audit rule** — mandatory reason, quarterly outcome review, underperformance banner |
| Database | Tables only, no indexes | **Full index set** — 15 indexes covering feature store, evaluations, entity links, deal funnel, portfolio, monitoring + training-ready materialized view |
| Storage | "Supabase free tier" | **Storage estimate** (~600MB, exceeds free tier) with upgrade plan |
| XGBoost inference | "API route or Python microservice" (unresolved) | **Resolved:** Python FastAPI microservice on Railway/Render, called from Next.js API routes |
| Backtest schema | Duplicate inline schema (0h) vs formal schema | **Deduplicated:** inline schema removed, references formal schema |

### Previous changes: v5 -> v6

| Area | v5 | v6 |
|------|----|----|
| Backtesting | Paper trading section only | **Full backtesting plan** — Phase 0 infrastructure (walk-forward splitter, test set quarantine, baseline generators, portfolio simulator), predefined pass/fail thresholds, historical alt data reconstruction for 8 signals, entity resolution validation, backtest provenance logging |
| Terms modelling | Equity raises only | **SAFE/convertible note handling** — valuation cap, discount, MFN, interest rate, maturity. Liquidation preference waterfall modelling (non-participating, participating, capped). Pro-rata exercise. Seniority stack. |
| Portfolio policy | Dashboard warnings only | **Hard-coded investor constraints** — max 2 investments/year, £10k check size, max 1 per sector/year, no forced deployment. Policy engine enforces at evaluation time, overrides logged. |
| Abstention consistency | Evidence quality gate routed to "Low Confidence" | **Fixed:** evidence quality gate now routes to **Abstain** like all other gates, consistent with "all gates must pass" rule |
| US tax treatment | QSBS deferred to future | **Basic QSBS modelling** — Section 1202 qualification check, 5-year hold, $10M/$50M thresholds, flags when eligibility is uncertain. Shown alongside EIS/SEIS in return distribution. |
| Calibration safety | ECE thresholds without sample gates | **Minimum sample gates** — ECE only actionable with >= 100 test outcomes. Bootstrap CIs for 50-100 range. Kill-switch requires minimum resolved recommendations before triggering. |
| Data licensing | Not addressed | **Data licensing and retention policy** — per-provider usage rules, 24-hour cache policy, no persistent mirroring of third-party data |
| Walk-forward validation | Single train/test split | **5-window rolling walk-forward** + regime sensitivity analysis + feature ablation |
| Alt data backtesting | "Can't be backtested" | **8 of 15 signals are historically reconstructable** (Wayback Machine, GitHub, Companies House, GDELT, npm, Google Trends, FCA, PyPI) — reconstruction pipelines specified |

### Previous changes: v4 -> v5

| Area | v4 | v5 |
|------|----|----|
| Model architecture | Single XGBoost model for all crowdfunding outcomes | **Stage-country model families** (UK_Seed, UK_EarlyGrowth, US_Seed, US_EarlyGrowth) with separate survival + progress models |
| Short-horizon prediction | Not present | **18-24 month progress model** — gives actionable checkpoint before waiting 5+ years |
| Decision gating | Confidence bands as informational | **Formal abstention gates** — 6 independent gates that must pass before any recommendation. Abstain is a first-class output |
| Kill criteria | Not present | **Hard kill criteria** — director disqualification, sanctions, administration, overdue accounts, cap table governance risk |
| Recommendation classes | Score + invest/pass | **5 classes: Invest, Deep Diligence, Watch, Pass, Abstain** |
| Entity resolution | Assumed simple matching | **Dedicated entity resolution layer** with CIK/Companies House/name/domain matching, confidence scoring, and blocking gate |
| Feature store | Features computed at evaluation time | **As-of feature store** with timestamp, provenance, and label quality tier on every feature |
| Label quality | All training data treated equally | **3-tier label quality** (verified/estimated/weak) with tier-based training weights |
| Return modelling | Heuristic bear/base/bull/moon scenarios | **Probabilistic P10/P50/P90 return distribution** derived from survival model probabilities + dilution model |
| Calibration | Not present | **Platt scaling + ECE monitoring** with quarterly recalibration and release gates |
| Kill-switch | Not present | **Automatic recommendation downgrade** if calibration drifts, data source fails, prompts drift, or systematic errors detected |
| Paper trading | Not present | **Mandatory shadow mode** before live deployment with go-live gate requiring model beats 3 baselines |
| Selection-bias tracking | Anti-portfolio only | **Full funnel tracking**: existed -> seen -> assessed -> invested/passed with blind spot detection |
| Prompt management | Prompts embedded in plan | **+ Version-controlled prompts** with drift detection against 20 reference pitches |
| Model registry | Single rubric_versions table | **Dedicated model_versions table** with per-family artifacts, release status, and training provenance |
| Monitoring | Feedback loop based on outcomes | **+ Data quality monitors, score drift detection, feature importance stability tracking, recommendation log with override tracking** |
| Cohort normalisation | Scores comparable globally | **Scores normalised within stage-country cohort** (a 70 for UK_Seed = top ~30% of UK seed deals) |
| Non-negotiables | Implicit | **Explicit invariant list** at top of document |

---

## Research Sources

### Academic Papers (Model B foundations)
- Ahlers, Cumming, Gunther & Schweizer (2015) — "Signaling in Equity Crowdfunding" — Entrepreneurship Theory & Practice
- Vismara (2020) — "Forecasting Success in Equity Crowdfunding" — Small Business Economics
- Signori & Vismara (2018) — "Does Success Bring Success?" — Journal of Corporate Finance
- Coakley, Lazos & Linares-Zegarra (2022) — "Equity Crowdfunding Founder Teams" — British Journal of Management
- Kleinert & Volkmann (2021) — "Signals in Equity-Based Crowdfunding and Risk of Failure" — Financial Innovation
- Walthoff-Borm, Schwienbacher & Vanacker (2018) — "Equity Crowdfunding, Shareholder Structures, and Firm Performance" — Corporate Governance
- Hornuf, Schmitt & Stenzhorn (2018) — "Equity Crowdfunding in Germany and the UK" — Corporate Governance
- Cumming, Johan & Reardon (2024) — "Institutional Quality and Success in U.S. Equity Crowdfunding" — ScienceDirect
- Mazzocchini & Lucarelli (2023) — "Success or Failure in Equity Crowdfunding? A Systematic Literature Review" — Management Research Review
- Hellmann, Mostipan & Vulkan (2019) — "Be Careful What You Ask For" — NBER Working Paper 26275
- Buttice & Vismara (2022) — "Predicting Business Failure After Crowdfunding Success" — J. Business Venturing Insights

### AI/LLM Validation
- Maarouf & Feuerriegel (2024) — "A Fused Large Language Model for Predicting Startup Success" — European Journal of Operational Research (arXiv:2409.03668)
- VCBench (2025) — "Benchmarking LLMs in Venture Capital" — arXiv:2509.14448
- kNN-ICL (2026) — "Predicting Startup Success Using Large Language Models" — arXiv:2601.16568
- Elitzur, Katz, Muttath & Soberman (2024) — "The Power of Machine Learning Methods to Predict Crowdfunding Success" — ScienceDirect

### Data Sources
- SEC EDGAR Form C datasets — sec.gov/data-research/sec-markets-data/crowdfunding-offerings-data-sets
- SEC EDGAR full-text search — efts.sec.gov
- Companies House API — developer.company-information.service.gov.uk
- FAU Equity Crowdfunding Tracker — business.fau.edu/equity-crowdfunding-tracker
- KingsCrowd — kingscrowd.com
- Harvard Dataverse crowdfunding collection — dataverse.harvard.edu/dataverse/crowdfunding

### Startup Metrics Frameworks
- David Sacks — Burn Multiple
- Paul Graham — Default Alive or Default Dead
- Sequoia — The Arc (PMF Framework)
- Bessemer — Rule of X
- ICONIQ — SaaS IPO Metrics
