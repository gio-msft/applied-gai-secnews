# PROJECT BRIEF — GAI Security News Pipeline

Automated arXiv-to-newsletter pipeline for AI/LLM security research. Searches arXiv, downloads PDFs, summarizes via Azure OpenAI, classifies relevance, matches to internal research projects, and produces Markdown + Outlook-draft `.eml` output.

## Architecture

```
arXiv API ──▶ execute_searches() ──▶ assemble_feeds() ──▶ prune_feeds()
                 (cached in               (dedup,             (date +
              search_state.json)        insert to DB)      disk filter)
                                             │
                                             ▼
                                    download_papers()  ──▶  papers/*.pdf
                                             │
                                             ▼
                                    summarize_records() ──▶ Azure OpenAI
                                             │               (JSON mode)
                                             │          (includes interest_score)
                                             ▼
                                   classify_relevance() ──▶ relevant: T/F
                                             │
                                             ▼
                               classify_project_relevance() ──▶ projects: [...]
                                             │
                                             ▼
                                      share_results()
                                       ├── sort by interest_score desc
                                       ├── summaries/YYYY-MM-DD.md
                                       └── summaries/YYYY-MM-DD.eml
```

## Key Modules

| File | Role |
|---|---|
| [`deepthought.py`](../deepthought.py) | Entry point & orchestrator. All config, prompts, search queries, and CLI flags live here. |
| [`secnews/utils_search.py`](../secnews/utils_search.py) | arXiv API queries with pagination, rate-limiting, and per-query cache (`search_state.json`, 1h TTL). |
| [`secnews/utils_papers.py`](../secnews/utils_papers.py) | Async bulk PDF download (`requests-futures`), PDF text extraction (`pypdf`). |
| [`secnews/utils_summary.py`](../secnews/utils_summary.py) | LLM summarization, relevance classification, and project-relevance classification. Includes anti-hallucination guard for affiliations. |
| [`secnews/utils_db.py`](../secnews/utils_db.py) | `PaperDB` — JSON-file-backed database (`papers.json`). In-memory list of dicts, flushed to disk on every write. |
| [`secnews/utils_comms.py`](../secnews/utils_comms.py) | Markdown + HTML formatting, `.md` and `.eml` file generation. |
| [`secnews/utils_citations.py`](../secnews/utils_citations.py) | Semantic Scholar citation fetcher with persistent JSON cache (`citations_cache.json`). Incremental: only queries S2 for papers not yet cached. |
| [`build_viz.py`](../build_viz.py) | Builds the interactive graph visualization — reads `papers.json`, fetches citations, computes author-overlap edges, computes per-paper embeddings (cached in `embeddings_cache.json`), pre-computes layout, and writes `docs/data/graph.json`. |
| [`projects.json`](../projects.json) | Research project definitions (`id` + `description`) for project-relevance matching. |

## Paper Record Schema

```json
{
  "id": "2602.15001v2",
  "url": "http://arxiv.org/pdf/2602.15001v2.pdf",
  "published": "2026-02-16T18:29:09Z",
  "title": "...",
  "authors": ["..."],
  "downloaded": true,
  "summarized": true,
  "points": ["...", "...", "..."],
  "one_liner": "...",
  "emoji": "🛡️",
  "tag": "security",            // security | cyber | general
  "affiliations": ["MIT"],
  "relevant": true,             // newsletter-relevant?
  "projects": ["backdoor-detection"],  // matched research projects
  "interest_score": 8            // 1-10 interest/quality rating from LLM
}
```

Fields are added progressively: search → download → summarize (includes `interest_score`) → classify → project-match.

Newsletter output is sorted by `interest_score` descending (ties broken by published date, newest first). Legacy records without a score are treated as `5`.

## Commands

```bash
# Activate conda environment
conda activate papers

# Normal run (search + download + summarize + classify + share)
python deepthought.py

# Re-run searches ignoring cache
python deepthought.py --force-search

# Re-summarize everything in the window
python deepthought.py --resummarize

# Re-classify project relevance after editing projects.json
python deepthought.py --reclassify-projects

# Only regenerate .md/.eml from existing data
python deepthought.py --share-only

# Include all papers (not just relevant ones) in output
python deepthought.py --include-general

# Skip interactive review of borderline papers
python deepthought.py --no-interactive

# Run unit tests (integration tests excluded by default)
python -m pytest tests/ -v

# Run integration tests (requires Azure OpenAI endpoint + Entra ID login)
python -m pytest tests/ -v -m integration

# Build the interactive graph visualization (uses cached citations)
python build_viz.py --skip-citations

# Build viz with fresh citation data from Semantic Scholar
python build_viz.py

# Build viz without embedding computation (no Azure OpenAI for embeddings)
python build_viz.py --skip-citations --skip-embeddings

# Build viz as part of the main pipeline run
python deepthought.py --build-viz

# Run Playwright E2E tests for the visualization
python -m pytest tests/ -v -m e2e
```

## Conventions

- **Config-in-code**: Prompts, search queries, and constants live in `deepthought.py`, not in external config files (exception: `projects.json`).
- **Error handling**: Per-record try/except in summarization and classification — never crash the batch. On classification error, default to `relevant=True` (fail-safe: don't drop papers). On project classification error, default to `[]`.
- **Rate limiting**: 3-second sleep between LLM calls, 0.5–1.5s random sleep between arXiv requests.
- **State resilience**: Search state saved after each query; DB flushed on every mutation.
- **Anti-hallucination**: Affiliations are validated against arXiv author metadata (≥50% last-name match required). Project IDs are validated against the known set.
- **Tests**: Mock the LLM via `MagicMock` chain: `classifier.chat.completions.create.return_value`. Keep one real PDF (`papers/2505.24201v1.pdf`) for PDF-reading tests. Integration tests are marked `@pytest.mark.integration`.

## Known Pitfalls

- **`PaperDB` is a flat JSON file** (~10k records). Every `insert()`/`update()` rewrites the entire file. Works fine at current scale but will not scale to 100k+ records.

## Interactive Graph Visualization

A static interactive graph of all summarized papers, hosted via GitHub Pages under `docs/`.

### Architecture

```
papers.json ──► build_viz.py ──► docs/data/graph.json
                    │
                    ├── reads papers.json (all summarized papers)
                    ├── fetches citations via Semantic Scholar batch API (cached in citations_cache.json)
                    ├── computes author-overlap edges (inverted author index)
                    ├── pre-computes ForceAtlas2 layout (networkx spring_layout)
                    └── outputs single graph.json with nodes + two edge arrays + positions

docs/
├── index.html          ← entry point (vanilla HTML, CDN deps)
├── css/style.css       ← light/dark themes with gradient accents
├── js/app.js           ← Sigma.js v3 + Graphology graph rendering
└── data/graph.json     ← generated by build_viz.py
```

### Features
- **Two edge layers**: Citations (directed, from Semantic Scholar) and Shared Authors (undirected, by name overlap). Users toggle between them.
- **Node encoding**: Size by `interest_score`, color by `tag` (security/cyber/general).
- **Click-to-inspect**: Clicking a node opens a card panel with title, authors, affiliations, arXiv link, score, summary, key findings, and project affinity.
- **Search**: Type-ahead search by paper title, zooms to matching node.
- **Light/dark theme**: Toggle with system preference detection; uses CSS custom properties and gradient accents.
- **GitHub Pages ready**: Static HTML/JS/CSS, `.nojekyll` in `docs/`, no build tools needed.

### Data Flow
- **`papers.json`** is read-only from the viz pipeline — never modified.
- **`citations_cache.json`** is the persistent S2 API cache, committed to the repo. Incremental: only new paper IDs are fetched on each run.
- **`docs/data/graph.json`** is the final output consumed by the frontend.

### Known Pitfalls (Viz)
- **Semantic Scholar rate limits**: Free tier allows 100 req/5 min. For ~6K papers batched at 500/req, this is ~12 requests — well within limits. If rate-limited, set `S2_API_KEY` env var.
- **Layout computation**: `networkx.spring_layout` with 6K+ nodes takes ~30–60s. Positions are pre-computed and baked into `graph.json` so the browser doesn't need to compute them.
- **graph.json size**: With 6K+ nodes and metadata, expect ~5–15 MB. Single file for simplicity; if too large, can split into lazy-loaded chunks.

## Known Pitfalls (continued)
- **`deepthought.py` instantiates `AzureOpenAI` at import time** (line ~40). Uses `DefaultAzureCredential` via `azure-identity` for Entra ID token-based auth (`azure_ad_token_provider`). The token is fetched lazily on first API call. Importing the module still requires `AZURE_OPENAI_ENDPOINT` in the environment — relevant for tests that import from it.
- **`reset_summarized()` must list all derived fields** to pop. When adding new fields to the record schema, update the tuple in `utils_db.py` (`points`, `one_liner`, `emoji`, `tag`, `affiliations`, `relevant`, `projects`, `interest_score`).
- **arXiv rate limits**: The API has undocumented rate limits. The pipeline sleeps between requests but can still get 503s under heavy load.
- **Search cache is time-based only** (`search_state.json`). If you change a query string, the old cache entry becomes stale automatically (different key), but the old results from the previous query remain in the DB.
