<p align="center">
  <img src="assets/baigong-logo.png" alt="Baigong logo" width="240">
</p>

<h1 align="center">Baigong (百工)</h1>

<p align="center"><strong>A traceable, auditable, and replay-oriented local foundation demo for AI agents, AI governance, and agent harnesses</strong></p>

<p align="center">
  <code>AI Agent</code> · <code>AI Governance</code> · <code>Agent Harness</code> ·
  <code>Traceable</code> · <code>Auditable</code> · <code>Local-first</code> ·
  <code>Python 3.12</code> · <code>Windows</code> · <code>Apache-2.0</code>
</p>

**Baigong** is a Data–Action–Data demonstration system for governing local AI data and execution.
Starting from a synthetic JSON sample, it runs data discovery, admission and lineage registration,
structural governance, SQLite identity anchoring, DuckDB analytical materialization, BGE-M3
embedding, Chroma storage, and return preparation.

This directory is a self-contained demo release for installation and integration testing.
It contains no real user data, production database, API key, or model weight. Baigong is not a
finished general-purpose agent product. It demonstrates how an agent can obtain verifiable data
origins, governed execution entry points, and reviewable evidence when using memory, retrieval,
or tools.

中文说明：[README.md](README.md)

## Positioning for AI agents, governance, and harnesses

An AI agent should not merely produce an answer. It should also make it possible to determine
where its inputs came from, which transformations occurred, which version executed them, and how
to return to a safe state after failure. Baigong places those requirements in one runnable chain:

| Area | Foundation provided by Baigong |
| --- | --- |
| AI agents | Stable object identities, structured context, and explicit input/output contracts for memory, retrieval, and tool execution |
| AI governance | Separation of source facts from derived results, with lineage, run manifests, explicit authorization gates, and structured errors |
| Agent harnesses | Version-pinned orchestration across Data, Action, embedding, and return nodes, with constraints on paths, configuration, models, and database writes |

Baigong is therefore best understood as a **governable agent execution and data-harness pattern**.
Models and upper-layer agents may change; provenance, identity, boundaries, execution evidence,
and rollback entry points should not disappear with them.

## Provenance, retrospective tracing, and rollback

Baigong is not primarily about writing the same data into several databases. Its central purpose
is to make every derived result traceable to its origin and explainable through its processing path:

```text
Synthetic source
  ↓ source path, content hash, stable identity
Data discovery and admission
  ↓ handoff / lineage / inventory
Action governance and identity anchoring
  ↓ concept, instance, character offsets, and run identity
DuckDB / BGE-M3 / Chroma derivation
  ↓ child completion manifests and write status
Action Return
  ↓ cycle_id, top-level completion manifest, inspectable result
Next Data cycle or upper-layer agent
```

- **Source provenance:** source inputs remain distinct from derived results; `asset_id`, content hashes, source paths, and character offsets provide a route back to evidence.
- **Retrospective tracing:** `run_id`, `cycle_id`, Action lineage, child completion manifests, and the return manifest reconstruct an execution.
- **Version verification:** the main orchestrator pins child-script versions, while the release manifest records SHA-256 values for scripts, templates, and sample data.
- **Governed actions:** database writes, real-model execution, and optional Neo4j writes require explicit confirmation; dry-run exposes configuration and schema failures before execution.
- **Diagnosable failure:** structured JSON errors prevent partial failure from being reported as complete success and prevent silent model or database substitution.
- **Rollback readiness:** test writes are contained under `temp/`; `v0005` remains a code rollback entry while `v0006` is the current validated entry.

“Rollback” does not claim automatic reversal of every external database transaction. It means that
code versions, test data, execution evidence, and write boundaries remain clear enough for a failed
run to be located, isolated, replayed, or safely withdrawn by an operator.

## Current status

- Main entry point: `scripts/orchestration/action/data_action_chain_pipeline_v0006.py`
- Rollback entry point: `scripts/orchestration/action/data_action_chain_pipeline_v0005.py`
- Validated platform: Windows with Python 3.12.7
- Validated GPU: NVIDIA GeForce RTX 4060 Laptop GPU
- Validated PyTorch build: `2.9.0+cu126`
- Validated main path: SQLite, DuckDB, BGE-M3, and Chroma
- Neo4j: code and templates are retained as an optional, unvalidated branch

The latest isolated acceptance run covered preflight dry-run, a full mock smoke test, and a small
real BGE-M3 run. The real-model run generated six 1024-dimensional vectors and ended with
`ready_for_data_discovery`. The sanitized acceptance summary is available at
[`docs/acceptance/acceptance_report_v0001.json`](docs/acceptance/acceptance_report_v0001.json).

## Quick installation

### Graphical installer (recommended)

On Windows, double-click:

```text
launch_installer.cmd
```

The installer intentionally stays simple: one Start button, a current-stage label, an active
progress bar, and a log view. It serially:

1. creates or reuses the local `.venv`;
2. detects NVIDIA hardware and chooses the CUDA or CPU path;
3. installs Python dependencies;
4. copies missing active configuration files from `*.example.yml` templates;
5. verifies PyTorch, CUDA, and the main runtime libraries.

The CUDA PyTorch wheel is approximately 2.6 GB. Do not run two pip processes against the same
virtual environment. Full installer logs are saved under `logs/`.

### Command-line installation

```powershell
Set-Location '<path-to-extracted-Baigong>'
python -m venv .venv
& '.\.venv\Scripts\Activate.ps1'
```

NVIDIA GPU path:

```powershell
python -m pip install --no-deps --timeout 600 --retries 10 `
  torch==2.9.0 --index-url https://download.pytorch.org/whl/cu126
python -m pip install --timeout 300 --retries 5 `
  -r .\requirements\requirements-data-action-demo.txt
```

CPU path:

```powershell
python -m pip install --no-deps --timeout 600 --retries 10 `
  torch==2.9.0 --index-url https://download.pytorch.org/whl/cpu
python -m pip install --timeout 300 --retries 5 `
  -r .\requirements\requirements-data-action-demo.txt
```

Verify the environment:

```powershell
python -c "import torch, yaml, duckdb, chromadb, sentence_transformers; print(torch.__version__); print(torch.version.cuda); print(torch.cuda.is_available())"
```

The requirement files pin the validated direct dependency versions, but do not hash every
transitive wheel. They therefore do not claim byte-for-byte reproducibility across package indexes;
see `requirements/README.md`.

## Configuration

The release stores configuration templates as `*.example.yml`. The graphical installer copies
missing active files without overwriting local files. For a command-line setup, run:

```powershell
Get-ChildItem .\config -Recurse -Filter *.example.yml | ForEach-Object {
    $destination = $_.FullName -replace '\.example\.yml$', '.yml'
    if (-not (Test-Path -LiteralPath $destination)) {
        Copy-Item -LiteralPath $_.FullName -Destination $destination
    }
}
```

The default embedding configuration uses `BAAI/bge-m3` on CUDA. CPU users must change
`backend.device` to `cpu` in the local active file
`config/embedding_generator_config_v0002.yml`. Model weights are not bundled; local execution
requires an existing Hugging Face cache or network access on first use.

## Preflight and execution

Use the root-level isolated test entry point. It copies the synthetic sample and keeps databases,
vectors, logs, and run evidence under a new `temp/<batch>/` directory; existing batches are never
overwritten:

```powershell
.\run_demo_test.ps1 -Mode DryRun
.\run_demo_test.ps1 -Mode Mock
```

To inspect the complete command without creating files:

```powershell
.\run_demo_test.ps1 -Mode Mock -PlanOnly
```

List every orchestration option with:

```powershell
python .\scripts\orchestration\action\data_action_chain_pipeline_v0006.py --help
```

`--dry-run` parses active configurations and checks paths and the Action business schema without
executing the pipeline:

- Valid four-table database: `preflight.ready=true`
- Database not created yet: `action_data_db.status=missing`
- Ordinary file or invalid database: structured JSON error

For the first isolated mock run, place every writable path under `temp/<batch>/` and explicitly use:

```text
--test-mode --mock-api --init-action-db
--confirm-database-write --confirm-execution
```

A real BGE-M3 run additionally requires `--confirm-model-api`. Never use real user data or a
production database for Demo tests.

## Database policy

The release contains an empty `sql/` directory with documentation only:

- `action_data.db` is the four-table Demo business database. Tests may create it only under `temp/`.
- `action.db` is the thirteen-table development registry. It is not Demo runtime data and is not bundled.
- Never pass `AGENTS.md`, an empty file, or the thirteen-table `action.db` as `--action-db`.

See `sql/README.md` for details.

## Directory layout

```text
Baigong/
├─ launch_installer.cmd        graphical installer entry
├─ run_demo_test.ps1           isolated dry-run / mock test entry
├─ assets/                     release visual assets such as the logo
├─ installer/                  installer implementation
├─ scripts/                    Data and Action scripts and orchestrators
├─ config/                     templates and local active configuration
├─ data/data_raw/test/         the single synthetic sample
├─ requirements/               main and optional Neo4j dependencies
├─ docs/acceptance/             sanitized acceptance summary
├─ sql/                        empty database location and policy
├─ temp/                       isolated test output; not released
├─ DEPENDENCY_MANIFEST.json    machine-readable dependency and SHA-256 manifest
└─ FILE_LIST.md                human-readable release file list
```

## Data and safety boundaries

- Treat the supplied synthetic sample as read-only; derived results must not overwrite it.
- Vector hits and analytical outputs are derived leads, not source facts; use stable identities to return to SQLite evidence.
- The same object should retain an inspectable identity mapping across SQLite, DuckDB, and Chroma.
- Test databases, vectors, logs, and run artifacts belong under `temp/` or `logs/`.
- Do not distribute real data, databases, model weights, tokens, passwords, or private local configuration.
- Do not copy a virtual environment from another project as a deployment method.
- Do not install or invoke Neo4j unless that optional branch is deliberately selected and validated.

## Release integrity

- `DEPENDENCY_MANIFEST.json` records SHA-256 values for scripts, templates, sample data, and release support files.
- `FILE_LIST.md` is the human-readable release inventory.
- The logo retains its original C2PA content credential, including signed generation-tool provenance. It is not executable code and does not identify the project owner.
- The sanitized acceptance summary excludes original run manifests containing machine-local absolute paths and explicitly records that limitation.
- `v0006` is the current entry point; `v0005` remains available for file-level rollback.
- Each real run should also retain the top-level completion manifest, child manifests, Action lineage, and Action Return manifest; release integrity and run-evidence integrity are checked separately.
- Runtime output, active configuration, and virtual environments are excluded by `.gitignore`.

If installation fails, preserve the complete installer log or PowerShell output. A report that only
says “stuck” is insufficient; include the progress line, network throughput, Python process state,
and final exit code.

This project is licensed under the **Apache License 2.0**; the complete terms are provided in the
GitHub repository's root `LICENSE` file. Third-party dependencies, models, and services remain
subject to their own licenses and terms. The project may continue to evolve when new requirements,
real defects, or test evidence provide a defined scope. No further exploratory optimization is
being added after this risk-closure pass.
