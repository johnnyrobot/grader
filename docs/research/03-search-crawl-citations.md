# Gap report: citation-verification search/crawl stack (opus agent, verified Jul 2026)

## crawl4ai
- v0.9.1 (2026-07-08), Apache 2.0, async Python (AsyncWebCrawler), Playwright under hood, clean markdown (BM25 PruningContentFilter), CSS/XPath LLM-free extraction, no keys, fully local.
- CAUTION: v0.8.7/0.9.0 fixed critical Docker-server vulns (RCE, SSRF, auth bypass, hardcoded JWT). → Use as Python LIBRARY only, never its Docker/HTTP server.

## Playwright direct
- chromium ~281 MB, headless shell smaller via --only-shell; runtime RAM peak ~706 MB headless / 1094 MB headed.
- Bundle headless shell vs lazy first-run install. Third option: macOS WKWebView = 0 MB bundle cost, but write own extraction.

## Firecrawl self-hosted — RULED OUT
- AGPL-3.0 core; Fire-engine (anti-bot) NOT open source; self-host lacks agent mode/proxy rotation/dashboard.
- Needs API server + worker + Redis + Postgres + Playwright microservice (docker-compose). ~$90-340/mo.
- AGPL = shipping it inside Grader would force AGPL on Grader. Not shippable in no-Docker consumer app.

## Search layer (2026 landscape)
- Bing Search API DEAD (retired 2025-08-11). Brave free tier GONE (Feb 2026; ~$5/1k, attribution-gated $5/mo credit, uncapped billing).
- SearXNG bundled: no Docker strictly required but wants uWSGI+Redis+reverse proxy AND gets bot-classified/rate-limited by engines → support nightmare; don't bundle. Remote self-host = you own the server + blocking problem.
- ddgs (ex duckduckgo-search): pure Python, keyless, May 2026 release; bot-detects before 30 req/min from one IP BUT desktop app = few queries/essay from residential IP → viable free default with backoff+cache.
- Kagi $15-25/1k — too expensive. Tavily 1k credits/mo free; Exa 1k/mo; Serper 2.5k free then $0.30-1/1k (cheapest paid). Google CSE 100/day.
- REC: ddgs keyless default + optional user-supplied key (Serper/Brave/Tavily) upgrade path.

## Citation verification — KEY ARCHITECTURAL INSIGHT
For academic refs, barely use web search at all — metadata APIs answer "does this exist / does biblio data match":
- Tier 1 (free, keyless): doi.org content negotiation (Accept: application/vnd.citationstyles.csl+json → citeproc-JSON, all DOI agencies, one call verifies existence + all fields). Crossref REST /works?query.bibliographic= (SBMV reference matching; no key; public pool 5 req/interval / polite pool 10 req + 3 concurrent via ?mailto= — MUST be app contact email, never teacher/student). Semantic Scholar Graph API keyless 100 req/5min.
- Tier 2: OpenAlex — changed Feb 2026 to credit-based w/ API key expected ($0.10/day keyless, $1/day w/ free key; DOI singleton lookups free) → enrichment only.
- Tier 3 AVOID: Google Scholar — no official API, blocks after a few queries, needs proxies/CAPTCHA solvers.

## Recommended stack (4 layers)
0. RESOLVE: doi.org → Crossref query.bibliographic → S2/OpenAlex enrich. Kills ~90% of academic refs, no crawl/search.
1. SEARCH (non-academic only): ddgs default; user-key Serper/Brave/Tavily upgrade.
2. FETCH/RENDER: httpx + trafilatura/readability fast path; crawl4ai → Playwright chromium --only-shell for JS pages (bundle or lazy-install).
3. CLAIM CHECK: local LLM compares student claim vs extracted markdown — never leaves machine.

Why: inverts naive search→crawl→LLM; ~10x cost/latency/failure reduction; zero AGPL; PII boundary holds (outbound = only cited-source data: DOI/biblio string/URL).

Tradeoffs: ddgs intermittent rate limits (backoff/cache/key path); Chromium +~280MB or WKWebView alternative; join Crossref polite pool day one; OpenAlex = enrichment not dependency.
