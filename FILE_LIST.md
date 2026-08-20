# 百工发布文件清单

生成依据：去敏验收摘要，以及从总编排入口开始的递归源码引用。带本机绝对路径的原始运行清单不随发布包提供。

- 入口：`scripts/orchestration/action/data_action_chain_pipeline_v0006.py`
- 脚本：82 个
- 配置模板：29 个
- 人工示例数据：1 个

## 脚本

- `scripts\action\adapters\select_semantic_text_units_v0001.py` — runtime_manifest_or_entry
- `scripts\action\anchor\check_sql_integrity_v0003.py` — runtime_manifest_or_entry
- `scripts\action\anchor\ingest_attribute_units_v0003.py` — runtime_manifest_or_entry
- `scripts\action\anchor\ingest_concept_units_v0002.py` — runtime_manifest_or_entry
- `scripts\action\anchor\ingest_instance_units_v0002.py` — recursive_source_reference
- `scripts\action\anchor\ingest_instance_units_v0003.py` — runtime_manifest_or_entry
- `scripts\action\anchor\replay_ingest_run_v0004.py` — runtime_manifest_or_entry
- `scripts\action\anchor\sql_writer_v0004.py` — recursive_source_reference
- `scripts\action\anchor\validate_ingest_payload_v0003.py` — runtime_manifest_or_entry
- `scripts\action\anchor\verify_data_anchor_v0002.py` — runtime_manifest_or_entry
- `scripts\action\archive\anchor\validate_ingest_payload_v0001.py` — recursive_source_reference
- `scripts\action\archive\anchor\verify_data_anchor_v0001.py` — recursive_source_reference
- `scripts\action\derivation\enrich_retrieval_with_analytics_v0001.py` — runtime_manifest_or_entry
- `scripts\action\derivation\init_graph_schema_v0002.py` — recursive_source_reference
- `scripts\action\derivation\query_duckdb_direct_v0001.py` — runtime_manifest_or_entry
- `scripts\action\derivation\sync_hierarchy_to_graph_v0002.py` — recursive_source_reference
- `scripts\action\derivation\sync_sql_to_duckdb_v0004.py` — runtime_manifest_or_entry
- `scripts\action\derivation\sync_to_graph_v0002.py` — recursive_source_reference
- `scripts\action\infrastructures\register_action_lineage_v0002.py` — runtime_manifest_or_entry
- `scripts\action\pipelines\aggregate_graph_statistics_v0002.py` — runtime_manifest_or_entry
- `scripts\action\pipelines\analyze_graph_statistics_v0002.py` — runtime_manifest_or_entry
- `scripts\action\pipelines\analyze_structural_adjacency_v0004.py` — runtime_manifest_or_entry
- `scripts\action\pipelines\analyze_structural_cooccurrence_v0004.py` — runtime_manifest_or_entry
- `scripts\action\pipelines\analyze_structural_hierarchy_v0004.py` — runtime_manifest_or_entry
- `scripts\action\pipelines\analyze_structural_statistics_v0003.py` — runtime_manifest_or_entry
- `scripts\action\pipelines\build_unit_adjacency_graph_v0003.py` — runtime_manifest_or_entry
- `scripts\action\pipelines\build_unit_cooccurrence_graph_v0003.py` — runtime_manifest_or_entry
- `scripts\action\pipelines\build_unit_hierarchy_graph_v0002.py` — recursive_source_reference
- `scripts\action\pipelines\decide_parse_eligibility_v0003.py` — runtime_manifest_or_entry
- `scripts\action\pipelines\decide_unit_prominence_v0004.py` — runtime_manifest_or_entry
- `scripts\action\pipelines\extract_frequent_units_v0003.py` — runtime_manifest_or_entry
- `scripts\action\pipelines\filter_structural_noise_v0003.py` — runtime_manifest_or_entry
- `scripts\action\pipelines\language_parse_lite_v0003.py` — runtime_manifest_or_entry
- `scripts\action\pipelines\normalize_text_units_v0002.py` — runtime_manifest_or_entry
- `scripts\action\pipelines\normalize_unit_variants_v0003.py` — runtime_manifest_or_entry
- `scripts\action\pipelines\profile_string_values_v0004.py` — runtime_manifest_or_entry
- `scripts\action\pipelines\profile_unit_structure_v0003.py` — runtime_manifest_or_entry
- `scripts\action\pipelines\register_structural_units_v0003.py` — runtime_manifest_or_entry
- `scripts\action\pipelines\validate_unit_boundaries_v0003.py` — runtime_manifest_or_entry
- `scripts\action\tools\init_action_data_duckdb_schema_v0001.py` — runtime_manifest_or_entry
- `scripts\action\tools\init_action_data_sql_schema_v0001.py` — recursive_source_reference
- `scripts\action\vector\embedding_contract_validator_v0002.py` — runtime_manifest_or_entry
- `scripts\action\vector\embedding_generator_v0004.py` — runtime_manifest_or_entry
- `scripts\action\vector\embedding_lifecycle_guard_v0001.py` — runtime_manifest_or_entry
- `scripts\action\vector\embedding_writer_v0001.py` — runtime_manifest_or_entry
- `scripts\action\vector\vector_integrity_check_v0001.py` — recursive_source_reference
- `scripts\action\vector\vector_integrity_check_v0002.py` — runtime_manifest_or_entry
- `scripts\analyze_json_structure_v0002.py` — runtime_manifest_or_entry
- `scripts\anchor\ingest_data_text_units_v0001.py` — runtime_manifest_or_entry
- `scripts\anchor\validate_ingest_payload_v0001.py` — recursive_source_reference
- `scripts\anchor\verify_data_anchor_v0001.py` — recursive_source_reference
- `scripts\coarse_slice_conversations_v0001.py` — runtime_manifest_or_entry
- `scripts\discover_data_v0001.py` — runtime_manifest_or_entry
- `scripts\identify_file_type_v0002.py` — runtime_manifest_or_entry
- `scripts\import_conversation_slices_to_processed_v0001.py` — runtime_manifest_or_entry
- `scripts\inventory_data_snapshot_v0001.py` — runtime_manifest_or_entry
- `scripts\orchestration\action\action_anchor_persistence_pipeline_v0001.py` — recursive_source_reference
- `scripts\orchestration\action\action_anchor_persistence_pipeline_v0002.py` — recursive_source_reference
- `scripts\orchestration\action\action_anchor_persistence_pipeline_v0003.py` — runtime_manifest_or_entry
- `scripts\orchestration\action\action_derivation_materialization_pipeline_v0001.py` — recursive_source_reference
- `scripts\orchestration\action\action_derivation_materialization_pipeline_v0002.py` — recursive_source_reference
- `scripts\orchestration\action\action_derivation_materialization_pipeline_v0003.py` — runtime_manifest_or_entry
- `scripts\orchestration\action\data_action_chain_pipeline_v0001.py` — recursive_source_reference
- `scripts\orchestration\action\data_action_chain_pipeline_v0002.py` — recursive_source_reference
- `scripts\orchestration\action\data_action_chain_pipeline_v0004.py` — recursive_source_reference
- `scripts\orchestration\action\data_action_chain_pipeline_v0005.py` — runtime_manifest_or_entry
- `scripts\orchestration\action\data_action_chain_pipeline_v0006.py` — runtime_manifest_or_entry
- `scripts\orchestration\action\structural_unit_governance_graph_pipeline_v0001.py` — recursive_source_reference
- `scripts\orchestration\action\structural_unit_governance_graph_pipeline_v0002.py` — runtime_manifest_or_entry
- `scripts\orchestration\action\vector_embedding_pipeline_v0001.py` — recursive_source_reference
- `scripts\orchestration\action\vector_embedding_pipeline_v0002.py` — recursive_source_reference
- `scripts\orchestration\action\vector_embedding_pipeline_v0003.py` — runtime_manifest_or_entry
- `scripts\orchestration\data\data_admission_lineage_pipeline_v0001.py` — recursive_source_reference
- `scripts\orchestration\data\data_admission_lineage_pipeline_v0002.py` — runtime_manifest_or_entry
- `scripts\orchestration\data\data_discovery_parse_preparation_pipeline_v0001.py` — runtime_manifest_or_entry
- `scripts\prepare_json_parse_task_v0001.py` — runtime_manifest_or_entry
- `scripts\register_understanding_lineage_v0001.py` — recursive_source_reference
- `scripts\snapshot_path_resolver_v0001.py` — runtime_manifest_or_entry
- `scripts\tools\init_data_schema_v0001.py` — runtime_manifest_or_entry
- `scripts\vector\embedding_lifecycle_guard_v0001.py` — recursive_source_reference
- `scripts\vector\embedding_writer_v0001.py` — recursive_source_reference
- `scripts\vector\vector_integrity_check_v0001.py` — recursive_source_reference

## 配置模板

- `config\action\config\action_data_neo4j_connection_config_v0001.example.yml`（源配置：`config\action\config\action_data_neo4j_connection_config_v0001.yml`）
- `config\action\config\action_data_neo4j_schema_config_v0001.example.yml`（源配置：`config\action\config\action_data_neo4j_schema_config_v0001.yml`）
- `config\action\config\decide_unit_prominence_policy_v0001.example.yml`（源配置：`config\action\config\decide_unit_prominence_policy_v0001.yml`）
- `config\action\config\duckdb_schema_config_v0001.example.yml`（源配置：`config\action\config\duckdb_schema_config_v0001.yml`）
- `config\action\config\embedding_generator_config_v0002.example.yml`（源配置：`config\action\config\embedding_generator_config_v0002.yml`）
- `config\action\config\filter_structural_noise_v0001.example.yml`（源配置：`config\action\config\filter_structural_noise_v0001.yml`）
- `config\action\config\ingest_attribute_units_config_v0001.example.yml`（源配置：`config\action\config\ingest_attribute_units_config_v0001.yml`）
- `config\action\config\ingest_concept_units_config_v0001.example.yml`（源配置：`config\action\config\ingest_concept_units_config_v0001.yml`）
- `config\action\config\ingest_instance_units_config_v0001.example.yml`（源配置：`config\action\config\ingest_instance_units_config_v0001.yml`）
- `config\action\config\ingest_instance_units_semantic_config_v0001.example.yml`（源配置：`config\action\config\ingest_instance_units_semantic_config_v0001.yml`）
- `config\action\config\language_parse_lite_policy_v0002.example.yml`（源配置：`config\action\config\language_parse_lite_policy_v0002.yml`）
- `config\action\config\normalize_unit_variants_policy_v0001.example.yml`（源配置：`config\action\config\normalize_unit_variants_policy_v0001.yml`）
- `config\action\config\parse_eligibility_policy_v0002.example.yml`（源配置：`config\action\config\parse_eligibility_policy_v0002.yml`）
- `config\action\config\sql_writer_config_v0001.example.yml`（源配置：`config\action\config\sql_writer_config_v0001.yml`）
- `config\action\config\validate_unit_boundaries_v0001.example.yml`（源配置：`config\action\config\validate_unit_boundaries_v0001.yml`）
- `config\action\init_schema\action_data_duckdb_schema_config_v0001.example.yml`（源配置：`config\action\init_schema\action_data_duckdb_schema_config_v0001.yml`）
- `config\action\init_schema\action_data_sql_schema_config_v0001.example.yml`（源配置：`config\action\init_schema\action_data_sql_schema_config_v0001.yml`）
- `config\decide_unit_prominence_policy_v0001.example.yml`（源配置：`config\decide_unit_prominence_policy_v0001.yml`）
- `config\duckdb_schema_config_v0001.example.yml`（源配置：`config\duckdb_schema_config_v0001.yml`）
- `config\embedding_generator_config_v0002.example.yml`（源配置：`config\embedding_generator_config_v0002.yml`）
- `config\filter_structural_noise_v0001.example.yml`（源配置：`config\filter_structural_noise_v0001.yml`）
- `config\ingest_attribute_units_config_v0001.example.yml`（源配置：`config\ingest_attribute_units_config_v0001.yml`）
- `config\ingest_concept_units_config_v0001.example.yml`（源配置：`config\ingest_concept_units_config_v0001.yml`）
- `config\ingest_instance_units_config_v0001.example.yml`（源配置：`config\ingest_instance_units_config_v0001.yml`）
- `config\language_parse_lite_policy_v0002.example.yml`（源配置：`config\language_parse_lite_policy_v0002.yml`）
- `config\normalize_unit_variants_policy_v0001.example.yml`（源配置：`config\normalize_unit_variants_policy_v0001.yml`）
- `config\parse_eligibility_policy_v0002.example.yml`（源配置：`config\parse_eligibility_policy_v0002.yml`）
- `config\sql_writer_config_v0001.example.yml`（源配置：`config\sql_writer_config_v0001.yml`）
- `config\validate_unit_boundaries_v0001.example.yml`（源配置：`config\validate_unit_boundaries_v0001.yml`）

## 示例数据

- `data\data_raw\test\reliable_pipeline_fake_conversations_v0001.json`

## 发布辅助文件

- `README.md`
- `README_EN.md`
- `assets/baigong-logo.png`
- `.gitignore`
- `AGENTS.md`
- `actioning/README.md`
- `launch_installer.cmd`
- `run_demo_test.ps1`
- `installer/installer_gui_v0001.py`
- `sql/README.md`
- `docs/acceptance/acceptance_report_v0001.json`
- `FILE_LIST.md`
- `LICENSE`
- `NOTICE`
- `requirements/requirements-data-action-demo.txt`
- `requirements/requirements-neo4j-optional.txt`
- `requirements/README.md`

## 明确排除

- `AGENTS_INTERNAL_DO_NOT_RELEASE.md`（内部维护规则，人工发布时不要带入）
- `LICENSE_INTERNAL_DO_NOT_RELEASE.md`（旧许可声明，已由 GitHub 生成的 Apache `LICENSE` 取代）
- `.venv`
- `.venv_broken_backup_*`
- `temp`
- `staging`
- `logs`
- `real databases`
- `database backups`
- `real data`
- `model weights`
- `API keys`
- `call logs`
