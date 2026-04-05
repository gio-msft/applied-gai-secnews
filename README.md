# Generative AI Security News

Automated arXiv-to-newsletter pipeline for AI/LLM security research. Searches arXiv, downloads PDFs, summarizes via Azure OpenAI, classifies relevance, matches papers to internal research projects, and produces Markdown + Outlook-draft `.eml` output.

This project powers the weekly "Last Week in GAI Security Research" newsletter.

## Setup

1. **Python dependencies**
   ```bash
   pip install -r requirements.txt
   ```

2. **Azure OpenAI credentials** — create a `.env` file:
   ```
   AZURE_OPENAI_ENDPOINT=https://your-endpoint.openai.azure.com/
   AZURE_OPENAI_API_KEY=your-key
   AZURE_OPENAI_SUMMARY_MODEL_NAME=your-deployment-name
   ```

3. **Research projects** (optional) — edit `projects.json` to define internal research projects for paper-to-project matching:
   ```json
   [
     {"id": "my-project", "description": "Short description of the project."}
   ]
   ```

## Usage

```bash
# Full pipeline: search → download → summarize → classify → share
python deepthought.py

# Re-run searches ignoring the 1-hour cache
python deepthought.py --force-search

# Re-summarize all papers in the 7-day window
python deepthought.py --resummarize

# Re-classify project relevance (after editing projects.json)
python deepthought.py --reclassify-projects

# Only regenerate .md/.eml output from existing data
python deepthought.py --share-only

# Include all papers regardless of relevance
python deepthought.py --include-general

# Skip the interactive review of borderline papers
python deepthought.py --no-interactive

# Run the full pipeline then build the interactive graph visualization
python deepthought.py --build-viz
```

### Graph Visualization

```bash
# Build the interactive paper graph (fetches citations + computes embeddings)
python build_viz.py

# Use cached citations only (skip Semantic Scholar queries)
python build_viz.py --skip-citations

# Skip embedding computation (no Azure OpenAI calls for embeddings)
python build_viz.py --skip-citations --skip-embeddings
```

The graph is written to `docs/data/graph.json` and served by the static site in `docs/`. Embeddings are cached in `embeddings_cache.json` so subsequent runs only embed new papers.

## Pipeline

1. **Search** — runs 24 arXiv API queries targeting `cs.CR`, `cs.CL`, `cs.AI`, `cs.LG` with LLM-security terms (prompt injection, jailbreak, backdoor, etc.). Results are cached per-query in `search_state.json`.
2. **Dedup & prune** — new papers are inserted into `papers.json`; duplicates, old papers, and already-downloaded PDFs are filtered out.
3. **Download** — bulk-fetches PDFs into `papers/` via async HTTP.
4. **Summarize** — reads each PDF and calls Azure OpenAI (JSON mode) to extract 3 findings, a one-liner, emoji, tag (`security`/`cyber`/`general`), and author affiliations.
5. **Classify relevance** — a second LLM call determines if each paper is relevant to the newsletter. Papers tagged `general` are auto-excluded.
6. **Classify project relevance** — if `projects.json` exists, a third LLM call matches relevant papers to internal research projects.
7. **Interactive review** — presents borderline papers (security/cyber-tagged but LLM-marked irrelevant) for manual inclusion.
8. **Share** — generates `summaries/YYYY-MM-DD.md` and `summaries/YYYY-MM-DD.eml` (opens as an unsent Outlook draft).

## Output

Each paper entry in the newsletter includes: emoji, title with source link, tag, authors, affiliations, a one-liner summary, 3 bullet-point findings, and matched research projects (if any).

## Testing

```bash
# Unit tests (integration tests excluded by default)
python -m pytest tests/ -v

# Integration tests (requires Azure OpenAI credentials)
python -m pytest tests/ -v -m integration
```

## Project Structure

```
deepthought.py          # Entry point, config, prompts, CLI
build_viz.py            # Graph visualization builder (citations, embeddings, layout)
projects.json           # Research project definitions
papers.json             # Paper database (auto-generated)
search_state.json       # Search cache (auto-generated)
embeddings_cache.json   # Paper embedding cache (auto-generated)
citations_cache.json    # Semantic Scholar citation cache (auto-generated)
papers/                 # Downloaded PDFs
summaries/              # Generated .md and .eml output
docs/                   # Interactive graph visualization (static site)
secnews/
  utils_search.py       # arXiv search + caching
  utils_papers.py       # PDF download + text extraction
  utils_summary.py      # LLM summarization + classification
  utils_db.py           # PaperDB (JSON-file database)
  utils_comms.py        # Output formatting + file generation
  utils_citations.py    # Semantic Scholar citation fetcher + cache
tests/                  # Unit + integration tests
ai/
  PROJECT_BRIEF.md      # Detailed architecture reference
```
