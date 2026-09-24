# Baigong 百工

[中文](README.md) | [English](README_EN.md)

<p align="center">
  <img src="assets/baigong-logo.png" alt="Baigong logo: a black ant with a circuit motif" width="260">
</p>

**Turn past conversations into knowledge you can use again.**

Baigong turns supported chat exports into locally stored, traceable data that AI systems can retrieve and use—so past conversations can inform your next idea.

**Current release: v0.3.0**, with `data_action_chain_pipeline_v0018.py` as the core entry point. Development continues in [David7583/baigong](https://github.com/David7583/baigong); `v0018` identifies the script version.

Download the `baigong-v0.3.0.zip` Release asset for the explicitly verified script set. GitHub-generated Source code archives also retain historical scripts and installers. Use the [v0.2.0 tag](https://github.com/David7583/baigong/tree/v0.2.0) to reproduce the older entry points; follow the commands below for this release.

## What this version does

- Accepts supported chat JSON structures, extracts text, timestamps and message identities, and preserves source mappings.
- Performs structural processing, SQLite identity anchoring, DuckDB analytical materialization, local BGE-M3 dense embedding generation, and Chroma storage.
- Supports incremental processing and duplicate-content checks; records execution evidence and rolls back the failure scenarios covered by acceptance tests.
- Provides a data foundation for semantic retrieval by higher-level AI applications. This release is a script pipeline.

This version does not include a ready-to-use chat interface, automatic experience summaries, a digital twin, or life simulations. Semantic retrieval has been tested on synthetic samples; this is not a quality guarantee for every personal dataset.

## Supported chat structures

The entry point detects JSON structure and validates the full contract. A filename or platform name alone does not establish compatibility.

| Input structure | Key identifying fields | Current verification scope |
|---|---|---|
| Chat graph export JSON, in a ChatGPT-style structure | Top-level array; `conversation_id`, `mapping`, `current_node`; message fields including `author.role` and `content.content_type` | Synthetic samples covered by both mock and real BGE-M3 end-to-end acceptance tests |
| UUID-based linear message export JSON | Top-level array; `uuid`, `chat_messages`; messages contain `sender` and `text` | Detector and adapter included; this format has not received a separate end-to-end acceptance run in this round |
| Baigong canonical conversation ingress JSON | Top-level object; `schema_version` is `canonical_conversation_ingress_v0001`, with `dataset_id`, `source_envelope_id`, and `conversations` | Canonical contract and passthrough path provided; adapted source formats enter this contract |

These fields illustrate structure, not a complete minimal sample. Node relationships, IDs, metadata and content types must also satisfy the contract. Missing fields, unknown structures and different platform export versions are not universally supported. Preserving image, audio or attachment information does not imply understanding their contents. Direct import of arbitrary PDF, Word, image, audio or WeChat exports is not claimed.

**Support for more chat export formats and file types is planned. Sanitized format examples and feature requests are welcome.**

## Future directions

**Research direction · Personal RSI:** Exploring how personal AI can build on long-term memory to improve its performance through feedback and evaluation.

## Project and citation

Development continues in the original GitHub repository; no separate Baigong 2.0 repository is being introduced.

- [All-versions DOI: 10.5281/zenodo.22093153](https://doi.org/10.5281/zenodo.22093153)
- [Archived v0.2.0 DOI: 10.5281/zenodo.22093154](https://doi.org/10.5281/zenodo.22093154)

The v0.3.0 version DOI will be available in its Zenodo record once archiving completes. Cite a specific release using its own DOI; the v0.2.0 DOI must not be presented as the v0.3.0 DOI.

This package contains v0018 and its dependencies. Acceptance tests use synthetic data and mock embeddings by default. Real offline inference requires an explicit local BGE-M3 path. Model weights, personal data, databases and credentials are not included.

## Verified platform and installation

Windows x64, Python 3.12, CPU inference. Installation requires network access; acceptance runs disable network access. Create a fresh virtual environment rather than copying an existing one:

```powershell
python -m venv .venv
& .\.venv\Scripts\python.exe -m pip install -r requirements/windows-py312.lock.txt
& .\.venv\Scripts\python.exe -m pip check
& .\.venv\Scripts\python.exe -B tests/release_preflight_v0001.py --run-id m01 --mode all
```

Obtain the model from [BAAI/bge-m3](https://huggingface.co/BAAI/bge-m3), pinned to revision `5617a9f61b028005a4858fdac845db406aefb181`. An existing local model directory can be shared across tasks. Keep model weights out of Git. This package uses dense embeddings only; sparse and ColBERT modes are not covered.

```powershell
& .\.venv\Scripts\python.exe -B tests/release_preflight_v0001.py --run-id r01 --mode all --model-path '<local-BGE-M3-directory>'
```

Use a short, unique run ID for each run while keeping the same task directory. On Windows, choose a short extraction path: the structural sub-pipeline rejects planned output paths longer than 240 characters, and an extended-length prefix does not bypass this check. Evidence is written to `evidence`; logs and synthetic databases go to `temp`. The test entry handles the Windows extended paths verified in this round. Do not replace the fixtures with unreviewed personal data. Memory requirements depend on the model and text length; CPU verification does not establish GPU support.

## Scope and entry points

Main pipeline: `scripts/orchestration/action/data_action_chain_pipeline_v0018.py`; use `--help` for arguments. Initialize the Action database with `scripts/action/tools/init_action_data_sql_schema_v0001.py` first. The acceptance tool demonstrates explicit arguments and isolated directory settings; it does not initialize production databases.

The package retains the existing local embedding generator. Real-model regression does not establish integration with a host system's AI controller. Applications integrating it must still satisfy their host's permission and controller requirements. The optional Neo4j branch, HTTP model services and other platforms are outside this acceptance scope.

## Integrity and licenses

`PACKAGE_MANIFEST.json` records SHA-256 hashes for every package file except itself. `provenance/source_manifest.json` records candidate provenance and hashes. The project uses Apache-2.0; see `LICENSE` and `NOTICE`. BGE-M3 uses MIT and is downloaded separately. Python dependency metadata is listed in `THIRD_PARTY_NOTICES.json`; dependencies are not bundled in this source package.

This release retains seven Windows SQLite URI compatibility fixes across six scripts from preparation. They normalize extended-length path prefixes without changing business fields or public entry-point interfaces. UNC shares and all special-path combinations have not been validated.

See [CHANGELOG.md](CHANGELOG.md) for release changes and [RELEASE_ACCEPTANCE.json](RELEASE_ACCEPTANCE.json) for the acceptance summary.
