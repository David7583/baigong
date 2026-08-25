# ============================================================
# 文件名: run_demo_test.ps1
# 中文名: 百工公开 Demo 隔离测试入口
# 版本号: v0002
#
# 主层级: action
# 层级: release / demo_test / stable_entry
# 脚本定位: 在公开发布包根目录构造隔离批次并调用 v0009 总入口
#
# 职责说明:
# - 为人工样例生成 dry-run 或 mock 的完整相对路径参数
# - 将全部测试数据库、日志、派生和回流产物限制在 temp 批次目录
#
# 本脚本做什么:
# - 复制人工构造的 uuid/chat_messages 时间样例并生成隔离 SQL 配置
# - 支持 PlanOnly、DryRun 和 Mock 三种公开验收方式
#
# 本脚本不做什么:
# - 不读取真实用户数据，不写正式数据库，不调用真实模型
# - 不覆盖既有批次，不自动安装依赖或修改 Git 状态
#
# 制度边界声明:
# - Mock 模式显式确认隔离写入且固定 --mock-api
# - 批次目录已存在时立即停止，失败不得伪装为完整成功
#
# 可更新: True
# ============================================================

# ============================================================
# ALIAS_META
# ============================================================
# alias: run_demo_test
# family: run_demo_test
# role: public_release_isolated_test_entry
# version: v0002
# status: active
# entry_point: run_demo_test.ps1
# input:
#   - artificial uuid/chat_messages JSON sample
# output:
#   - isolated temp batch and structured v0009 result
# depends_on:
#   - scripts/orchestration/action/data_action_chain_pipeline_v0009.py
# used_by:
#   - public Baigong release operator
# ============================================================

[CmdletBinding()]
param(
    [ValidateSet('DryRun', 'Mock')]
    [string]$Mode = 'DryRun',

    [ValidatePattern('^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$')]
    [string]$BatchName = ('r' + [Convert]::ToString(
        ([DateTimeOffset]::UtcNow.ToUnixTimeSeconds() % 65536), 16
    ).PadLeft(4, '0')),

    [string]$PythonPath = '',

    [switch]$PlanOnly
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = $PSScriptRoot
$Python = if ([string]::IsNullOrWhiteSpace($PythonPath)) {
    Join-Path $ProjectRoot '.venv\Scripts\python.exe'
} else {
    $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($PythonPath)
}
$Entry = Join-Path $ProjectRoot 'scripts\orchestration\action\data_action_chain_pipeline_v0009.py'
$BatchRelative = "temp/$BatchName"
$BatchRoot = Join-Path $ProjectRoot "temp\$BatchName"
$SampleSource = Join-Path $ProjectRoot 'data\data_raw\test\adapter_timestamp_fake_conversations_v0002.json'
$SampleDestination = Join-Path $BatchRoot 'data\data_raw\test\adapter_timestamp_fake_conversations_v0002.json'
$SqlConfigRelative = "$BatchRelative/sql_writer_config.yml"
$SqlConfig = Join-Path $BatchRoot 'sql_writer_config.yml'
$PathBudgetProbe = Join-Path $BatchRoot (
    "outputs\data_action_chain_pipeline\v0009\$BatchName\canonical_data\w" +
    "\data_discovery_parse_preparation_pipeline\v0002\${BatchName}_d\json" +
    "\task_${BatchName}_d\data_raw\ingress\_analysis" +
    "\canonical_content_structure_report.json.tmp"
)

if ($PathBudgetProbe.Length -ge 260) {
    throw (
        "The projected v0009 evidence path is $($PathBudgetProbe.Length) characters. " +
        'Extract or clone Baigong to a shorter directory and/or use a shorter -BatchName.'
    )
}

$Arguments = @(
    $Entry,
    '--project-root', $ProjectRoot,
    '--data-root', "$BatchRelative/data",
    '--target', 'data_raw/test/adapter_timestamp_fake_conversations_v0002.json',
    '--data-intermediate-root', 'demo/intermediate',
    '--data-workspace-root', 'demo/workspace',
    '--admission-target-root', "$BatchRelative/admitted",
    '--registry-dir', "$BatchRelative/lineage",
    '--asset-version', 'v0001',
    '--data-db', "$BatchRelative/data.db",
    '--data-table', 'data_text_units',
    '--action-db', "$BatchRelative/action_data.db",
    '--sql-writer-config', $SqlConfigRelative,
    '--output-root', "$BatchRelative/outputs",
    '--structural-business-root', "$BatchRelative/structural",
    '--anchor-business-root', "$BatchRelative/anchor",
    '--derivation-business-root', "$BatchRelative/derivation",
    '--derivation-target', 'duckdb',
    '--duckdb-path', "$BatchRelative/action.duckdb",
    '--duckdb-config', 'config/action/init_schema/action_data_duckdb_schema_config_v0001.yml',
    '--vector-target', 'both',
    '--vector-pipeline-root', "$BatchRelative/vector",
    '--chromadb-path', "$BatchRelative/chroma",
    '--vector-state-index', "$BatchRelative/state/active_index.jsonl",
    '--vector-replaced-archive', "$BatchRelative/replaced",
    '--embed-config', 'config/embedding_generator_config_v0002.yml',
    '--return-root', "$BatchRelative/returns",
    '--run-id', $BatchName,
    '--test-mode',
    '--mock-api',
    '--semantic-path', '/conversations/*/nodes/*/message/content/parts/*/text',
    '--semantic-limit', '5'
)

if ($Mode -eq 'DryRun') {
    $Arguments += '--dry-run'
} else {
    $Arguments += @('--init-action-db', '--confirm-database-write', '--confirm-execution')
}

if ($PlanOnly) {
    Write-Host ('Planned command: ' + $Python + ' ' + ($Arguments -join ' '))
    exit 0
}

if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw 'Missing .venv\Scripts\python.exe. Run launch_installer.cmd or follow README first.'
}
if (-not (Test-Path -LiteralPath $SampleSource -PathType Leaf)) {
    throw "Missing synthetic sample: $SampleSource"
}
if (Test-Path -LiteralPath $BatchRoot) {
    throw "Batch directory already exists; refusing to overwrite: $BatchRoot"
}

Get-ChildItem (Join-Path $ProjectRoot 'config') -Recurse -Filter '*.example.yml' | ForEach-Object {
    $Destination = $_.FullName -replace '\.example\.yml$', '.yml'
    if (-not (Test-Path -LiteralPath $Destination)) {
        Copy-Item -LiteralPath $_.FullName -Destination $Destination
    }
}

New-Item -ItemType Directory -Path (Split-Path -Parent $SampleDestination) -Force | Out-Null
Copy-Item -LiteralPath $SampleSource -Destination $SampleDestination
New-Item -ItemType Directory -Path $BatchRoot -Force | Out-Null

$SqlTemplate = Join-Path $ProjectRoot 'config\sql_writer_config_v0001.example.yml'
$SqlText = Get-Content -LiteralPath $SqlTemplate -Raw
$Expected = 'path: sql/understand.db'
if (-not $SqlText.Contains($Expected)) {
    throw "Expected database path was not found; refusing an ambiguous replacement: $SqlTemplate"
}
$SqlText.Replace($Expected, "path: $BatchRelative/action_data.db") |
    Set-Content -LiteralPath $SqlConfig -Encoding UTF8

Write-Host "Mode: $Mode; isolated batch: $BatchRoot"
& $Python @Arguments
exit $LASTEXITCODE
