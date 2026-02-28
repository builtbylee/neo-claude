# StartupLens v3 — Final Architecture Plan

## What this tool does

StartupLens scores pre-IPO tech startups listed on equity crowdfunding platforms (Crowdcube, Republic, Wefunder, StartEngine, Seedrs/Republic Europe, and others) using:

1. **Model B (primary):** Trained on 8,500+ US Reg CF filings from SEC EDGAR + UK crowdfunding outcomes from Companies House + published academic datasets — predicting which crowdfunding-stage companies survive, fail, or exit
2. **Model A (refinement):** Derived from 20+ years of US/UK tech S-1 filings — informing directional benchmarks by business model (what gross margin, growth rate, and capital efficiency look like for companies that went on to succeed)
3. **Claude text analysis (core scoring component):** LLM evaluation of pitch narrative quality, claims plausibility, competitive moat, and team strength — validated by research as the single most predictive feature
4. **Alternative data enrichment (15+ free signals):** Job postings, Google Trends, app store data, GitHub activity, government grants, FCA permissions, press coverage, and more

The user enters data from a crowdfunding listing, the tool auto-enriches with alternative data, applies a confidence-adjusted quantitative score from a sceptical baseline, and generates an AI-powered due diligence narrative.

---

## System Diagram

```
+-------------------------------------------------------------------------+
|                          DATA PIPELINES (Python)                        |
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
|                +---------------------+                                  |
|                |  MODEL B ENGINE     |                                  |
|                |  XGBoost/LightGBM   |                                  |
|                |  + rubric weights   |                                  |
|                +---------+-----------+                                  |
|                          |                                              |
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
|  |  Startup Input    |  |  API Routes         |  |  Results          | |
|  |  Form             |  |                     |  |  Dashboard        | |
|  |                   |  |  +----------------+ |  |                   | |
|  |  Manual fields    +->|  | Alt Data       | +->|  Score +/- range  | |
|  |  + pitch text     |  |  | Enrichment     | |  |  Category scores  | |
|  |  paste            |  |  | (15+ APIs)     | |  |  Confidence level | |
|  |                   |  |  +-------+--------+ |  |  Risk flags       | |
|  +-------------------+  |         |           |  |  AI narrative     | |
|                         |  +------v---------+ |  |  Alt data signals | |
|  +-------------------+  |  | Rubric         | |  |  Missing data     | |
|  |  Portfolio        |  |  | Scoring        | |  |                   | |
|  |  Tracker          |  |  | (XGBoost)      | |  +-------------------+ |
|  |                   |  |  +------+---------+ |                        |
|  |  Investments      |  |         |           |                        |
|  |  Outcomes         |  |  +------v---------+ |                        |
|  |  Rubric accuracy  |  |  | Claude API     | |                        |
|  |                   |  |  |                 | |                        |
|  +-------------------+  |  | Text scoring   | |                        |
|                         |  | + qualitative  | |                        |
|                         |  | narrative      | |                        |
|                         |  +----------------+ |                        |
|                         +---------------------+                        |
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

### Phase 1 — Model B: Crowdfunding Outcome Model (~2.5 weeks)

This is the highest-value work. Delivers a functional scoring engine before any S-1 analysis begins.

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

#### 1b. Feature Engineering

For each company in the training set, construct these features (where available):

**Campaign features:**
- Funding target amount
- Amount actually raised
- Overfunding ratio (raised / target)
- Equity offered %
- Pre-money valuation
- Investor count
- Funding velocity (days to reach target)
- EIS/SEIS eligible (boolean)
- Platform (Crowdcube, Seedrs, Republic, Wefunder, StartEngine)

**Company features:**
- Company age at raise (months since founding)
- Employee count
- Revenue at raise (and whether pre-revenue)
- Revenue growth rate (if 2+ years of data)
- Total prior funding raised
- Had prior VC/angel backing (boolean)
- Sector / industry category
- Revenue model type (SaaS, transactional, marketplace, hardware, consumer, other)
- Country (US, UK, EU)

**Team features:**
- Founder count
- Founder domain experience (years)
- Prior founder exits (boolean)
- Accelerator alumni (boolean + which accelerator)

**Derived features (from Companies House / SEC):**
- Total assets at raise
- Total debt at raise
- Debt-to-asset ratio
- Cash position
- Burn rate estimate (if derivable from sequential filings)

**Outcome variable:**
- Status as of verification date: **trading** (3+ years), **exited** (acquisition/IPO), **failed** (dissolved/liquidation/administration)
- Time to outcome (months from raise to failure/exit)

#### 1c. Model Training

**Method: XGBoost (gradient boosted trees)**

Why XGBoost over logistic regression:
- Captures non-linear thresholds (success drops sharply above certain raise targets)
- Captures Goldilocks effects (2-3 founders optimal, too many or too few is worse)
- Captures interaction effects (founder experience x competitive advantage)
- Elitzur et al. (2024) on 108,223 campaigns: boosted trees consistently outperformed logistic regression

**Validation: Time-based split**
- Train: campaigns from 2016-2020
- Validate: campaigns from 2021-2022
- Test: campaigns from 2023-2025
- This prevents data leakage and properly simulates real-world use

**Class imbalance handling:**
- Failure rate is ~7-21% depending on dataset. Use cost-sensitive learning (weight the loss function by class frequency) rather than SMOTE — more stable for small samples.

**Output:**
- Feature importance rankings (SHAP values)
- Rubric v1 weights derived from feature importance
- Validation AUC, precision, recall, F1
- Expected realistic AUC: 0.67-0.75 (based on published benchmarks)

**Log:** "Rubric v1, generated [date], trained on [n] US Form C offerings + [n] UK crowdfunding outcomes, time-based validation 2016-2020 / 2021-2022, validation AUC: [x]"

#### 1d. Rubric Generation from Model + Academic Research

The XGBoost model produces feature importance weights. These are combined with academic findings to produce the final rubric. Where the model's findings align with published research, confidence is high. Where they diverge, flag for investigation.

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

---

### Phase 2 — Web App + Claude Integration + Alternative Data (~2 weeks)

Build simultaneously with Phase 1 data collection. The web app should be functional with rubric v1 as soon as Model B training completes.

#### 2a. Claude Text Analysis (Core Scoring Component)

Research finding: the way a startup describes itself is the **single most important predictive feature**, above all structured variables (Maarouf & Feuerriegel 2024, SHAP analysis). This is not a "nice to have" narrative layer — it's a core quantitative input.

**Implementation: Two-stage Claude call**

**Stage 1: Structured Text Scoring (Claude Sonnet 4.5)**

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
  "text_quality_score": <int>,  // weighted average
  "red_flags": [<string>, ...],
  "reasoning": "<2-3 sentences>"
}
```

The `text_quality_score` becomes a quantitative input to the rubric with its own weight (see scoring section).

**Stage 2: Qualitative Due Diligence Narrative (Claude Sonnet 4.5)**

```
You are writing a due diligence brief for a personal investor
evaluating a startup on {platform_name}.

COMPANY DATA:
{all structured inputs + alt data signals + text scores from Stage 1}

QUANTITATIVE SCORES:
{category breakdown}

SCORING CONTEXT:
Equity crowdfunding companies are empirically 8.5x more likely to
fail than matched non-crowdfunded companies (Walthoff-Borm 2018).
The base rate is against this investment. Your job is to identify
whether this specific company is an exception to that base rate.

Write these sections:

1. MOAT ASSESSMENT (2-3 sentences): What type of competitive
   advantage exists? (network effects, switching costs, regulatory,
   brand, IP, none). Be specific about WHY it's defensible or not.

2. MARKET TIMING (2-3 sentences): Is the market ready? Too early,
   right time, or too late? What external forces support or
   undermine timing?

3. TEAM RISK (2-3 sentences): What concerns you about this team's
   ability to execute? What's genuinely strong?

4. BIGGEST RISK (1-2 sentences): The single most likely failure
   mode for this company.

5. BULL CASE (1-2 sentences): The specific scenario where this
   returns 10x+. Be concrete.

6. BASE RATE OVERRIDE: Does this company have characteristics that
   justify overriding the sceptical base rate? (yes/no + 1 sentence why)

7. QUALITATIVE MODIFIER: Integer from -15 to +15. How much should
   the quantitative score be adjusted based on factors the rubric
   cannot capture?

Be direct. Do not hedge. If information is insufficient, say so
explicitly rather than giving a middling assessment.
```

**Cost per evaluation:** ~$0.03-0.07 (two Sonnet calls).

#### 2b. Alternative Data Enrichment

When the user submits a company name + website URL, the API routes automatically fetch signals from free sources. Each signal is displayed alongside the manual input and factored into scoring where applicable.

**Tier 1 — Auto-fetched for every evaluation:**

| Signal | API | Auth | Rate limit | What we extract |
|--------|-----|------|-----------|----------------|
| Search interest trend (12mo) | Google Trends (pytrends) | None | ~1,400/session | Relative interest 0-100, direction (rising/falling/flat), % change |
| Website rank + trend | SimilarWeb DigitalRank | Free API key | 100/month | Global rank, rank change direction |
| Press coverage volume + tone | GDELT Doc 2.0 API | None | Unlimited | Article count (30/90 day), average tone score (-100 to +100) |
| Job posting count + roles | Adzuna API | Free API key | 1,000/day | Open role count, role categories (eng/sales/ops), seniority |
| Company status + charges | Companies House API | Free API key | 600/5min | Active/dissolved, outstanding charges, filing timeliness |
| Director disqualifications | Companies House API | Free API key | (same) | Binary red flag check on all directors |

**Tier 2 — Auto-fetched when applicable (conditional on sector/product type):**

| Signal | Condition | API | What we extract |
|--------|-----------|-----|----------------|
| App store rating + reviews | User indicates mobile app | google-play-scraper / app-store-scraper (npm) | Rating, review count, review growth, sentiment |
| GitHub stars + commit velocity | User provides GitHub URL | GitHub REST API (5K/hr) | Stars, star velocity, contributor count, commit frequency, issue close rate |
| npm/PyPI downloads | User indicates dev tool | npmjs.org API / pypistats.org | Weekly download count, growth trend |
| Trustpilot score | Consumer-facing company | Trustpilot API | TrustScore, review count, response rate |
| ProductHunt launch | User indicates PH launched | ProductHunt GraphQL API | Upvotes, comments, featured status |
| FCA permissions | Fintech company | FCA Register API (free) | Permission types, authorisation status, regulatory history |
| Reddit mentions | Any | PRAW (Reddit API) | Post count (30/90 day), subreddit presence, sentiment |
| Stack Overflow questions | Dev tool | Stack Exchange API (10K/day) | Question count, growth, accepted answer rate |

**Tier 3 — Manual enrichment (user checks and enters):**

| Signal | Where to check | Form field |
|--------|---------------|------------|
| LinkedIn employee count (current) | LinkedIn company page | Number input |
| LinkedIn employee count (6mo ago) | LinkedIn company page (or estimate) | Number input |
| Glassdoor rating | glassdoor.co.uk | Number input (1-5) |
| Innovate UK grants | UKRI funded projects CSV | Checkbox + amount |
| Government contracts won | Contracts Finder | Checkbox + count |

**Tier 3 signals improve confidence level but are not required.**

#### 2c. Scoring Engine

**Rubric Structure (7 categories):**

```
OVERALL SCORE (0-100) +/- confidence range

  Sceptical baseline: ECF companies are 8.5x more likely to fail
  than matched non-ECF firms. Score starts at 35 (below average)
  and adjusts upward only when evidence supports it.

+-- TEXT & NARRATIVE QUALITY (20%)
|   +-- Clarity score (Claude Stage 1)
|   +-- Claims plausibility (Claude Stage 1)
|   +-- Problem specificity (Claude Stage 1)
|   +-- Differentiation depth (Claude Stage 1)
|   +-- Founder domain signal (Claude Stage 1)
|   +-- Risk honesty (Claude Stage 1)
|   +-- Business model clarity (Claude Stage 1)
|
+-- TRACTION & GROWTH (20%)
|   +-- Revenue exists (yes/no -- binary gate)
|   +-- Revenue growth rate (YoY or MoM)
|   +-- Customer/user count + growth
|   +-- Google Trends direction (auto)
|   +-- Web traffic rank + trend (auto)
|   +-- App store rating + reviews (auto, if applicable)
|   +-- GitHub star velocity (auto, if applicable)
|   +-- npm/PyPI download growth (auto, if applicable)
|
+-- FINANCIAL HEALTH (15%)
|   +-- Gross margin %
|   +-- Burn multiple (net burn / net new revenue)
|   +-- Cash runway (months)
|   +-- Capital efficiency (revenue / total raised)
|   +-- Debt-to-asset ratio
|   +-- Revenue model type (recurring > transactional > project)
|
+-- TEAM (15%)
|   +-- Founder count (2-3 optimal; solo penalised)
|   +-- Relevant domain experience (years)
|   +-- Prior startup exits (boolean -- strong positive)
|   +-- Accelerator alumni (boolean + tier)
|   +-- LinkedIn headcount trend (manual)
|   +-- Glassdoor rating (manual, if available)
|   +-- Active hiring signal (Adzuna job count, auto)
|
+-- INVESTMENT SIGNAL (15%)
|   +-- Qualified institutional co-investor (near-binary -- see below)
|   +-- Prior VC/angel backing (strong positive)
|   +-- EIS/SEIS eligibility (positive)
|   +-- Overfunding ratio (moderate positive)
|   +-- Funding velocity (moderate positive)
|   +-- Investor count (NEGATIVE above threshold -- dispersed
|   |   ownership weakens monitoring)
|   +-- Round progression (clean step-ups vs bridges)
|
+-- MARKET (10%)
|   +-- TAM plausibility (Claude assessment)
|   +-- Market timing / tailwinds (Claude assessment)
|   +-- Competitive density
|   +-- Press coverage trend (GDELT, auto)
|   +-- Innovate UK / EU grants received (manual)
|   +-- Government contracts won (manual)
|
+-- DEAL TERMS (5%)
    +-- Pre-money valuation vs revenue multiple benchmarks
    +-- Equity offered % (>25% is negative)
    +-- Share class protections
    +-- Platform nominee structure risk
```

**Special rules:**

1. **Qualified institutional co-investor override:** If a qualified institutional investor (named VC fund or established angel syndicate) has co-invested, apply a +15 point bonus to the overall score. Academic basis: zero failures in this group (Signori & Vismara 2018).

2. **Pre-revenue penalty:** If the company has no revenue, cap the Traction & Growth category at 30/100 regardless of other signals. Pre-revenue companies have a 7.1% failure rate vs 2.0% for $10M+ revenue (KingsCrowd data).

3. **Sceptical baseline:** The score starts at 35, not 50. Evidence must push it upward. This reflects the empirical 8.5x failure rate of ECF companies vs matched non-ECF firms.

4. **Qualitative modifier:** Claude's Stage 2 assessment produces a modifier of -15 to +15, applied after the quantitative score. This captures factors the rubric cannot (market timing nuance, team chemistry signals, competitive dynamics visible only in narrative).

**Confidence calculation:**

| Data completeness | Confidence | Score range |
|-------------------|-----------|-------------|
| >80% of fields + pitch text + 3+ auto signals | High | +/-8 points |
| 50-80% of fields + pitch text | Moderate | +/-15 points |
| 50-80% of fields, no pitch text | Moderate-Low | +/-20 points |
| <50% of fields | Low | +/-25 points |

Additionally, if the company's feature profile is a statistical outlier (far from any training data cluster), confidence is downgraded one level regardless of completeness, with an explicit note: *"This company's profile is unusual -- few historical comparables exist in the training data."*

---

### Phase 3 — Model A: S-1 / IPO Historical Analysis (~2.5 weeks)

Runs after Model B is functional. Refines rubric weights with hard data on what business model benchmarks predict long-term success.

#### 3a. SEC EDGAR S-1 Pipeline

- Download quarterly `form.idx` bulk files (2000-2025)
- Filter to tech SIC codes: 7370-7379, 3571-3577, 3661-3674, 8742
- Estimated yield: ~3,000-4,500 original S-1 filings
- For each filing:
  - Download HTML document
  - Extract financial tables using Claude Haiku 4.5 (~$0.01/filing, ~$40 total)
  - Extract from same filing: funding history (capitalization section), founding date (from company history), employee count
  - Spot-check 50 filings manually. If accuracy >95%, proceed.
- Extracted metrics per company: revenue (2-3 years), revenue growth YoY, gross margin, operating expenses, net income/loss, cash position, total funding raised, employee count, time from founding to IPO
- Libraries: `edgartools`, `sec-parser`, `beautifulsoup4`, `pandas`

#### 3b. UK IPO Pipeline

- Cross-reference Companies House tech SIC codes with LSE/AIM listing records to identify UK tech companies that IPO'd
- Fetch pre-IPO iXBRL filings where available (large companies only -- `ixbrlparse`)
- For small companies with balance-sheet-only filings: track equity and asset changes as growth proxies
- Download ~100 most relevant AIM Admission Documents (PDF) -> extract financials with Claude Sonnet 4.5 (~$10)
- Accept smaller, noisier UK dataset vs US

#### 3c. Stock Price Data (Post-IPO Performance)

- `yfinance` for US tickers, `yfinance` `.L` suffix + Twelve Data for UK/AIM
- For each company calculate:
  - **Benchmark-relative returns (alpha):**
    - US benchmark: S&P 500 Information Technology Index
    - UK benchmark: FTSE AIM All-Share
    - Periods: 1yr, 3yr, 5yr
  - Max drawdown within first 3 years
  - **Macro regime label:** bull (market up >15% prior 12mo), neutral (+/-15%), bear (down >15%)

#### 3d. Analysis

- **Time-based validation:** Train on 2000-2017, validate on 2018-2020, test on 2021-2025
- Spearman rank correlation: each pre-IPO metric vs benchmark-relative 3yr alpha
- Random forest feature importance for non-linear relationships
- Control for macro regime -- run analysis within each regime separately
- Output: **business-model-specific benchmarks:**
  - "SaaS companies with gross margin below 60% at IPO underperformed peers by X% over 3 years"
  - "Marketplace companies with revenue growth below 40% YoY at IPO underperformed by Y%"
  - "Hardware companies with burn multiple above 3x at IPO had Z% higher failure rate"

#### 3e. Rubric Refinement -> v2

- Model A findings refine the **scoring thresholds** within each category (what "good" looks like for SaaS vs marketplace vs consumer vs hardware)
- Category weights stay anchored to Model B (crowdfunding-relevant)
- Log: "Rubric v2, generated [date], Model B: [n] crowdfunding outcomes, Model A: [n] S-1 companies, validation AUC: [x], business-model benchmarks for [n] sectors"

---

### Phase 4 — Portfolio Tracker + Feedback Loop (~3-4 days)

#### Investment Tracker
- When you invest based on an evaluation, log: company, date, amount, evaluation score, rubric version
- Link to the saved evaluation for reference

#### Outcome Monitoring
- Quarterly check: is the company still trading? Companies House status for UK, SEC EDGAR for US.
- Track: follow-on raises, revenue updates (from platform updates or Companies House filings), team changes
- Record: current status (active / raised again / revenue growing / stagnant / written off / exited), outcome multiple (when known)

#### Feedback Loop
- After 20+ tracked investments with known outcomes, compare predicted scores against actual results
- Identify systematic biases: is the rubric consistently wrong on a specific factor?
- Manual rubric adjustment based on personal data
- Automated alert: if the average score of failed investments overlaps with the average score of successful ones, the rubric has lost discriminatory power and needs retraining

---

## Database Schema

```sql
-- ============================================================
-- TRAINING DATA
-- ============================================================

companies (
  id uuid PRIMARY KEY,
  name text NOT NULL,
  ticker text,
  country text NOT NULL,                  -- US, GB, DE, FR, etc.
  sector text,
  sic_code text,
  founding_date date,
  ipo_date date,
  ipo_exchange text,                      -- NYSE, NASDAQ, AIM, LSE Main
  source text NOT NULL,                   -- edgar_s1, edgar_form_c, companies_house,
                                          --   crowdfunding_dataset, manual
  source_id text,                         -- CIK, company number, or dataset row
  current_status text,                    -- active, dissolved, acquired, ipo'd,
                                          --   liquidation, administration
  status_verified_date date,
  created_at timestamptz DEFAULT now()
)

financial_data (
  id uuid PRIMARY KEY,
  company_id uuid REFERENCES companies,
  period_end_date date NOT NULL,
  period_type text,                       -- annual, quarterly
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
  source_filing text                      -- S-1, 10-K, form_c, iXBRL,
                                          --   admission_doc, manual
)

funding_rounds (
  id uuid PRIMARY KEY,
  company_id uuid REFERENCES companies,
  round_date date,
  round_type text,                        -- seed, series_a, crowdfunding, grant, etc.
  amount_raised numeric,
  pre_money_valuation numeric,
  post_money_valuation numeric,
  lead_investor text,
  qualified_institutional boolean,        -- institutional co-investor present?
  platform text,                          -- crowdcube, seedrs, republic, wefunder, etc.
  overfunding_ratio numeric,
  investor_count integer,
  funding_velocity_days integer,
  eis_seis_eligible boolean,
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
  macro_regime text,                      -- bull, neutral, bear
  alpha_1yr numeric,
  alpha_3yr numeric,
  alpha_5yr numeric,
  return_1yr numeric,
  return_3yr numeric,
  return_5yr numeric,
  max_drawdown_3yr numeric,
  success_tier smallint                   -- 1-5
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
  -- Outcome
  outcome text NOT NULL,                  -- trading, exited, failed, acquired
  outcome_detail text,                    -- dissolved, liquidation, ipo, m&a, etc.
  outcome_date date,
  years_to_outcome numeric,
  data_source text                        -- form_c, companies_house, academic, manual
)

-- ============================================================
-- RUBRIC & SCORING
-- ============================================================

rubric_versions (
  id serial PRIMARY KEY,
  version text NOT NULL,                  -- v1, v2, etc.
  generated_at timestamptz NOT NULL,
  model_b_summary text,                   -- "4,200 Form C + 1,800 UK outcomes"
  model_a_summary text,                   -- "3,100 S-1 companies" (null for v1)
  validation_method text,                 -- "time-based 2016-2020 / 2021-2022"
  validation_auc numeric,
  feature_importance jsonb,               -- SHAP values per feature
  category_weights jsonb,                 -- { text: 0.20, traction: 0.20, ... }
  scoring_thresholds jsonb,               -- business-model-specific thresholds
  academic_overrides jsonb,               -- documented overrides from research
  notes text
)

evaluations (
  id uuid PRIMARY KEY,
  rubric_version_id integer REFERENCES rubric_versions,
  company_name text NOT NULL,
  platform text,
  listing_url text,
  -- Input data
  manual_inputs jsonb NOT NULL,           -- everything from the form
  pitch_text text,                        -- pasted pitch description
  alt_data jsonb,                         -- auto-fetched signals
  alt_data_fetched_at timestamptz,
  -- Claude analysis
  text_scores jsonb,                      -- Stage 1 structured scores
  qualitative_narrative text,             -- Stage 2 narrative
  qualitative_modifier integer,           -- -15 to +15
  -- Scoring
  quantitative_score numeric NOT NULL,
  confidence_lower numeric NOT NULL,
  confidence_upper numeric NOT NULL,
  confidence_level text NOT NULL,         -- high, moderate, moderate_low, low
  category_scores jsonb NOT NULL,         -- per-category breakdown
  risk_flags jsonb,
  missing_data_fields jsonb,              -- what would improve confidence
  -- Metadata
  created_at timestamptz DEFAULT now(),
  notes text
)

-- ============================================================
-- PORTFOLIO TRACKER
-- ============================================================

investments (
  id uuid PRIMARY KEY,
  evaluation_id uuid REFERENCES evaluations,
  company_name text NOT NULL,
  platform text,
  invested_date date NOT NULL,
  amount_invested numeric NOT NULL,
  evaluation_score numeric,
  rubric_version_id integer REFERENCES rubric_versions,
  -- Outcome tracking
  current_status text DEFAULT 'active',   -- active, raised_again, growing,
                                          --   stagnant, written_off, exited
  last_status_check date,
  outcome_date date,
  outcome_multiple numeric,               -- e.g., 3.2x (null until known)
  follow_on_raises jsonb,                 -- [{date, amount, source}, ...]
  outcome_notes text,
  created_at timestamptz DEFAULT now()
)
```

---

## Input Form

The form is designed around what is actually visible on a Crowdcube / equity crowdfunding listing page. Required fields are marked with `*`. Pitch text is required because research shows it is the single strongest predictor of startup quality.

**Sections:**

1. **Company** — name*, website URL*, platform, listing URL, sector*, country*, founded date*, employees, Companies House number (UK only, enables auto status/charges/director checks)
2. **Financials** — revenue (last 12mo), revenue (prior year), gross margin %, monthly burn rate, revenue model
3. **Deal Terms** — total raised to date, current round target, amount raised so far, pre-money valuation, equity offered %, EIS/SEIS eligibility, investor count
4. **Investment Signals** — prior VC/angel backing*, qualified institutional co-investor, notable investors, accelerator attendance
5. **Traction** — customers/users, growth rate + period, key traction points, net retention % (SaaS), GitHub URL (if open source), app name (if mobile)
6. **Team** — number of founders*, domain experience (years), prior exits, LinkedIn headcount (current + 6mo ago), Glassdoor rating
7. **Market** — TAM claim, direct competitors (count), differentiator, Innovate UK grants, government contracts
8. **Pitch Text*** — large text area for pasting the full pitch description from the listing
9. **Your Notes** — optional free text for personal impressions, red/green flags

---

## Results Dashboard

The results page displays:

1. **Overall score** with confidence range (e.g., "68 +/-14, Moderate confidence") and base rate context explaining that ECF companies are 8.5x more likely to fail
2. **Category breakdown** — bar chart showing score per category with weights
3. **Alternative data signals** — auto-fetched results (Google Trends, press coverage, job postings, company status, etc.)
4. **Pitch text analysis** — Claude Stage 1 scores per dimension with red flags
5. **Risk flags** — specific concerns identified from data and AI analysis
6. **AI due diligence brief** — Claude Stage 2 narrative covering moat, timing, team, biggest risk, bull case, and base rate override assessment
7. **Missing data** — fields that would improve confidence if provided
8. **Actions** — Save, Add to Portfolio, Export PDF, New Evaluation

---

## Tech Stack

| Component | Technology | Hosting | Cost |
|-----------|-----------|---------|------|
| Frontend + API routes | Next.js 15 (App Router, TypeScript) | Vercel free tier | 0/mo |
| Database | PostgreSQL | Supabase free tier (500MB) | 0/mo |
| UI | shadcn/ui + Tailwind CSS + Recharts | Bundled | 0 |
| ML model | XGBoost (Python, exported as JSON) | Loaded in API route or Python microservice | 0 |
| Data pipeline | Python 3.12 (pandas, httpx, xgboost, shap) | Local / GitHub Actions | 0/mo |
| S-1 extraction | edgartools + Claude Haiku 4.5 | Anthropic API | ~40 one-time |
| UK filing parsing | ixbrlparse + Claude Sonnet 4.5 | Anthropic API | ~10 one-time |
| Evaluation AI | Claude Sonnet 4.5 (2 calls/eval) | Anthropic API | ~3-5/mo |
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
| **Total ongoing** | | | **~3-5/mo** |
| **One-time data build** | | | **~50** |

---

## Out of Scope (v1)

- **Historical comparables matching** — deferred until Model A is validated and the stage gap is addressed with a proper nearest-neighbour approach
- **Automated platform scraping** — all platforms prohibit it; manual input + SEC EDGAR for structured US data
- **Paid data sources** (Beauhurst, PitchBook, KingsCrowd Edge) — not justified at personal scale
- **Pipeline orchestration** (Dagster/Prefect) — scripts kept modular, not needed yet
- **Multi-user auth** — personal tool; add later if opened to others
- **Real-time data feeds** — batch pipeline, refreshed quarterly
- **Automated model retraining** — sample size too small; manual review after 20+ tracked investments
- **Twitter/X monitoring** — $200/month minimum, not worth it for a personal tool
- **CCJ checks** — 6-10 GBP/search, do manually for companies you're seriously considering

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
