# Lattice

Codebase intelligence engine and multi-session Claude Code orchestrator.

## What it does

**Mapper** -- Static analysis combined with an LLM agent fleet to produce living documentation of a codebase.

- Parses Python and TypeScript ASTs, builds dependency graphs, detects entry points and config wiring
- Discovers tests (pytest/jest), classifies them by type, and performs gap analysis ranked by centrality
- Dispatches parallel LLM agents via LangGraph in bottom-up waves, producing `_dir.md` shadow docs per directory
- Analyzes cross-cutting concerns: event flows, shared state, API contracts, plugin points
- Keeps docs current via a git post-commit hook that triggers incremental re-documentation
- Supports developer hint/correct/IDK mode for guiding and correcting output

**Orchestrator** -- Process and context management for running multiple Claude Code instances.

- Spawns, monitors, and tasks Claude Code as managed subprocesses
- Manages soul files and context windows with forced compaction at 50-60% utilization
- Push-to-talk voice interface (Whisper STT, intent classification, command routing)
- Pluggable MCP connector registry (Tavily, GitHub, Mattermost)
- Circuit breakers to prevent runaway loops
- Per-project isolation with canary injection testing

## Quick start

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/navindasg/lattice.git
cd lattice
uv sync
```

Parse a codebase and generate documentation:

```bash
lattice map:init --target /path/to/repo
lattice map:doc
lattice map:status
```

Start the orchestrator:

```bash
lattice orchestrator:start
```

Run the HTTP server:

```bash
lattice serve
```

### Key commands

| Command | Description |
|---|---|
| `lattice map:init` | Parse codebase, build dependency graph |
| `lattice map:doc` | Dispatch agent fleet for documentation |
| `lattice map:status` | Show mapping progress |
| `lattice map:gaps` | Show untested integration seams |
| `lattice map:hint` | Provide developer hints to guide mapping |
| `lattice map:cross` | Cross-cutting analysis |
| `lattice orchestrator:start` | Start orchestrator |
| `lattice orchestrator:voice` | Voice interface |
| `lattice serve` | FastAPI HTTP server |

## Architecture

```
src/lattice/
  adapters/       # Python + TypeScript AST parsing (ts-morph via Node subprocess)
  api/            # FastAPI HTTP surface and NDJSON stdio server
  cli/            # Click + Rich CLI commands, formatting, hooks
  cross_cutting/  # Event flow, shared state, API contract detection
  fleet/          # LangGraph wave dispatch, checkpointing, doc assembly
  graph/          # Dependency graph builder, entry points, config wiring
  llm/            # LangChain model factory, per-project config
  models/         # Shared Pydantic models (FileAnalysis, GraphNode, etc.)
  orchestrator/   # Process manager, context/soul files, voice, MCP connectors
  persistence/    # DuckDB checkpointer, FAISS vector store
  shadow/         # _dir.md schema, reader/writer, staleness detection
  testing/        # Test discovery, classification, coverage mapping
```

**Stack:** Python 3.12+, uv, LangGraph/LangChain, DuckDB, FAISS-cpu, FastAPI, Click + Rich, ts-morph.

## License

MIT
